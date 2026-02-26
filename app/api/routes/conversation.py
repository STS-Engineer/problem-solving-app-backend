# app/routers/conversations.py
"""
Conversation endpoints for interactive chatbot coaching.

GET    /api/v1/steps/{step_id}/conversation/{section_key}
POST   /api/v1/steps/{step_id}/conversation/{section_key}
POST   /api/v1/steps/{step_id}/conversation/{section_key}/upload  ← NEW
DELETE /api/v1/steps/{step_id}/conversation/{section_key}
GET    /api/v1/steps/{step_id}/conversations
"""

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

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# File upload config (mirrors step_files router)
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


VALID_SECTION_KEYS = {
    # D1
    "team_members",
    # D2
    "five_w_2h", "deviation", "is_is_not",
    # Future D3–D8
    "containment", "root_cause", "corrective_actions",
    "implementation", "prevention", "closure",
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
    # Filenames of files that were just uploaded via the /upload endpoint
    # in the same "send" action.  The service injects them into the AI context.
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
    # Allow empty text message when files are being referenced
    if not body.message.strip() and not body.uploaded_file_names:
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    _require_step(step_id, db)
    _require_section(section_key)

    kb = KnowledgeBaseRetriever(db)
    complaint_context = kb.get_complaint_context(step_id)

    svc = ConversationService(db)
    try:
        return svc.send_message(
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

    Returns a list of file records (same shape as the step_files router).
    """
    _require_step(step_id, db)
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

        # Save to disk
        stored_name = f"{uuid.uuid4().hex}{ext}"
        dest = _upload_dir() / stored_name
        dest.write_bytes(content)

        # Insert file record
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

        # Link to step
        step_file = StepFile(
            report_step_id=step_id,
            file_id       =db_file.id,
            created_at    =datetime.now(timezone.utc),
        )
        db.add(step_file)
        db.flush()
        db.refresh(step_file)

        results.append(_serialize_file(step_file))

    db.commit()
    # After db.commit() in upload_conversation_files:
    if section_key == "deviation":
        from app.services.conversation_service import ConversationService
        svc = ConversationService(db)
        file_names = svc._get_step_file_names(step_id)
        svc._update_step_data(step_id, {"evidence_documents": ", ".join(file_names)})
    return {
        "uploaded": results,
        "filenames": [r["filename"] for r in results],
    }


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