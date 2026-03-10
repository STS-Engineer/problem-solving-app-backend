"""
app/routers/conversations.py  
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

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
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
from pathlib import Path

logger = logging.getLogger(__name__)

router = APIRouter()

# ── File upload config ────────────────────────────────────────────────────────

UPLOAD_DIR = Path("/home/uploads/8d")
MAX_SIZE_BYTES = 25 * 1024 * 1024
SYSTEM_USER_ID: int = int(os.environ.get("SYSTEM_USER_ID", "1"))

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif",
    ".webp", ".bmp", ".tif", ".tiff", ".pdf",
}
ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/tiff",
    "application/pdf",
}

_EVIDENCE_SYNC_SECTIONS = {"deviation", "implementation"}



def _upload_dir(request: Request) -> Path:
    upload_dir = Path(request.app.state.uploads_root) / "8d"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


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
        "id":           sf.id,
        "file_id":      f.id,
        "filename":     f.original_name,
        "stored_path":  f.stored_path,
        "mime_type":    f.mime_type or "application/octet-stream",
        "size_bytes":   f.size_bytes,
        "size_label":   _human_size(f.size_bytes),
        "icon":         _file_icon(f.mime_type or ""),
        "is_image":     (f.mime_type or "").startswith("image/"),
        "uploaded_at":  f.created_at.isoformat() if f.created_at else None,
        "checksum":     f.checksum,
        "action_type":  sf.action_type,
        "action_index": sf.action_index,
    }


# ── Step context ──────────────────────────────────────────────────────────────

@dataclass
class _StepContext:
    complaint_id: int
    report_id: int
    step_code: str
    cqt_email: str | None


def _resolve_step_context(db: Session, step_id: int) -> _StepContext:
    row = db.execute(
        sa_text(
            "SELECT rs.step_code, r.id AS report_id, r.complaint_id, c.cqt_email "
            "FROM report_steps rs "
            "JOIN reports r    ON r.id  = rs.report_id "
            "JOIN complaints c ON c.id  = r.complaint_id "
            "WHERE rs.id = :step_id"
        ),
        {"step_id": step_id},
    ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Step {step_id} not found. "
                "Open the step form first to initialise it."
            ),
        )

    return _StepContext(
        complaint_id=int(row.complaint_id),
        report_id=int(row.report_id),
        step_code=str(row.step_code),
        cqt_email=str(row.cqt_email) if row.cqt_email else None,
    )


# ── Guards ────────────────────────────────────────────────────────────────────

VALID_SECTION_KEYS = {
    "team_members",
    "five_w_2h", "deviation", "is_is_not",
    "containment", "restart",
    "four_m_occurrence", "four_m_non_detection",
    "corrective_occurrence", "corrective_detection",
    "implementation", "monitoring_checklist",
    "prevention", "knowledge", "lessons_learned",
    "closure",
    "root_cause", "corrective_actions",
}

VALID_ACTION_TYPES = {"occurrence", "detection"}


def _require_section(section_key: str) -> None:
    # Accept plain keys AND structured "implementation:occurrence:0" keys
    base_key = section_key.split(":")[0]
    if base_key not in VALID_SECTION_KEYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown section_key '{section_key}'. "
                f"Valid keys: {sorted(VALID_SECTION_KEYS)}"
            ),
        )


def _parse_action_scope(
    section_key: str,
    action_type_param: str | None,
    action_index_param: int | None,
) -> tuple[str, str | None, int | None]:
    """
    Resolve (base_section_key, action_type, action_index) from either:
      - a structured section_key like "implementation:occurrence:0"
      - OR explicit query params action_type + action_index

    Query params take precedence over encoded section_key.
    """
    parts = section_key.split(":")
    base_key = parts[0]

    # Default from encoded key
    enc_action_type: str | None = None
    enc_action_index: int | None = None
    if len(parts) == 3:
        enc_action_type = parts[1] if parts[1] in VALID_ACTION_TYPES else None
        try:
            enc_action_index = int(parts[2])
        except (ValueError, IndexError):
            enc_action_index = None

    # Query params override
    action_type = action_type_param if action_type_param is not None else enc_action_type
    action_index = action_index_param if action_index_param is not None else enc_action_index

    # Validate consistency
    if (action_type is None) != (action_index is None):
        raise HTTPException(
            status_code=422,
            detail="action_type and action_index must both be provided or both omitted.",
        )
    if action_type is not None and action_type not in VALID_ACTION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"action_type must be 'occurrence' or 'detection', got '{action_type}'.",
        )

    return base_key, action_type, action_index


# ── Schemas ───────────────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    message: str
    uploaded_file_names: list[str] | None = None
    # D6 per-action context — tells the AI which corrective action
    # the uploaded files belong to (mirrors the upload endpoint scope)
    action_type: str | None = None   # "occurrence" | "detection"
    action_index: int | None = None  # 0-based


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


# ── Audit helpers ─────────────────────────────────────────────────────────────

def _is_first_fill(previous_state: str, new_state: str) -> bool:
    if new_state != "fulfilled":
        return False
    if previous_state == "fulfilled":
        return False
    return True


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/{step_id}/conversation/{section_key}",
    response_model=ConversationResponse,
)
def get_conversation(step_id: int, section_key: str, db: Session = Depends(get_db)):
    ctx = _resolve_step_context(db, step_id)
    _require_section(section_key)
    kb = KnowledgeBaseRetriever(db)
    try:
        kb_coaching = kb.get_step_coaching_content(ctx.step_code)
    except ValueError:
        kb_coaching = ""
    twenty_rules = kb.get_twenty_rules()
    svc = ConversationService(db)
    complaint_context = svc.get_complaint_context(step_id)

    return svc.get_or_start_conversation(
        step_id, section_key,
        complaint_context=complaint_context or None,
        kb_coaching=kb_coaching,
        twenty_rules=twenty_rules,
    )


@router.post("/{step_id}/conversation/{section_key}", response_model=SendMessageResponse)
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
    try:
        kb_coaching = kb.get_step_coaching_content(ctx.step_code)
    except ValueError:
        kb_coaching = ""
    twenty_rules = kb.get_twenty_rules()
    svc = ConversationService(db)
    complaint_context = svc.get_complaint_context(step_id)

    existing_data: dict[str, Any] = svc.get_current_step_data(step_id) or {}
    previous_state: str = svc.get_conversation_state(step_id, section_key) or "opening"

    try:
        response: dict = svc.send_message(
            step_id=step_id,
            section_key=section_key,
            user_message=body.message.strip(),
            complaint_context=complaint_context or None,
            uploaded_file_names=body.uploaded_file_names or None,
            action_type=body.action_type or None,
            action_index=body.action_index,
            kb_coaching=kb_coaching,
            twenty_rules=twenty_rules
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")

    extracted: dict[str, Any] = response.get("extracted_fields") or {}
    new_state: str = response.get("state") or "collecting"

    if extracted:
        try:
            changed = [k for k, v in extracted.items() if existing_data.get(k) != v]
            first_fill = _is_first_fill( previous_state, new_state)
            if first_fill:
                log_step_filled_sync(db, ctx.complaint_id, ctx.report_id, step_id,
                                     ctx.step_code, fields_snapshot=extracted,
                                     performed_by_email=ctx.cqt_email)
            elif changed:
                log_step_updated_sync(db, ctx.complaint_id, ctx.report_id, step_id,
                                      ctx.step_code, changed_fields=changed,
                                      old_values={k: existing_data.get(k) for k in changed},
                                      new_values={k: extracted[k] for k in changed},
                                      performed_by_email=ctx.cqt_email)
            db.commit()
        except Exception as audit_exc:
            logger.warning("Failed to write audit log for step %s: %s", step_id, audit_exc, exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass
    else:
        try:
            db.commit()
        except Exception:
            logger.warning("Failed to commit conversation update for step %s", step_id, exc_info=True)
            try:
                db.rollback()
            except Exception:
                pass

    return response


@router.post("/{step_id}/conversation/{section_key}/upload")
async def upload_conversation_files(
    step_id: int,
    section_key: str,
    request: Request,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    action_type: str | None = Query(
        default=None,
        description="'occurrence' | 'detection' — scope upload to a specific D6 action",
    ),
    action_index: int | None = Query(
        default=None,
        ge=0,
        description="0-based index of the D6 corrective action",
    ),
):
    """
    Upload evidence file(s) from the ChatCoach panel.

    For D6 per-action uploads, pass either:
      - ?action_type=occurrence&action_index=0  as query params, OR
      - encode in the section_key: "implementation:occurrence:0"

    Both methods are equivalent. Query params take precedence.
    """
    _resolve_step_context(db, step_id)
    _require_section(section_key)

    base_key, resolved_action_type, resolved_action_index = _parse_action_scope(
        section_key, action_type, action_index
    )

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
            raise HTTPException(status_code=422, detail=f"File '{original_name}' is empty.")
        if len(content) > MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File '{original_name}' too large ({_human_size(len(content))}). Max 25 MB.",
            )

        mime_type = (
            file.content_type
            or mimetypes.guess_type(original_name)[0]
            or "application/octet-stream"
        )
        if mime_type == "image/jpg":
            mime_type = "image/jpeg"
        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=422, detail=f"MIME type '{mime_type}' is not allowed.")

        stored_name = f"{uuid.uuid4().hex}{ext}"
        upload_base = Path(request.app.state.uploads_root) / "8d"
        upload_base.mkdir(parents=True, exist_ok=True)

        dest = upload_base / stored_name
        dest.write_bytes(content)


        db_file = FileModel(
            purpose      ="evidence",
            original_name=original_name,
            stored_path  =stored_name,
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
            action_type   =resolved_action_type,   # ← new
            action_index  =resolved_action_index,  # ← new
            created_at    =datetime.now(timezone.utc),
        )
        db.add(step_file)
        db.flush()
        db.refresh(step_file)

        results.append(_serialize_file(step_file))

    # Sync evidence_documents for the base section
    if base_key in _EVIDENCE_SYNC_SECTIONS:
        svc = ConversationService(db)
        file_names = svc._get_step_file_names(step_id)
        svc._update_step_data(step_id, {"evidence_documents": ", ".join(file_names)})

    db.commit()

    return {
        "uploaded":  results,
        "filenames": [r["filename"] for r in results],
    }


@router.delete("/{step_id}/conversation/{section_key}", response_model=ConversationResponse)
def reset_conversation(step_id: int, section_key: str, db: Session = Depends(get_db)):
    ctx = _resolve_step_context(db, step_id)
    _require_section(section_key)
    svc = ConversationService(db)
    result = svc.reset_conversation(step_id, section_key)
    try:
        log_step_reopened_sync(db, ctx.complaint_id, ctx.report_id, step_id,
                               ctx.step_code, section_key=section_key,
                               performed_by_email=ctx.cqt_email)
        db.commit()
    except Exception as audit_exc:
        logger.warning("Failed to write step_reopened audit for step %s: %s",
                       step_id, audit_exc, exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
    return result


@router.get("/{step_id}/conversations")
def get_all_conversations(step_id: int, db: Session = Depends(get_db)):
    _resolve_step_context(db, step_id)
    svc = ConversationService(db)
    sections = svc.get_all_section_conversations(step_id)
    return {"step_id": step_id, "sections": sections}