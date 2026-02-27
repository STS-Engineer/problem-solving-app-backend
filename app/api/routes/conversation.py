# app/routers/conversations.py
"""
Conversation endpoints for interactive chatbot coaching.

GET    /api/v1/steps/{step_id}/conversation/{section_key}
POST   /api/v1/steps/{step_id}/conversation/{section_key}
POST   /api/v1/steps/{step_id}/conversation/{section_key}/upload
DELETE /api/v1/steps/{step_id}/conversation/{section_key}
GET    /api/v1/steps/{step_id}/conversations

Audit events written:
  upload_conversation_files  → file_uploaded      (one per file)
  send_message (first fill)  → step_filled
  send_message (re-fill)     → step_updated  (if extracted_fields differ)
  send_message (no fields)   → comment_added (bot reply stored as a note)
  reset_conversation         → step_reopened
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text as sa_text

from app.api.deps import get_db
from app.models.file import File as FileModel
from app.models.step_file import StepFile
from app.services.conversation_service import ConversationService
from app.services.chatbot_service import KnowledgeBaseRetriever

# Sync audit helpers — conversation router uses sqlalchemy.orm.Session (sync)
from app.services.audit_service import (
    _complaint_id_for_step,
    _report_id_for_step,
    log_file_uploaded_sync,
    log_step_filled_sync,
    log_step_updated_sync,
    log_step_reopened_sync,
    log_comment_added_sync,
)

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
# Audit helpers (step metadata lookup)
# ─────────────────────────────────────────────────────────────────────────────

def _step_code_for_id(db: Session, step_id: int) -> str | None:
    """Resolve the step_code (D1–D8) for a given report_steps.id."""
    row = db.execute(
        sa_text("SELECT step_code FROM report_steps WHERE id = :id"),
        {"id": step_id},
    ).fetchone()
    return str(row[0]) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Guards
# ─────────────────────────────────────────────────────────────────────────────

def _require_step(step_id: int, db: Session) -> None:
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


# Keep router validation in sync with conversation_service SECTION_OPENING / schemas.
# NOTE: include legacy keys if other UI parts still call them.
VALID_SECTION_KEYS = {
    # D1
    "team_members",

    # D2
    "five_w_2h", "deviation", "is_is_not",

    # D3
    "containment", "restart",

    # D4
    "four_m_occurrence", "four_m_non_detection",

    # D5
    "corrective_occurrence", "corrective_detection",

    # D6
    "implementation", "monitoring_checklist",

    # D7
    "prevention", "knowledge", "lessons_learned",

    # D8
    "closure",

    # Legacy / backwards compatibility (if old frontend still calls these)
    "root_cause", "corrective_actions",
}


def _require_section(section_key: str) -> None:
    if section_key not in VALID_SECTION_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown section_key '{section_key}'. "
                   f"Valid keys: {sorted(VALID_SECTION_KEYS)}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    message: str
    uploaded_file_names: Optional[List[str]] = None


class ConversationResponse(BaseModel):
    step_id: int
    section_key: str
    messages: list
    state: str


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
    _require_step(step_id, db)
    _require_section(section_key)
    svc = ConversationService(db)
    return svc.get_or_start_conversation(step_id, section_key)


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

    _require_step(step_id, db)
    _require_section(section_key)

    kb = KnowledgeBaseRetriever(db)
    complaint_context = kb.get_complaint_context(step_id)

    svc = ConversationService(db)

    # ── Snapshot existing fields BEFORE the update so we can diff ────────────
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
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    # ── Write audit log based on what the service extracted ──────────────────
    try:
        extracted: dict[str, Any] = response.get("extracted_fields") or {}
        new_state: str = response.get("state") or "collecting"
        bot_reply: str = response.get("bot_reply") or ""

        # Resolve identifiers (best-effort; skip if resolution fails)
        complaint_id = _complaint_id_for_step(db, step_id)
        report_id    = _report_id_for_step(db, step_id)
        step_code    = _step_code_for_id(db, step_id)

        if extracted:
            # Determine which fields actually changed (top-level keys)
            changed = [k for k, v in extracted.items() if existing_data.get(k) != v]

            # Define "first fill" as: before = not fulfilled; after = fulfilled; and no prior persisted data
            first_fill = (
                previous_state != "fulfilled"
                and new_state == "fulfilled"
                and not existing_data
            )

            if first_fill:
                log_step_filled_sync(
                    db,
                    complaint_id,
                    report_id,
                    step_id,
                    step_code or section_key,
                    fields_snapshot=extracted,
                    performed_by_email=None,  # chatbot / user context unavailable here
                )
            elif changed:
                old_values = {k: existing_data.get(k) for k in changed}
                new_values = {k: extracted[k] for k in changed}
                log_step_updated_sync(
                    db,
                    complaint_id,
                    report_id,
                    step_id,
                    step_code or section_key,
                    changed_fields=changed,
                    old_values=old_values,
                    new_values=new_values,
                    performed_by_email=None,
                )
        else:
            if bot_reply.strip():
                log_comment_added_sync(
                    db,
                    complaint_id,
                    step_id=step_id,
                    step_code=step_code,
                    comment=f"[bot] {bot_reply.strip()[:500]}",
                    performed_by_email=None,
                )

        # Single commit point for send_message (service only flushes)
        db.commit()

    except Exception as audit_exc:
        # Audit failure must NEVER break the user-facing response.
        import sys
        print(f"[audit] WARNING: failed to write audit log for step {step_id}: {audit_exc}", file=sys.stderr)
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
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """
    Upload one or more evidence files while chatting.

    The frontend calls this BEFORE sending the accompanying text message,
    then passes the returned filenames in `uploaded_file_names` on the
    POST /conversation/{section_key} request so the AI sees them.

    Writes a file_uploaded audit log entry for each file.
    Returns a list of file records (same shape as the step_files router).
    """
    _require_step(step_id, db)
    _require_section(section_key)

    if not files:
        raise HTTPException(status_code=422, detail="No files provided")

    # Resolve audit identifiers once (best-effort)
    try:
        complaint_id = _complaint_id_for_step(db, step_id)
        step_code    = _step_code_for_id(db, step_id)
    except Exception:
        complaint_id = None
        step_code    = None

    results = []
    for file in files:
        original_name = file.filename or "unnamed"
        ext = Path(original_name).suffix.lower()

        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"File type '{ext}' not allowed. "
                       "Accepted: images (jpg, png, gif, webp, bmp, tiff) and PDF.",
            )

        content = await file.read()

        if len(content) == 0:
            raise HTTPException(status_code=422, detail=f"File '{original_name}' is empty.")

        if len(content) > MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File '{original_name}' too large "
                       f"({_human_size(len(content))}). Max 25 MB.",
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

        # ── Save to disk ──────────────────────────────────────────────────────
        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = _upload_dir() / stored_name
        dest.write_bytes(content)

        # ── Insert file record ────────────────────────────────────────────────
        db_file = FileModel(
            purpose       ="evidence",
            original_name =original_name,
            stored_path   =str(dest),
            size_bytes    =len(content),
            mime_type     =mime_type,
            uploaded_by   =SYSTEM_USER_ID,
            checksum      =_sha256(content),
            created_at    =datetime.now(timezone.utc),
        )
        db.add(db_file)
        db.flush()

        # ── Link to step ──────────────────────────────────────────────────────
        step_file = StepFile(
            report_step_id=step_id,
            file_id       =db_file.id,
            created_at    =datetime.now(timezone.utc),
        )
        db.add(step_file)
        db.flush()
        db.refresh(step_file)

        serialized = _serialize_file(step_file)
        results.append(serialized)

        # ── Write audit log entry for this file ───────────────────────────────
        if complaint_id is not None:
            log_file_uploaded_sync(
                db,
                complaint_id,
                step_id,
                step_code or section_key,
                filename=original_name,
                file_size=len(content),
                mime_type=mime_type,
                performed_by_email=None,  # extend when auth is available
            )

    # ── Update evidence_documents on the deviation section ───────────────────
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
    _require_step(step_id, db)
    _require_section(section_key)

    svc = ConversationService(db)
    result = svc.reset_conversation(step_id, section_key)

    # ── Audit: step_reopened ──────────────────────────────────────────────────
    try:
        complaint_id = _complaint_id_for_step(db, step_id)
        report_id    = _report_id_for_step(db, step_id)
        step_code    = _step_code_for_id(db, step_id)

        log_step_reopened_sync(
            db,
            complaint_id,
            report_id,
            step_id,
            step_code or section_key,
            section_key=section_key,
            performed_by_email=None,
        )
        db.commit()
    except Exception as audit_exc:
        import sys
        print(f"[audit] WARNING: failed to write step_reopened for step {step_id}: {audit_exc}", file=sys.stderr)
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
    _require_step(step_id, db)
    svc = ConversationService(db)
    sections = svc.get_all_section_conversations(step_id)
    return {"step_id": step_id, "sections": sections}