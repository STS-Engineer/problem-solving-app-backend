# app/routers/conversations.py
"""
Conversation endpoints for interactive chatbot coaching.

GET    /api/v1/steps/{step_id}/conversation/{section_key}
POST   /api/v1/steps/{step_id}/conversation/{section_key}
DELETE /api/v1/steps/{step_id}/conversation/{section_key}
GET    /api/v1/steps/{step_id}/conversations
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text as sa_text

from app.api.deps import get_db
from app.services.conversation_service import ConversationService
from app.services.chatbot_service import KnowledgeBaseRetriever

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _require_step(step_id: int, db: Session) -> None:
    """
    Raise HTTP 404 if step_id does not exist in report_steps.
    This prevents the FK-violation 500 when the frontend passes a stale/wrong ID.
    """
    row = db.execute(
        sa_text("SELECT id FROM report_steps WHERE id = :id"),
        {"id": step_id},
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Step {step_id} not found. "
                "The step may not have been created yet — "
                "open the step form first to initialise it."
            ),
        )


VALID_SECTION_KEYS = {
    # D1
    "team_members",
    # D2
    "five_w_2h", "deviation", "is_is_not",
    # D3-D8 (future)
    "containment", "root_cause", "corrective_actions",
    "implementation", "prevention", "closure",
}


def _require_section(section_key: str) -> None:
    if section_key not in VALID_SECTION_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown section_key '{section_key}'. Valid keys: {sorted(VALID_SECTION_KEYS)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    message: str


class ConversationResponse(BaseModel):
    step_id: int
    section_key: str
    messages: list
    state: str          # opening | collecting | ready_to_validate


class SendMessageResponse(BaseModel):
    step_id: int
    section_key: str
    bot_reply: str
    extracted_fields: Optional[Dict[str, Any]]
    state: str
    messages: list


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{step_id}/conversation/{section_key}",
    response_model=ConversationResponse,
    summary="Get or start conversation for a section",
)
def get_conversation(
    step_id: int,
    section_key: str,
    db: Session = Depends(get_db),
):
    _require_step(step_id, db)        # ← 404 guard
    _require_section(section_key)     # ← 422 guard
    svc = ConversationService(db)
    return svc.get_or_start_conversation(step_id, section_key)


@router.post(
    "/{step_id}/conversation/{section_key}",
    response_model=SendMessageResponse,
    summary="Send a user message and get bot reply",
)
def send_message(
    step_id: int,
    section_key: str,
    body: SendMessageRequest,
    db: Session = Depends(get_db),
):
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    _require_step(step_id, db)        # ← 404 guard
    _require_section(section_key)     # ← 422 guard

    kb = KnowledgeBaseRetriever(db)
    complaint_context = kb.get_complaint_context(step_id)

    svc = ConversationService(db)
    try:
        return svc.send_message(
            step_id=step_id,
            section_key=section_key,
            user_message=body.message.strip(),
            complaint_context=complaint_context or None,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@router.delete(
    "/{step_id}/conversation/{section_key}",
    response_model=ConversationResponse,
    summary="Reset the conversation for a section",
)
def reset_conversation(
    step_id: int,
    section_key: str,
    db: Session = Depends(get_db),
):
    _require_step(step_id, db)
    _require_section(section_key)
    svc = ConversationService(db)
    return svc.reset_conversation(step_id, section_key)


@router.get(
    "/{step_id}/conversations",
    summary="Get all conversations for a step grouped by section",
)
def get_all_conversations(
    step_id: int,
    db: Session = Depends(get_db),
):
    _require_step(step_id, db)
    svc = ConversationService(db)
    sections = svc.get_all_section_conversations(step_id)
    return {"step_id": step_id, "sections": sections}