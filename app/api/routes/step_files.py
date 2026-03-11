"""
app/api/routes/step_files.py

Evidence file upload / list / delete / serve for 8D report steps.
Files are stored in the GitHub repository via FileStorageService.

Endpoints (all mounted under /api/v1/steps):
  POST   /{step_id}/files                         – upload one file
  GET    /{step_id}/files                         – list all files for a step
  GET    /{step_id}/files?action_type=X&action_index=Y – list files for a specific action
  DELETE /{step_id}/files/{step_file_id}          – detach + delete file
  GET    /{step_id}/files/{step_file_id}/download – redirect to raw GitHub URL
"""

import hashlib
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.file import File as FileModel
from app.models.step_file import StepFile
from app.models.report_step import ReportStep
from app.services.file_storage import storage

router = APIRouter()


# ─── Config ───────────────────────────────────────────────────────────────────
MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
SYSTEM_USER_ID: int = int(os.environ.get("SYSTEM_USER_ID", "1"))

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif",
    "image/webp", "image/bmp", "image/tiff",
    "application/pdf",
}
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif",
    ".webp", ".bmp", ".tif", ".tiff",
    ".pdf",
}

ActionType = Literal["occurrence", "detection"]


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


def _serialize(sf: StepFile) -> dict:
    f = sf.file
    return {
        "id":           sf.id,
        "file_id":      f.id,
        "filename":     f.original_name,
        "url":          storage.url_for(f.stored_path),
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


def _get_step_or_404(step_id: int, db: Session) -> ReportStep:
    step = db.query(ReportStep).filter(ReportStep.id == step_id).first()
    if not step:
        raise HTTPException(status_code=404, detail="Step not found")
    return step


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/{step_id}/files")
async def upload_file(
    step_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    action_type: ActionType | None = Query(
        default=None,
        description="'occurrence' or 'detection' — required when action_index is set",
    ),
    action_index: int | None = Query(
        default=None,
        ge=0,
        description="0-based index of the corrective action in the D6 action array",
    ),
):
    """Upload a single evidence file and attach it to the step."""
    _get_step_or_404(step_id, db)

    if (action_type is None) != (action_index is None):
        raise HTTPException(
            status_code=422,
            detail="action_type and action_index must be provided together or not at all.",
        )

    original_name = file.filename or "unnamed"
    ext = Path(original_name).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"File type '{ext}' not allowed. Accepted: images (jpg, png, gif, webp, bmp, tiff) and PDF.",
        )

    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({_human_size(len(content))}). Max 25 MB.",
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

    # ── Upload to GitHub ──────────────────────────────────────────────────────
    try:
        result = await storage.upload(
            content=content,
            original_name=original_name,
            mime_type=mime_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub upload failed: {exc}")

    # ── Persist to DB ─────────────────────────────────────────────────────────
    db_file = FileModel(
        purpose       ="evidence",
        original_name =original_name,
        stored_path   =result["stored_name"],   # uuid.ext — leaf name only
        size_bytes    =len(content),
        mime_type     =mime_type,
        uploaded_by   =SYSTEM_USER_ID,
        checksum      =_sha256(content),
        created_at    =datetime.now(timezone.utc),
    )
    db.add(db_file)
    db.flush()

    step_file = StepFile(
        report_step_id=step_id,
        file_id       =db_file.id,
        action_type   =action_type,
        action_index  =action_index,
        created_at    =datetime.now(timezone.utc),
    )
    db.add(step_file)
    db.commit()
    db.refresh(step_file)

    return _serialize(step_file)


@router.get("/{step_id}/files")
def list_files(
    step_id: int,
    db: Session = Depends(get_db),
    action_type: ActionType | None = Query(default=None),
    action_index: int | None = Query(default=None, ge=0),
):
    """Return evidence files attached to a step."""
    _get_step_or_404(step_id, db)

    q = (
        db.query(StepFile)
        .filter(StepFile.report_step_id == step_id)
        .join(StepFile.file)
        .order_by(FileModel.created_at)
    )

    if action_type is not None:
        q = q.filter(StepFile.action_type == action_type)
    if action_index is not None:
        q = q.filter(StepFile.action_index == action_index)

    return [_serialize(sf) for sf in q.all()]


@router.delete("/{step_id}/files/{step_file_id}")
async def delete_file(
    step_id: int,
    step_file_id: int,
    db: Session = Depends(get_db),
):
    """Delete a step_files row, the underlying files row, and the file on GitHub."""
    sf = (
        db.query(StepFile)
        .filter(StepFile.id == step_file_id, StepFile.report_step_id == step_id)
        .first()
    )
    if not sf:
        raise HTTPException(status_code=404, detail="File attachment not found")

    stored_name = sf.file.stored_path
    file_record = sf.file

    db.delete(sf)
    db.flush()
    db.delete(file_record)
    db.commit()

    # Best-effort GitHub deletion (don't fail the request if GitHub is slow)
    try:
        await storage.delete(stored_name)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "GitHub delete failed for %s: %s", stored_name, exc
        )

    return {"deleted": True, "step_file_id": step_file_id}


@router.get("/{step_id}/files/{step_file_id}/download")
async def download_file(
    step_id: int,
    step_file_id: int,
    db: Session = Depends(get_db),
):
    """
    Proxy the file content from the private GitHub repo to the browser.
    Supports both inline preview (images/PDF) and download.
    """
    sf = (
        db.query(StepFile)
        .filter(StepFile.id == step_file_id, StepFile.report_step_id == step_id)
        .first()
    )
    if not sf:
        raise HTTPException(status_code=404, detail="File not found")

    mime_type     = sf.file.mime_type or "application/octet-stream"
    original_name = sf.file.original_name
    stored_name   = sf.file.stored_path

    try:
        content = await storage.fetch_content(stored_name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File missing from GitHub")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub fetch failed: {exc}")

    # Images and PDFs open inline in the browser; everything else forces download
    is_previewable = mime_type.startswith("image/") or mime_type == "application/pdf"
    disposition    = "inline" if is_previewable else f'attachment; filename="{original_name}"'

    return Response(
        content=content,
        media_type=mime_type,
        headers={"Content-Disposition": disposition},
    )


@router.post("/{step_id}/files/{step_file_id}/copy")
def copy_file_to_action(
    step_id: int,
    step_file_id: int,
    action_type: ActionType = Query(...),
    action_index: int = Query(..., ge=0),
    db: Session = Depends(get_db),
):
    """
    Copy an existing file attachment to a different action scope.
    Creates a NEW StepFile row pointing at the same File record.
    No re-upload needed — the GitHub blob is shared.
    """
    _get_step_or_404(step_id, db)

    source = (
        db.query(StepFile)
        .filter(StepFile.id == step_file_id, StepFile.report_step_id == step_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source file not found")

    exists = (
        db.query(StepFile)
        .filter(
            StepFile.report_step_id == step_id,
            StepFile.file_id        == source.file_id,
            StepFile.action_type    == action_type,
            StepFile.action_index   == action_index,
        )
        .first()
    )
    if exists:
        raise HTTPException(
            status_code=409,
            detail="This file is already attached to that action.",
        )

    new_sf = StepFile(
        report_step_id=step_id,
        file_id       =source.file_id,
        action_type   =action_type,
        action_index  =action_index,
        created_at    =datetime.now(timezone.utc),
    )
    db.add(new_sf)
    db.commit()
    db.refresh(new_sf)
    return _serialize(new_sf)