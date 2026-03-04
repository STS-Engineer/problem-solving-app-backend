"""
conversation_service.py
═══════════════════════
Conversational coaching service.

Key changes vs previous version:
  1. All prompts moved to prompts.py
  2. _build_context_block replaced by build_already_known_block from prompts.py
     → structured [ALREADY KNOWN] block prevents the AI from re-asking
       confirmed fields or repeating questions from conversation history
  3. _build_smart_opening simplified — seed now references [ALREADY KNOWN]
     directly instead of re-dumping raw data
  4. AI system prompt structure:
       CONV_SYSTEM_PROMPT          (behaviour + memory + format rules)
       + build_already_known_block (complaint + confirmed data — concise)
       + SECTION_COACHING_RULES    (section-specific validation)
       + EXTRACTION INSTRUCTION    (when/how to emit JSON)
       + EXTRACTION_SCHEMA         (required JSON shape)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.step_conversation import StepConversation
from app.models.step_file import StepFile
from app.models.file import File as FileModel
from app.services.section_config import get_all_section_keys
from app.services.prompts import (
    CONV_SYSTEM_PROMPT,
    SECTION_COACHING_RULES,
    EXTRACTION_SCHEMA,
    build_already_known_block,
)

logger = logging.getLogger(__name__)


# =============================================================================
# MERGE HELPERS  (unchanged)
# =============================================================================

_FIELD_ALIASES: Dict[str, str] = {
    "role":            "function",
    "job_title":       "function",
    "position":        "function",
    "title":           "function",
    "dept":            "department",
    "department_name": "department",
}

_DEPARTMENT_MAP: Dict[str, str] = {
    "production": "production", "manufacturing": "production", "fabrication": "production",
    "maintenance": "maintenance",
    "engineering": "engineering", "r&d": "engineering",
    "quality": "quality", "qa": "quality", "qc": "quality",
    "quality control": "quality", "quality assurance": "quality",
    "logistics": "logistics", "supply chain": "logistics", "warehouse": "logistics",
    "supplier quality": "supplier_quality", "supplier_quality": "supplier_quality", "sqe": "supplier_quality",
}

_FUNCTION_MAP: Dict[str, str] = {
    "operator": "operator",
    "line_leader": "line_leader", "line leader": "line_leader",
    "team leader": "team_leader", "team_leader": "team_leader", "teamleader": "team_leader", "leader": "team_leader",
    "supervisor": "supervisor",
    "engineer": "engineer", "engineering": "engineer", "technician": "engineer",
    "project manager": "project_manager", "project_manager": "project_manager", "manager": "project_manager",
}

VALID_DEPARTMENTS = {"production","maintenance","engineering","quality","logistics","supplier_quality","other"}
VALID_FUNCTIONS   = {"operator","line_leader","supervisor","engineer","team_leader","project_manager","other"}


def _normalise_member(raw: Dict) -> Dict:
    member: Dict[str, str] = {}
    for key, value in raw.items():
        canonical_key = _FIELD_ALIASES.get(str(key).lower(), str(key).lower())
        member[canonical_key] = str(value).strip() if value else ""

    dept_raw = member.get("department", "").lower().strip()
    member["department"] = _DEPARTMENT_MAP.get(dept_raw, dept_raw if dept_raw in VALID_DEPARTMENTS else "other")

    func_raw = member.get("function", "").lower().strip()
    member["function"] = _FUNCTION_MAP.get(func_raw, func_raw if func_raw in VALID_FUNCTIONS else "other")

    return {
        "name":       member.get("name", ""),
        "department": member.get("department", "other"),
        "function":   member.get("function", "other"),
    }


def _merge_extracted(current: Dict, extracted: Dict) -> Dict:
    merged = {**current}
    for key, value in extracted.items():
        if key == "team_members" and isinstance(value, list):
            merged["team_members"] = [_normalise_member(m) for m in value if isinstance(m, dict)]
        elif key == "is_is_not_factors" and isinstance(value, list):
            existing = {f["factor"]: f for f in (merged.get("is_is_not_factors") or [])}
            for patch in value:
                factor = patch.get("factor")
                if factor:
                    existing[factor] = {**(existing.get(factor) or {}), **patch}
            merged["is_is_not_factors"] = list(existing.values())
        elif key == "five_w_2h" and isinstance(value, dict):
            merged["five_w_2h"] = {**(merged.get("five_w_2h") or {}), **value}
        elif key == "suspected_parts_status" and isinstance(value, list):
            existing = {r["location"]: r for r in (merged.get("suspected_parts_status") or [])}
            for row in value:
                loc = row.get("location")
                if loc:
                    existing[loc] = {**(existing.get(loc) or {"location": loc}), **row}
            merged["suspected_parts_status"] = list(existing.values())
        elif key in ("four_m_occurrence", "four_m_non_detection") and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}
        elif key in ("five_whys_occurrence", "five_whys_non_detection") and isinstance(value, dict):
            merged[key] = {**(merged.get(key) or {}), **value}
        elif key in ("corrective_actions_occurrence", "corrective_actions_detection") and isinstance(value, list):
            merged[key] = value
        elif key == "monitoring" and isinstance(value, dict):
            merged["monitoring"] = {**(merged.get("monitoring") or {}), **value}
        elif key in (
            "recurrence_risks", "replication_validations", "knowledge_base_updates",
            "long_term_monitoring", "lesson_disseminations",
        ) and isinstance(value, list):
            merged[key] = value
        else:
            merged[key] = value
    return merged


# =============================================================================
# SECTION COMPLETENESS  (unchanged)
# =============================================================================

def _section_is_complete(section_key: str, extracted: Dict) -> bool:
    if section_key == "team_members":
        members = extracted.get("team_members", [])
        if not isinstance(members, list) or len(members) < 2:
            return False
        return any(m.get("function") == "team_leader" for m in members if isinstance(m, dict))

    if section_key == "five_w_2h":
        w2h = extracted.get("five_w_2h", {})
        return isinstance(w2h, dict) and all(
            str(w2h.get(k, "")).strip()
            for k in ("what", "where", "when", "who", "why", "how", "how_many")
        )

    if section_key == "deviation":
        return all(
            str(extracted.get(k, "")).strip()
            for k in ("standard_applicable", "expected_situation", "observed_situation")
        )

    if section_key == "is_is_not":
        factors = extracted.get("is_is_not_factors", [])
        if not isinstance(factors, list):
            return False
        return sum(
            1 for f in factors
            if isinstance(f, dict)
            and str(f.get("is_problem", "")).strip()
            and str(f.get("is_not_problem", "")).strip()
        ) >= 3

    if section_key == "containment":
        dps = extracted.get("defected_part_status", {})
        has_defected = isinstance(dps, dict) and any(
            bool(v) for k, v in dps.items() if k in ("returned", "isolated", "identified")
        )
        suspected = extracted.get("suspected_parts_status", [])
        has_suspected = isinstance(suspected, list) and any(
            str(r.get("actions", "")).strip() for r in suspected if isinstance(r, dict)
        )
        alert_to = extracted.get("alert_communicated_to", {})
        has_alert = (
            (isinstance(alert_to, dict) and any(bool(v) for v in alert_to.values()))
            or str(extracted.get("alert_number", "")).strip()
        )
        return (has_defected or has_suspected) and bool(has_alert)

    if section_key == "restart":
        rp = extracted.get("restart_production", {})
        return (
            isinstance(rp, dict)
            and str(rp.get("when", "")).strip()
            and str(rp.get("approved_by", "")).strip()
            and str(extracted.get("containment_responsible", "")).strip()
        )

    if section_key in ("four_m_occurrence", "four_m_non_detection"):
        fm_key  = "four_m_occurrence"     if section_key == "four_m_occurrence" else "four_m_non_detection"
        rc_key  = "root_cause_occurrence" if section_key == "four_m_occurrence" else "root_cause_non_detection"
        why_key = "five_whys_occurrence"  if section_key == "four_m_occurrence" else "five_whys_non_detection"
        fm   = extracted.get(fm_key, {})
        rc   = extracted.get(rc_key, {})
        whys = extracted.get(why_key, {})
        whys_filled = (
            sum(1 for w in whys.values() if isinstance(w, dict) and str(w.get("answer", "")).strip())
            if isinstance(whys, dict) else 0
        )
        return (
            isinstance(fm, dict) and str(fm.get("selected_problem", "")).strip()
            and isinstance(rc, dict) and str(rc.get("root_cause", "")).strip()
            and str(rc.get("validation_method", "")).strip()
            and whys_filled >= 3
        )

    if section_key == "corrective_occurrence":
        actions = extracted.get("corrective_actions_occurrence", [])
        return isinstance(actions, list) and any(
            str(a.get("action", "")).strip()
            and str(a.get("responsible", "")).strip()
            and str(a.get("due_date", "")).strip()
            for a in actions if isinstance(a, dict)
        )

    if section_key == "corrective_detection":
        actions = extracted.get("corrective_actions_detection", [])
        return isinstance(actions, list) and any(
            str(a.get("action", "")).strip()
            and str(a.get("responsible", "")).strip()
            and str(a.get("due_date", "")).strip()
            for a in actions if isinstance(a, dict)
        )

    if section_key == "implementation":
        occ = extracted.get("corrective_actions_occurrence", [])
        det = extracted.get("corrective_actions_detection", [])
        return (
            isinstance(occ, list) and any(str(a.get("imp_date", "")).strip() for a in occ if isinstance(a, dict))
        ) or (
            isinstance(det, list) and any(str(a.get("imp_date", "")).strip() for a in det if isinstance(a, dict))
        )

    if section_key == "monitoring_checklist":
        mon = extracted.get("monitoring", {})
        return (
            isinstance(mon, dict)
            and str(mon.get("monitoring_interval", "")).strip()
            and str(extracted.get("audited_by", "")).strip()
        )

    if section_key == "prevention":
        risks = extracted.get("recurrence_risks", [])
        return isinstance(risks, list) and any(
            str(r.get("area_line_product", "")).strip() and str(r.get("action_taken", "")).strip()
            for r in risks if isinstance(r, dict)
        )

    if section_key == "knowledge":
        kb  = extracted.get("knowledge_base_updates", [])
        ltm = extracted.get("long_term_monitoring", [])
        return (
            isinstance(kb, list) and any(str(u.get("document_type", "")).strip() for u in kb if isinstance(u, dict))
        ) or (
            isinstance(ltm, list) and any(str(m.get("checkpoint_type", "")).strip() for m in ltm if isinstance(m, dict))
        )

    if section_key == "lessons_learned":
        disem = extracted.get("lesson_disseminations", [])
        return bool(
            isinstance(disem, list)
            and any(str(d.get("audience_team", "")).strip() for d in disem if isinstance(d, dict))
            and str(extracted.get("ll_conclusion", "")).strip()
        )

    if section_key == "closure":
        statement = str(extracted.get("closure_statement", "")).strip()
        sigs = extracted.get("signatures", {})
        return (
            len(statement) >= 200
            and isinstance(sigs, dict)
            and str(sigs.get("closed_by", "")).strip()
            and str(sigs.get("closure_date", "")).strip()
        )

    return bool(extracted)


# =============================================================================
# PRIOR STEP DATA COLLECTOR
# =============================================================================

def _collect_all_step_data(db: Session, step_id: int) -> Dict[str, Any]:
    """Merge step.data from ALL steps of the same report into one flat dict."""
    from app.models.report_step import ReportStep

    current_step = db.get(ReportStep, step_id)
    if current_step is None:
        return {}

    all_steps = (
        db.query(ReportStep)
        .filter(ReportStep.report_id == current_step.report_id)
        .order_by(ReportStep.step_code)
        .all()
    )

    merged: Dict[str, Any] = {}
    for step in all_steps:
        if step.data:
            merged.update(step.data)

    return merged


# =============================================================================
# SMART OPENING SEED BUILDER
# =============================================================================

def _build_smart_opening(
    section_key: str,
    all_step_data: Dict[str, Any],
    complaint_context: Optional[Dict],
) -> str:
    """
    Build a seed passed to the AI to generate a natural opening message.

    The seed references [ALREADY KNOWN] (already in the system prompt) and
    instructs the AI on HOW to open — not what to say word-for-word.
    This prevents the AI from ignoring pre-filled data on the opening turn.
    """
    lines = [
        f"[OPENING INSTRUCTION FOR SECTION: {section_key}]",
        "",
        "You have just received the complaint file and all prior step data.",
        "The [ALREADY KNOWN] block in your system prompt tells you exactly",
        "what has already been confirmed. Use it.",
        "",
        "Your opening message must follow this structure:",
        "  1. One sentence recapping the problem (from the complaint context).",
        "  2. State what you already know for this section — reference the",
        "     confirmed fields from [ALREADY KNOWN] naturally, in prose.",
        "     Do NOT list field names. Weave them into a sentence.",
        "  3. Ask exactly ONE question for the first genuine gap.",
        "",
        "STRICT RULES for this opening:",
        "  - Do NOT ask for anything already in [ALREADY KNOWN].",
        "  - Do NOT list all the fields you need to fill.",
        "  - Do NOT say 'Let me walk you through...' or similar robotic openers.",
        "  - Format your reply using the **What I know** / **What I need** structure.",
    ]

    # Add section-specific opening hint
    _OPENING_HINTS = {
        "team_members": (
            "  - Mention which departments are typically needed for this defect type.\n"
            "  - Ask who will be leading the 8D — just that one question."
        ),
        "five_w_2h": (
            "  - The auto-extraction has pre-filled several 5W2H fields. State what\n"
            "    is already confirmed, then ask only for the first missing field."
        ),
        "deviation": (
            "  - You know the product type and process. Suggest the likely applicable\n"
            "    standard as a proposal, ask the user to confirm or correct it."
        ),
        "is_is_not": (
            "  - Product and Time can likely be inferred from the complaint and D2.\n"
            "    Present your inference and ask the user to confirm."
        ),
        "containment": (
            "  - Propose what containment you would expect given the D2 quantity\n"
            "    and defect. Ask what has already been done."
        ),
        "four_m_occurrence": (
            "  - Propose the 1-2 most likely cause categories from your domain\n"
            "    knowledge. Ask the user to confirm or redirect."
        ),
        "four_m_non_detection": (
            "  - Reason from D2's detection method. Propose the likely detection\n"
            "    gap. Ask the user to confirm."
        ),
        "corrective_occurrence": (
            "  - From the D4 root cause in [ALREADY KNOWN], propose 1-2 likely\n"
            "    corrective actions. Ask the user to confirm or adjust."
        ),
        "implementation": (
            "  - List the D5 planned actions from [ALREADY KNOWN] naturally.\n"
            "    Ask which one has been implemented first."
        ),
        "lessons_learned": (
            "  - Draft the lesson learned conclusion from the full 8D in [ALREADY KNOWN].\n"
            "    Present it and ask the user to confirm or adjust."
        ),
        "closure": (
            "  - Draft a closure statement from the full 8D data in [ALREADY KNOWN].\n"
            "    Ask the user to confirm the 4 closure criteria."
        ),
    }

    hint = _OPENING_HINTS.get(section_key)
    if hint:
        lines.append("")
        lines.append("Section-specific guidance:")
        lines.append(hint)

    return "\n".join(lines)


# =============================================================================
# SERVICE
# =============================================================================

class ConversationService:
    def __init__(self, db: Session):
        self.db = db
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_current_step_data(self, step_id: int) -> Dict[str, Any]:
        from app.models.report_step import ReportStep
        step = self.db.get(ReportStep, step_id)
        return (step.data or {}) if step else {}

    def get_conversation_state(self, step_id: int, section_key: str) -> str:
        messages = self._load_messages(step_id, section_key)
        return self._infer_state(section_key, messages)

    def get_or_start_conversation(
        self,
        step_id: int,
        section_key: str,
        complaint_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        messages = self._load_messages(step_id, section_key)
        if not messages:
            all_step_data = _collect_all_step_data(self.db, step_id)
            seed = _build_smart_opening(section_key, all_step_data, complaint_context)
            opening = self._call_ai(
                section_key=section_key,
                history=[{"role": "user", "content": seed}],
                complaint_context=complaint_context,
                existing_files=[],
                all_step_data=all_step_data,
            )
            self._persist_message(step_id, section_key, "assistant", opening, 0, commit=True)
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
        effective_message = user_message
        if uploaded_file_names:
            file_list = ", ".join(uploaded_file_names)
            effective_message = (
                f"{user_message}\n\n📎 Uploaded: {file_list}" if user_message.strip()
                else f"📎 Uploaded: {file_list}"
            )

        history = self._load_messages(step_id, section_key)
        next_idx = len(history)

        self._persist_message(step_id, section_key, "user", effective_message, next_idx, commit=False)
        history.append(self._msg_dict("user", effective_message, next_idx))
        next_idx += 1

        existing_files = self._get_step_file_names(step_id)
        all_step_data  = _collect_all_step_data(self.db, step_id)

        bot_reply = self._call_ai(
            section_key=section_key,
            history=history,
            complaint_context=complaint_context,
            existing_files=existing_files,
            all_step_data=all_step_data,
        )

        extracted = self._parse_extracted_fields(bot_reply)
        meta = {"extracted_fields": extracted} if extracted else None

        self._persist_message(step_id, section_key, "assistant", bot_reply, next_idx, meta=meta, commit=False)
        history.append(self._msg_dict("assistant", bot_reply, next_idx, meta))

        if extracted:
            if section_key == "deviation" and existing_files:
                current_evidence = extracted.get("evidence_documents", "")
                all_names = list(existing_files)
                for name in (current_evidence.split(",") if current_evidence else []):
                    name = name.strip()
                    if name and name not in all_names:
                        all_names.append(name)
                extracted["evidence_documents"] = ", ".join(all_names)
            self._update_step_data(step_id, extracted, commit=False)

        section_complete = bool(extracted and _section_is_complete(section_key, extracted))

        if section_complete:
            state = "fulfilled"
            self._maybe_mark_step_fulfilled(step_id, just_completed_section=section_key, commit=False)
        elif len(history) > 1:
            state = "collecting"
        else:
            state = "opening"

        self.db.flush()

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
                self._msg_dict(row.role, row.content, row.message_index, row.meta, row.created_at)
            )
        return result

    # ── Internal ───────────────────────────────────────────────────────────────

    def _get_step_file_names(self, step_id: int) -> List[str]:
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
        existing_files: List[str],
        all_step_data: Dict[str, Any],
    ) -> str:
        schema         = EXTRACTION_SCHEMA.get(section_key, "")
        coaching_rules = SECTION_COACHING_RULES.get(section_key, "")

        # Core improvement: build_already_known_block replaces the old
        # _build_context_block. It produces a concise, structured summary
        # of complaint + confirmed fields, explicitly labelled so the AI
        # knows what NOT to ask again.
        already_known = build_already_known_block(
            section_key=section_key,
            all_step_data=all_step_data,
            complaint_context=complaint_context,
        )

        system = (
            CONV_SYSTEM_PROMPT
            + "\n\n"
            + already_known
            + (f"\n\n{coaching_rules}" if coaching_rules else "")
            + "\n\n════════════════════════════════════════\n"
              "EXTRACTION INSTRUCTION\n"
              "════════════════════════════════════════\n"
              "Emit <extracted_fields>{...}</extracted_fields> ONLY when:\n"
              "  1. ALL required fields are confirmed and validated.\n"
              "  2. The user has confirmed the data is correct.\n"
              "  3. NOT on the opening message.\n"
              "  4. NOT while any validation rule is still failing.\n\n"
            + f"Required JSON schema:\n{schema}"
        )

        openai_messages = [{"role": "system", "content": system}]
        for msg in history:
            openai_messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = self.client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=openai_messages,
                temperature=0.3,
                max_completion_tokens=1400,
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
        *,
        commit: bool = False,
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
        if commit:
            self.db.commit()
        else:
            self.db.flush()

    @staticmethod
    def _msg_dict(
        role: str,
        content: str,
        message_index: int,
        meta: Optional[Dict] = None,
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
        match = re.search(r"<extracted_fields>(.*?)</extracted_fields>", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            logger.warning("Failed to parse extracted_fields JSON from AI reply")
            return None

    def _update_step_data(self, step_id: int, extracted: Dict, *, commit: bool = False) -> None:
        from app.models.report_step import ReportStep
        step = self.db.get(ReportStep, step_id)
        if step is None:
            logger.warning("_update_step_data: step %d not found", step_id)
            return
        step.data       = _merge_extracted(step.data or {}, extracted)
        step.updated_at = datetime.now(timezone.utc)
        if commit:
            self.db.commit()
        else:
            self.db.flush()
        logger.info("Saved extracted fields to step %d (keys: %s)", step_id, list(extracted.keys()))

    def _maybe_mark_step_fulfilled(
        self,
        step_id: int,
        just_completed_section: str,
        *,
        commit: bool = False,
    ) -> None:
        from app.models.report_step import ReportStep
        step = self.db.get(ReportStep, step_id)
        if step is None:
            return

        complaint    = step.report.complaint
        all_sections = get_all_section_keys(step.step_code)

        if not all_sections:
            step.status       = "fulfilled"
            complaint.status  = step.step_code
            step.completed_at = datetime.now(timezone.utc)
            if commit: self.db.commit()
            else:      self.db.flush()
            return

        for section_key in all_sections:
            if section_key == just_completed_section:
                continue
            if not self._is_section_fulfilled(step_id, section_key):
                logger.debug("Step %d: section '%s' not yet fulfilled", step_id, section_key)
                return

        step.status       = "fulfilled"
        complaint.status  = step.step_code
        step.completed_at = datetime.now(timezone.utc)
        if commit: self.db.commit()
        else:      self.db.flush()
        logger.info("Step %d (%s) fulfilled — all sections complete", step_id, step.step_code)

    def _is_section_fulfilled(self, step_id: int, section_key: str) -> bool:
        rows = (
            self.db.query(StepConversation)
            .filter(
                StepConversation.report_step_id == step_id,
                StepConversation.section_key    == section_key,
                StepConversation.role           == "assistant",
            )
            .order_by(StepConversation.message_index)
            .all()
        )
        for row in rows:
            meta      = row.meta or {}
            extracted = meta.get("extracted_fields")
            if extracted and _section_is_complete(section_key, extracted):
                return True
        return False

    @staticmethod
    def _infer_state(section_key: str, messages: List[Dict]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                meta      = msg.get("meta") or {}
                extracted = meta.get("extracted_fields")
                if extracted and _section_is_complete(section_key, extracted):
                    return "fulfilled"
        return "opening" if len(messages) <= 1 else "collecting"