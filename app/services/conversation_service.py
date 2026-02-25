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
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session
from sqlalchemy import text as sa_text
from fastapi.encoders import jsonable_encoder

from app.core.config import settings
from app.models.step_conversation import StepConversation

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION OPENING MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

SECTION_OPENING: Dict[str, str] = {
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
        "4. Any **evidence documents** to reference? (optional)"
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

# JSON schemas the AI must produce when extraction is complete
EXTRACTION_SCHEMA: Dict[str, str] = {
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
  "evidence_documents":  "<string — doc names/refs, or empty string>"
}
standard_applicable, expected_situation, observed_situation are REQUIRED.""",

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
"""


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
        """
        Return existing messages for a section, or start fresh.
        """
        messages = self._load_messages(step_id, section_key)
        if not messages:
            opening = SECTION_OPENING.get(
                section_key,
                "Let's fill in this section. Please provide the required information.",
            )
            self._persist_message(step_id, section_key, "assistant", opening, 0)
            messages = [self._msg_dict("assistant", opening, 0)]

        return {
            "step_id": step_id,
            "section_key": section_key,
            "messages": messages,
            "state": self._infer_state(messages),
        }

    def send_message(
        self,
        step_id: int,
        section_key: str,
        user_message: str,
        complaint_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Process a user message: persist it, call AI, persist bot reply.
        Returns full updated conversation + any extracted fields.
        """
        history = self._load_messages(step_id, section_key)
        next_idx = len(history)

        # Persist user turn
        self._persist_message(step_id, section_key, "user", user_message, next_idx)
        history.append(self._msg_dict("user", user_message, next_idx))
        next_idx += 1

        # Call AI
        bot_reply = self._call_ai(section_key, history, complaint_context)

        # Parse extracted fields
        extracted = self._parse_extracted_fields(bot_reply)
        meta = {"extracted_fields": extracted} if extracted else None

        # Persist bot reply
        self._persist_message(step_id, section_key, "assistant", bot_reply, next_idx, meta)
        history.append(self._msg_dict("assistant", bot_reply, next_idx, meta))

        # Auto-update step.data if extraction succeeded
        if extracted:
            self._update_step_data(step_id, extracted)

        state = "ready_to_validate" if extracted else "collecting"

        return {
            "step_id": step_id,
            "section_key": section_key,
            "bot_reply": bot_reply,
            "extracted_fields": extracted,
            "state": state,
            "messages": history,
        }

    def reset_conversation(self, step_id: int, section_key: str) -> Dict[str, Any]:
        """Wipe conversation for a section and restart."""
        self.db.query(StepConversation).filter(
            StepConversation.report_step_id == step_id,
            StepConversation.section_key == section_key,
        ).delete()
        self.db.commit()
        return self.get_or_start_conversation(step_id, section_key)

    def get_all_section_conversations(self, step_id: int) -> Dict[str, List[Dict]]:
        """Return all conversations for a step, grouped by section."""
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

    def _call_ai(
        self,
        section_key: str,
        history: List[Dict],
        complaint_context: Optional[Dict],
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

        system = (
            CONV_SYSTEM_PROMPT
            + f"\n\nSECTION: {section_key}"
            + context_block
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
                StepConversation.section_key == section_key,
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
        meta: Optional[Dict] = None,
        created_at: Optional[datetime] = None,
    ) -> Dict:
        return {
            "role": role,
            "content": content,
            "message_index": message_index,
            "meta": meta,
            "created_at": created_at.isoformat() if created_at else None,
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

    def _update_step_data(self, step_id: int, extracted: Dict) -> None:
        result = self.db.execute(
            sa_text("SELECT data FROM report_steps WHERE id = :id"),
            {"id": step_id},
        ).fetchone()
        current = (result[0] or {}) if result else {}
        merged = {**current, **extracted}
        self.db.execute(
            sa_text(
                "UPDATE report_steps SET data = :data::jsonb, updated_at = NOW() "
                "WHERE id = :id"
            ),
            {"data": json.dumps(jsonable_encoder(merged)), "id": step_id},
        )
        self.db.commit()
        logger.info("Auto-updated step %d data from conversation extraction", step_id)

    @staticmethod
    def _infer_state(messages: List[Dict]) -> str:
        for msg in reversed(messages):
            if msg["role"] == "assistant":
                meta = msg.get("meta") or {}
                if meta.get("extracted_fields"):
                    return "ready_to_validate"
        if len(messages) <= 1:
            return "opening"
        return "collecting"