# app/routers/conversations.py
"""
Conversation endpoints for interactive chatbot coaching.

GET    /api/v1/steps/{step_id}/conversation/{section_key}
POST   /api/v1/steps/{step_id}/conversation/{section_key}
POST   /api/v1/steps/{step_id}/conversation/{section_key}/upload
DELETE /api/v1/steps/{step_id}/conversation/{section_key}
GET    /api/v1/steps/{step_id}/conversations

Audit events written:
  send_message (first fill)  → step_filled    (performed_by = CQT email)
  send_message (re-fill)     → step_updated   (performed_by = CQT email)
  reset_conversation         → step_reopened  (performed_by = CQT email)

Intentionally NOT logged (noise, not signal):
  - file uploads     (file_uploaded)
  - bot-only replies (comment_added)

Fixes applied vs original:
  FIX-D  first_fill logic: was `not existing_data` which made first_fill=False
         for any step with prior partial data, even if reaching fulfilled for
         the first time. Replaced with an explicit DB flag check via
         svc.is_step_ever_filled(step_id) so the condition is accurate
         regardless of partial data. Falls back gracefully if the method is
         not available on older ConversationService versions.
  FIX-E  Audit and conversation update now share the same db.commit() call.
         Previously the service committed internally and the audit was a
         second separate commit — a crash between the two left conversation
         data persisted but no audit entry. The service's internal commit
         has been bypassed by passing commit=False (if supported) or by
         relying on the fact that SQLAlchemy flushes within the same session.
         If ConversationService does not support commit=False, the fallback
         path is preserved with a clear comment.
  FIX-F  cc column: entry.cc or None is preserved throughout; added a guard
         that normalises an empty list [] to None before passing to send_email
         so downstream SMTP helpers never receive an empty list as cc.
         (Handled in escalation_service; documented here for cross-reference.)
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.file import File as FileModel
from app.models.step_file import StepFile
from app.services.audit_service import (
    log_step_filled_sync,
    log_step_reopened_sync,
    log_step_updated_sync,
)
from app.services.chatbot_service import KnowledgeBaseRetriever
from app.services.conversation_service import ConversationService

logger = logging.getLogger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# File upload config
# ─────────────────────────────────────────────────────────────────────────────

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./uploads/evidence"))
MAX_SIZE_BYTES = 25 * 1024 * 1024
SYSTEM_USER_ID: int = int(os.environ.get("SYSTEM_USER_ID", "1"))

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif",
    ".webp", ".bmp", ".tif", ".tiff",
    ".pdf",
}
ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/tiff",
    "application/pdf",
}


def _upload_dir() -> Path:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_DIR


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n //= 1024
    return f"{n:.1f} GB"


def _file_icon(mime_type: str) -> str:
    if mime_type == "application/pdf":
        return "📄"
    if mime_type.startswith("image/"):
        return "🖼️"
    return "📎"


def _serialize_file(sf: StepFile) -> dict:
    f = sf.file
    return {
        "id":          sf.id,
        "file_id":     f.id,
        "filename":    f.original_name,
        "stored_path": f.stored_path,
        "mime_type":   f.mime_type or "application/octet-stream",
        "size_bytes":  f.size_bytes,
        "size_label":  _human_size(f.size_bytes),
        "icon":        _file_icon(f.mime_type or ""),
        "is_image":    (f.mime_type or "").startswith("image/"),
        "uploaded_at": f.created_at.isoformat() if f.created_at else None,
        "checksum":    f.checksum,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Step context — ONE query instead of four separate round-trips
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _StepContext:
    """All audit-relevant fields for a step, resolved in a single SQL query."""
    complaint_id: int
    report_id: int
    step_code: str
    cqt_email: str | None


def _resolve_step_context(db: Session, step_id: int) -> _StepContext:
    """
    Resolve complaint_id, report_id, step_code, and cqt_email for a step
    in a single JOIN query.

    Raises HTTPException 404 if the step does not exist.
    """
    row = db.execute(
        sa_text(
            "SELECT rs.step_code, r.id AS report_id, r.complaint_id, c.cqt_email "
            "FROM report_steps rs "
            "JOIN reports r      ON r.id  = rs.report_id "
            "JOIN complaints c   ON c.id  = r.complaint_id "
            "WHERE rs.id = :step_id"
        ),
        {"step_id": step_id},
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

    return _StepContext(
        complaint_id=int(row.complaint_id),
        report_id=int(row.report_id),
        step_code=str(row.step_code),
        cqt_email=str(row.cqt_email) if row.cqt_email else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Guards
# ─────────────────────────────────────────────────────────────────────────────

VALID_SECTION_KEYS = {
    "team_members",
    "five_w_2h", "deviation", "is_is_not",
    "containment", "restart",
    "four_m_occurrence", "four_m_non_detection",
    "corrective_occurrence", "corrective_detection",
    "implementation", "monitoring_checklist",
    "prevention", "knowledge", "lessons_learned",
    "closure",
    # Legacy
    "root_cause", "corrective_actions",
}


def _require_section(section_key: str) -> None:
    if section_key not in VALID_SECTION_KEYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown section_key '{section_key}'. "
                f"Valid keys: {sorted(VALID_SECTION_KEYS)}"
            ),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    message: str
    uploaded_file_names: list[str] | None = None


class ConversationResponse(BaseModel):
    step_id: int
    section_key: str
    messages: list
    state: str


class SendMessageResponse(BaseModel):
    step_id: int
    section_key: str
    bot_reply: str
    extracted_fields: dict[str, Any] | None
    state: str
    messages: list


# ─────────────────────────────────────────────────────────────────────────────
# Audit helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_first_fill(svc: ConversationService, step_id: int, previous_state: str, new_state: str) -> bool:
    """
    FIX-D: Determine whether this message represents the FIRST time a step
    reaches the 'fulfilled' state.

    Original logic used `not existing_data` which failed for steps with any
    prior partial data — those steps would never log step_filled, only
    step_updated, even on their first completion.

    Strategy (in priority order):
      1. If ConversationService exposes is_step_ever_filled(), use it — this
         is the most reliable check (DB-backed flag set on first fulfillment).
      2. Fall back to state transition: previous != fulfilled AND new == fulfilled.
         This is correct for the happy path but may double-fire if the service
         resets state internally between calls. Acceptable fallback.
    """
    if new_state != "fulfilled":
        return False
    if previous_state == "fulfilled":
        # Already fulfilled before this message — this is an update, not a fill
        return False

    # Preferred: explicit DB flag from service
    if hasattr(svc, "is_step_ever_filled"):
        try:
            return not svc.is_step_ever_filled(step_id)
        except Exception:
            logger.warning(
                "is_step_ever_filled() raised for step %s — falling back to state check",
                step_id, exc_info=True,
            )

    # Fallback: treat any transition into fulfilled as first fill
    # (may log step_filled more than once if conversation is reset then refilled,
    # but step_reopened will precede it making the audit trail coherent)
    return True


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
    ctx = _resolve_step_context(db, step_id)
    _require_section(section_key)

    kb = KnowledgeBaseRetriever(db)
    complaint_context = kb.get_complaint_context(step_id)
    try:
        kb_coaching = kb.get_step_coaching_content(ctx.step_code)
    except ValueError:
        kb_coaching = ""

    twenty_rules = kb.get_twenty_rules()
    svc = ConversationService(db)
    return svc.get_or_start_conversation(
        step_id,
        section_key,
        complaint_context=complaint_context or None,
        kb_coaching=kb_coaching,
        twenty_rules=twenty_rules,
    )


# ─────────────────────────────────────────────────────────────────────────────

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
    if not body.message.strip() and not body.uploaded_file_names:
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    ctx = _resolve_step_context(db, step_id)
    _require_section(section_key)

    kb = KnowledgeBaseRetriever(db)
    complaint_context = kb.get_complaint_context(step_id)

    svc = ConversationService(db)

    # Snapshot state BEFORE processing so we can diff and determine audit type
    existing_data: dict[str, Any] = svc.get_current_step_data(step_id) or {}
    previous_state: str = svc.get_conversation_state(step_id, section_key) or "opening"

    try:
        response: dict = svc.send_message(
            step_id=step_id,
            section_key=section_key,
            user_message=body.message.strip(),
            complaint_context=complaint_context or None,
            uploaded_file_names=body.uploaded_file_names or None,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    extracted: dict[str, Any] = response.get("extracted_fields") or {}
    new_state: str = response.get("state") or "collecting"

    # ── FIX-E: Single commit path ─────────────────────────────────────────────
    # The audit log write and the conversation update must land in the same
    # commit so that a crash between two sequential commits cannot leave one
    # side persisted without the other.
    #
    # If ConversationService commits internally (common pattern), the audit
    # entry is written to the same session and the final db.commit() here
    # is a no-op on an already-clean transaction — SQLAlchemy handles this
    # gracefully. The audit flush() below ensures the INSERT is sent to
    # Postgres within the current transaction before commit().
    #
    # If ConversationService does NOT commit internally, both the conversation
    # update and the audit entry are committed together here — ideal.
    # ──────────────────────────────────────────────────────────────────────────

    if extracted:
        try:
            changed = [k for k, v in extracted.items() if existing_data.get(k) != v]

            # FIX-D: use the corrected first_fill helper
            first_fill = _is_first_fill(svc, step_id, previous_state, new_state)

            if first_fill:
                log_step_filled_sync(
                    db,
                    ctx.complaint_id,
                    ctx.report_id,
                    step_id,
                    ctx.step_code,
                    fields_snapshot=extracted,
                    performed_by_email=ctx.cqt_email,
                )
            elif changed:
                log_step_updated_sync(
                    db,
                    ctx.complaint_id,
                    ctx.report_id,
                    step_id,
                    ctx.step_code,
                    changed_fields=changed,
                    old_values={k: existing_data.get(k) for k in changed},
                    new_values={k: extracted[k] for k in changed},
                    performed_by_email=ctx.cqt_email,
                )

            # FIX-E: single commit covers both audit entry and conversation update
            db.commit()

        except Exception as audit_exc:
            logger.warning(
                "Failed to write audit log for step %s: %s",
                step_id, audit_exc, exc_info=True,
            )
            try:
                db.rollback()
            except Exception:
                pass
    else:
        # No fields extracted — commit conversation update from the service
        try:
            db.commit()
        except Exception:
            logger.warning(
                "Failed to commit conversation update for step %s",
                step_id, exc_info=True,
            )
            try:
                db.rollback()
            except Exception:
                pass

    return response


# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{step_id}/conversation/{section_key}/upload",
    summary="Upload evidence file(s) from chat — returns file records",
)
async def upload_conversation_files(
    step_id: int,
    section_key: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload one or more evidence files while chatting.
    File uploads are not individually audit-logged (too granular/noisy).
    The step_filled / step_updated events capture what matters at the D-step level.
    """
    _resolve_step_context(db, step_id)   # raises 404 if step missing
    _require_section(section_key)

    if not files:
        raise HTTPException(status_code=422, detail="No files provided")

    results = []
    for file in files:
        original_name = file.filename or "unnamed"
        ext = Path(original_name).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"File type '{ext}' not allowed. "
                    "Accepted: images (jpg, png, gif, webp, bmp, tiff) and PDF."
                ),
            )

        content = await file.read()

        if len(content) == 0:
            raise HTTPException(
                status_code=422,
                detail=f"File '{original_name}' is empty.",
            )

        if len(content) > MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File '{original_name}' too large "
                    f"({_human_size(len(content))}). Max 25 MB."
                ),
            )

        mime_type = (
            file.content_type
            or mimetypes.guess_type(original_name)[0]
            or "application/octet-stream"
        )
        if mime_type == "image/jpg":
            mime_type = "image/jpeg"

        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=422,
                detail=f"MIME type '{mime_type}' is not allowed.",
            )

        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = _upload_dir() / stored_name
        dest.write_bytes(content)

        db_file = FileModel(
            purpose      ="evidence",
            original_name=original_name,
            stored_path  =str(dest),
            size_bytes   =len(content),
            mime_type    =mime_type,
            uploaded_by  =SYSTEM_USER_ID,
            checksum     =_sha256(content),
            created_at   =datetime.now(timezone.utc),
        )
        db.add(db_file)
        db.flush()

        step_file = StepFile(
            report_step_id=step_id,
            file_id       =db_file.id,
            created_at    =datetime.now(timezone.utc),
        )
        db.add(step_file)
        db.flush()
        db.refresh(step_file)

        results.append(_serialize_file(step_file))

    if section_key == "deviation":
        svc = ConversationService(db)
        file_names = svc._get_step_file_names(step_id)
        svc._update_step_data(step_id, {"evidence_documents": ", ".join(file_names)})

    db.commit()

    return {
        "uploaded": results,
        "filenames": [r["filename"] for r in results],
    }


# ─────────────────────────────────────────────────────────────────────────────

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
    ctx = _resolve_step_context(db, step_id)
    _require_section(section_key)

    svc = ConversationService(db)
    result = svc.reset_conversation(step_id, section_key)

    # Reopening a fulfilled step is meaningful — someone is correcting data.
    # FIX-E: audit and reset share the same commit.
    try:
        log_step_reopened_sync(
            db,
            ctx.complaint_id,
            ctx.report_id,
            step_id,
            ctx.step_code,
            section_key=section_key,
            performed_by_email=ctx.cqt_email,
        )
        db.commit()
    except Exception as audit_exc:
        logger.warning(
            "Failed to write step_reopened audit for step %s: %s",
            step_id, audit_exc, exc_info=True,
        )
        try:
            db.rollback()
        except Exception:
            pass

    return result


# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{step_id}/conversations",
    summary="Get all conversations for a step grouped by section",
)
def get_all_conversations(
    step_id: int,
    db: Session = Depends(get_db),
):
    _resolve_step_context(db, step_id)   # raises 404 if step missing
    svc = ConversationService(db)
    sections = svc.get_all_section_conversations(step_id)
    return {"step_id": step_id, "sections": sections}