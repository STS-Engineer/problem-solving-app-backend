# app/services/conversation_service.py
"""
Conversational coaching service.

Flow per section
────────────────
1. User arrives on section  →  GET /conversation  →  bot sends opening question
2. User answers             →  POST /conversation  →  bot parses, asks follow-up
3. When enough data         →  bot embeds <extracted_fields>{...}</extracted_fields>
                               and sets state="ready_to_validate"
4. Frontend calls           →  POST /submit-section  (existing endpoint, unchanged)

File uploads
────────────
Files are uploaded separately via POST /steps/{step_id}/files (step_files router).
The conversation service receives a list of already-uploaded StepFile records
(their filenames) so the AI can reference them in its reasoning.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.models.step_conversation import StepConversation
from app.models.step_file import StepFile
from app.models.file import File as FileModel

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION OPENING MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

SECTION_OPENING: Dict[str, str] = {
    # ── D1 ────────────────────────────────────────────────────────────────────
    "team_members": (
        "👋 Let's build your **D1 — Team** together.\n\n"
        "Tell me who is on the 8D team. For each person give me their "
        "**name**, **department** (Production / Quality / Engineering / Maintenance / Logistics / Supplier Quality / Other) "
        "and **function** (Operator / Line Leader / Supervisor / Engineer / Team Leader / Project Manager / Other).\n\n"
        "You can list everyone at once or one by one — whichever is easier.\n\n"
        "Who is the **team leader**?"
    ),

    # ── D2 ────────────────────────────────────────────────────────────────────
    "five_w_2h": (
        "👋 Let's fill in the **5W2H analysis** for your problem description.\n\n"
        "Start by giving me a short description of the problem "
        "(object/process + defect observed). Then I'll guide you through the "
        "7 questions: What, Where, When, Who, Why, How, and How Many.\n\n"
        "What is the problem?"
    ),
    "deviation": (
        "📋 Now let's document the **deviation vs the standard**.\n\n"
        "Please tell me:\n"
        "1. What **standard or specification** applies here? (e.g. WI-WELD-02)\n"
        "2. What was the **expected situation**?\n"
        "3. What did you **actually observe**?\n"
        "4. Any **evidence documents** to reference? "
        "(You can also attach files using the 📎 button — I'll register them automatically)"
    ),
    "is_is_not": (
        "🔍 Let's build the **IS / IS NOT analysis**.\n\n"
        "For each factor below, tell me what IS affected and what is NOT:\n"
        "- **Product**: which product/part is affected vs not?\n"
        "- **Time**: when did it start / when did it not occur?\n"
        "- **Lot**: which lot numbers are affected vs not?\n"
        "- **Pattern**: is the defect on all units or a subset?\n\n"
        "You can answer all at once or one factor at a time."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# JSON EXTRACTION SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

EXTRACTION_SCHEMA: Dict[str, str] = {
    # ── D1 ────────────────────────────────────────────────────────────────────
    "team_members": """{
  "team_members": [
    {
      "name":       "<string — full name of the person>",
      "department": "<string — MUST be exactly one of: production | maintenance | engineering | quality | logistics | supplier_quality | other>",
      "function":   "<string — MUST be exactly one of: operator | line_leader | supervisor | engineer | team_leader | project_manager | other>"
    }
  ]
}

CRITICAL FIELD NAMING RULES — follow exactly or the form will break:
- Use "department" NOT "dept" or "department_name"
- Use "function" NOT "role" or "job_title" or "position"
- department values: production | maintenance | engineering | quality | logistics | supplier_quality | other
- function values: operator | line_leader | supervisor | engineer | team_leader | project_manager | other
- If you cannot map to an exact value above, use "other"
- Return ALL team members collected so far (full accumulated list)
- Minimum 2 members required before extracting
- At least one member must have function = "team_leader"
- NEVER invent data not provided by the user""",

    # ── D2 ────────────────────────────────────────────────────────────────────
    "five_w_2h": """{
  "problem_description": "<string — 1-2 sentence description>",
  "five_w_2h": {
    "what":     "<string — the defect>",
    "where":    "<string — location: site / line / process>",
    "when":     "<string — ISO date or descriptive period>",
    "who":      "<string — who detected it>",
    "why":      "<string — why it is a problem>",
    "how":      "<string — how it was detected>",
    "how_many": "<string — quantity with unit>"
  }
}
All fields including all 7 five_w_2h sub-fields are REQUIRED before you extract.""",

    "deviation": """{
  "standard_applicable": "<string — standard name/code>",
  "expected_situation":  "<string — what should have been>",
  "observed_situation":  "<string — what was actually found>",
  "evidence_documents":  "<string — filenames of attached evidence, comma-separated, or empty string>"
}
standard_applicable, expected_situation, observed_situation are REQUIRED.
For evidence_documents: use the filenames from any attached files the user mentioned.""",

    "is_is_not": """{
  "is_is_not_factors": [
    {"factor": "Product", "is_problem": "<string>", "is_not_problem": "<string>"},
    {"factor": "Time",    "is_problem": "<string>", "is_not_problem": "<string>"},
    {"factor": "Lot",     "is_problem": "<string>", "is_not_problem": "<string>"},
    {"factor": "Pattern", "is_problem": "<string>", "is_not_problem": "<string>"}
  ]
}
At least 3 of 4 factors must have both is_problem and is_not_problem filled.""",
}

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

CONV_SYSTEM_PROMPT = """You are a friendly but rigorous 8D Quality Coaching Assistant.
Your job is to help a quality engineer fill in a specific section of an 8D report
through a natural conversation.

RULES:
1. Ask ONE focused follow-up question at a time when information is missing.
2. Be concise: max 3 short sentences per bot message.
3. When the user provides information, acknowledge it briefly and ask for the next missing piece.
4. When you have ALL required fields, output a JSON block using EXACTLY this wrapper:
   <extracted_fields>
   { ... }
   </extracted_fields>
   Then add: "✅ I have everything I need. Click **Validate Section** to continue."
5. If an answer is too vague, ask for clarification with a concrete example.
6. Never request optional fields before all required fields are collected.
7. Reply in the same language the user writes in (French or English).
8. NEVER invent data — only use what the user explicitly provides.
9. NEVER use field names other than what the schema specifies. For team_members,
   ALWAYS use "department" and "function" — NEVER "role", "job_title", "position", etc.
10. For team_members: keep accumulating members across turns. Re-emit the FULL
    updated list every time you extract (not just the newly added member).
11. If the user mentions uploaded files (shown as "📎 Uploaded: filename.pdf"),
    reference those filenames in evidence_documents field.
"""


# ─────────────────────────────────────────────────────────────────────────────
# MERGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_FIELD_ALIASES: Dict[str, str] = {
    "role":            "function",
    "job_title":       "function",
    "position":        "function",
    "title":           "function",
    "dept":            "department",
    "department_name": "department",
}

_DEPARTMENT_MAP: Dict[str, str] = {
    "production":        "production",
    "manufacturing":     "production",
    "fabrication":       "production",
    "maintenance":       "maintenance",
    "engineering":       "engineering",
    "r&d":               "engineering",
    "quality":           "quality",
    "qa":                "quality",
    "qc":                "quality",
    "quality control":   "quality",
    "quality assurance": "quality",
    "logistics":         "logistics",
    "supply chain":      "logistics",
    "warehouse":         "logistics",
    "supplier quality":  "supplier_quality",
    "supplier_quality":  "supplier_quality",
    "sqe":               "supplier_quality",
}

_FUNCTION_MAP: Dict[str, str] = {
    "operator":        "operator",
    "line_leader":     "line_leader",
    "line leader":     "line_leader",
    "team leader":     "team_leader",
    "team_leader":     "team_leader",
    "teamleader":      "team_leader",
    "leader":          "team_leader",
    "supervisor":      "supervisor",
    "engineer":        "engineer",
    "engineering":     "engineer",
    "technician":      "engineer",
    "project manager": "project_manager",
    "project_manager": "project_manager",
    "manager":         "project_manager",
}

VALID_DEPARTMENTS = {
    "production", "maintenance", "engineering",
    "quality", "logistics", "supplier_quality", "other",
}
VALID_FUNCTIONS = {
    "operator", "line_leader", "supervisor", "engineer",
    "team_leader", "project_manager", "other",
}


def _normalise_member(raw: Dict) -> Dict:
    member: Dict[str, str] = {}
    for key, value in raw.items():
        canonical_key = _FIELD_ALIASES.get(key.lower(), key.lower())
        member[canonical_key] = str(value).strip() if value else ""

    dept_raw = member.get("department", "").lower().strip()
    member["department"] = _DEPARTMENT_MAP.get(
        dept_raw, dept_raw if dept_raw in VALID_DEPARTMENTS else "other"
    )

    func_raw = member.get("function", "").lower().strip()
    member["function"] = _FUNCTION_MAP.get(
        func_raw, func_raw if func_raw in VALID_FUNCTIONS else "other"
    )

    return {
        "name":       member.get("name", ""),
        "department": member.get("department", "other"),
        "function":   member.get("function", "other"),
    }


def _merge_extracted(current: Dict, extracted: Dict) -> Dict:
    merged = {**current}

    for key, value in extracted.items():
        if key == "team_members" and isinstance(value, list):
            merged["team_members"] = [
                _normalise_member(m) for m in value if isinstance(m, dict)
            ]

        elif key == "is_is_not_factors" and isinstance(value, list):
            existing = {f["factor"]: f for f in (merged.get("is_is_not_factors") or [])}
            for patch in value:
                factor = patch.get("factor")
                if factor:
                    existing[factor] = {**(existing.get(factor) or {}), **patch}
            merged["is_is_not_factors"] = list(existing.values())

        elif key == "five_w_2h" and isinstance(value, dict):
            merged["five_w_2h"] = {**(merged.get("five_w_2h") or {}), **value}

        else:
            merged[key] = value

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# SECTION COMPLETENESS CHECK
# Used to decide whether state should be "ready_to_validate" or "collecting".
# This mirrors the AI's own logic but is evaluated server-side for reliability.
# ─────────────────────────────────────────────────────────────────────────────

def _section_is_complete(section_key: str, extracted: Dict) -> bool:
    """
    Return True only when the extracted payload satisfies the minimum
    requirements for the section — i.e. the AI *should* have emitted
    the extracted_fields block only at this point, but we double-check.
    """
    if section_key == "team_members":
        members = extracted.get("team_members", [])
        if not isinstance(members, list) or len(members) < 2:
            return False
        has_leader = any(
            m.get("function") == "team_leader" for m in members if isinstance(m, dict)
        )
        return has_leader

    if section_key == "five_w_2h":
        w2h = extracted.get("five_w_2h", {})
        if not isinstance(w2h, dict):
            return False
        required = {"what", "where", "when", "who", "why", "how", "how_many"}
        return all(str(w2h.get(k, "")).strip() for k in required)

    if section_key == "deviation":
        return all(
            str(extracted.get(k, "")).strip()
            for k in ("standard_applicable", "expected_situation", "observed_situation")
        )

    if section_key == "is_is_not":
        factors = extracted.get("is_is_not_factors", [])
        if not isinstance(factors, list):
            return False
        filled = sum(
            1 for f in factors
            if isinstance(f, dict)
            and str(f.get("is_problem", "")).strip()
            and str(f.get("is_not_problem", "")).strip()
        )
        return filled >= 3

    # Unknown section — treat extraction as completion signal
    return bool(extracted)


# ─────────────────────────────────────────────────────────────────────────────
# SERVICE
# ─────────────────────────────────────────────────────────────────────────────

class ConversationService:
    def __init__(self, db: Session):
        self.db = db
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_or_start_conversation(
        self,
        step_id: int,
        section_key: str,
    ) -> Dict[str, Any]:
        """Return existing messages for a section, or start fresh."""
        messages = self._load_messages(step_id, section_key)
        if not messages:
            opening = SECTION_OPENING.get(
                section_key,
                "Let's fill in this section. Please provide the required information.",
            )
            self._persist_message(step_id, section_key, "assistant", opening, 0)
            messages = [self._msg_dict("assistant", opening, 0)]

        return {
            "step_id":     step_id,
            "section_key": section_key,
            "messages":    messages,
            "state":       self._infer_state(section_key, messages),
        }

    def send_message(
        self,
        step_id: int,
        section_key: str,
        user_message: str,
        complaint_context: Optional[Dict] = None,
        uploaded_file_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Process a user message: persist → call AI → persist reply → return.

        uploaded_file_names: list of original filenames that were just uploaded
        via the /files endpoint before this message was sent.  They are injected
        into the message text so the AI can reference them.
        """
        # Inject uploaded file info into user message text
        effective_message = user_message
        if uploaded_file_names:
            file_list = ", ".join(uploaded_file_names)
            effective_message = (
                f"{user_message}\n\n📎 Uploaded: {file_list}"
                if user_message.strip()
                else f"📎 Uploaded: {file_list}"
            )

        history  = self._load_messages(step_id, section_key)
        next_idx = len(history)

        self._persist_message(step_id, section_key, "user", effective_message, next_idx)
        history.append(self._msg_dict("user", effective_message, next_idx))
        next_idx += 1

        # Also inject existing step-file names into context so AI knows what's on record
        existing_files = self._get_step_file_names(step_id)
        bot_reply = self._call_ai(
            section_key, history, complaint_context, existing_files
        )

        extracted = self._parse_extracted_fields(bot_reply)
        meta      = {"extracted_fields": extracted} if extracted else None

        self._persist_message(step_id, section_key, "assistant", bot_reply, next_idx, meta)
        history.append(self._msg_dict("assistant", bot_reply, next_idx, meta))

        if extracted:
            # Merge uploaded file names into evidence_documents if deviation section
            if section_key == "deviation" and existing_files:
                current_evidence = extracted.get("evidence_documents", "")
                all_names = list(existing_files)
                if current_evidence:
                    # Avoid duplicates
                    for name in current_evidence.split(","):
                        name = name.strip()
                        if name and name not in all_names:
                            all_names.append(name)
                extracted["evidence_documents"] = ", ".join(all_names)

            self._update_step_data(step_id, extracted)

        is_complete = extracted and _section_is_complete(section_key, extracted)
        state = "ready_to_validate" if is_complete else (
            "collecting" if len(history) > 1 else "opening"
        )

        return {
            "step_id":          step_id,
            "section_key":      section_key,
            "bot_reply":        bot_reply,
            "extracted_fields": extracted,
            "state":            state,
            "messages":         history,
        }

    def reset_conversation(self, step_id: int, section_key: str) -> Dict[str, Any]:
        self.db.query(StepConversation).filter(
            StepConversation.report_step_id == step_id,
            StepConversation.section_key    == section_key,
        ).delete()
        self.db.commit()
        return self.get_or_start_conversation(step_id, section_key)

    def get_all_section_conversations(self, step_id: int) -> Dict[str, List[Dict]]:
        rows = (
            self.db.query(StepConversation)
            .filter(StepConversation.report_step_id == step_id)
            .order_by(StepConversation.section_key, StepConversation.message_index)
            .all()
        )
        result: Dict[str, List[Dict]] = {}
        for row in rows:
            result.setdefault(row.section_key, []).append(
                self._msg_dict(row.role, row.content, row.message_index, row.meta)
            )
        return result

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_step_file_names(self, step_id: int) -> List[str]:
        """Return original filenames of all files attached to this step."""
        rows = (
            self.db.query(FileModel.original_name)
            .join(StepFile, StepFile.file_id == FileModel.id)
            .filter(StepFile.report_step_id == step_id)
            .all()
        )
        return [r.original_name for r in rows]

    def _call_ai(
        self,
        section_key: str,
        history: List[Dict],
        complaint_context: Optional[Dict],
        existing_files: Optional[List[str]] = None,
    ) -> str:
        schema = EXTRACTION_SCHEMA.get(section_key, "")
        context_block = ""
        if complaint_context:
            context_block = (
                "\n\nCOMPLAINT CONTEXT (use to assess relevance):\n"
                f"  Problem : {complaint_context.get('complaint_name', '')}\n"
                f"  Desc    : {complaint_context.get('complaint_description', '')}\n"
                f"  Product : {complaint_context.get('product_line', '')}\n"
                f"  Plant   : {complaint_context.get('plant', '')}\n"
            )

        files_block = ""
        if existing_files:
            files_block = (
                "\n\nATTACHED EVIDENCE FILES (already uploaded to this step):\n"
                + "\n".join(f"  - {f}" for f in existing_files)
                + "\nReference these in evidence_documents when extracting.\n"
            )

        system = (
            CONV_SYSTEM_PROMPT
            + f"\n\nSECTION: {section_key}"
            + context_block
            + files_block
            + "\n\nWhen all required fields are collected, wrap JSON in "
              "<extracted_fields>...</extracted_fields>.\n"
            + f"Required JSON schema:\n{schema}"
        )

        openai_messages = [{"role": "system", "content": system}]
        for msg in history:
            openai_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=openai_messages,
                temperature=0.4,
                max_tokens=600,
                timeout=30,
            )
            return response.choices[0].message.content.strip()
        except OpenAIError as e:
            logger.error("OpenAI conversation error: %s", e)
            raise RuntimeError(f"AI service unavailable: {e}")

    def _load_messages(self, step_id: int, section_key: str) -> List[Dict]:
        rows = (
            self.db.query(StepConversation)
            .filter(
                StepConversation.report_step_id == step_id,
                StepConversation.section_key    == section_key,
            )
            .order_by(StepConversation.message_index)
            .all()
        )
        return [
            self._msg_dict(r.role, r.content, r.message_index, r.meta, r.created_at)
            for r in rows
        ]

    def _persist_message(
        self,
        step_id: int,
        section_key: str,
        role: str,
        content: str,
        message_index: int,
        meta: Optional[Dict] = None,
    ) -> None:
        self.db.add(StepConversation(
            report_step_id=step_id,
            section_key=section_key,
            role=role,
            content=content,
            message_index=message_index,
            meta=meta,
            created_at=datetime.now(timezone.utc),
        ))
        self.db.commit()

    @staticmethod
    def _msg_dict(
        role: str,
        content: str,
        message_index: int,
        meta: Optional[Dict]           = None,
        created_at: Optional[datetime] = None,
    ) -> Dict:
        return {
            "role":          role,
            "content":       content,
            "message_index": message_index,
            "meta":          meta,
            "created_at":    created_at.isoformat() if created_at else None,
        }

    @staticmethod
    def _parse_extracted_fields(text: str) -> Optional[Dict]:
        match = re.search(
            r"<extracted_fields>(.*?)</extracted_fields>", text, re.DOTALL
        )
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            logger.warning("Failed to parse extracted_fields JSON from AI reply")
            return None

    def _update_step_data(self, step_id: int, extracted: Dict) -> None:
        from app.models.report_step import ReportStep  # avoid circular import

        step = self.db.get(ReportStep, step_id)
        if step is None:
            logger.warning("_update_step_data: step %d not found", step_id)
            return

        current = step.data or {}
        merged  = _merge_extracted(current, extracted)

        step.data       = merged
        step.updated_at = datetime.now(timezone.utc)
        self.db.commit()

        logger.info(
            "Auto-updated step %d data from conversation extraction (keys: %s)",
            step_id,
            list(extracted.keys()),
        )

    @staticmethod
    def _infer_state(section_key: str, messages: List[Dict]) -> str:
        """
        Infer conversation state from message history.
        Only return ready_to_validate when the last extracted payload
        actually satisfies section completeness — not just because a JSON
        block was emitted (e.g. mid-accumulation team_members dumps).
        """
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                meta = msg.get("meta") or {}
                extracted = meta.get("extracted_fields")
                if extracted and _section_is_complete(section_key, extracted):
                    return "ready_to_validate"
        return "opening" if len(messages) <= 1 else "collecting"