"""
app/api/routes/step_files.py

Evidence file upload / list / delete / serve for 8D report steps.

Endpoints (all mounted under /api/v1/steps):
  POST   /{step_id}/files                         – upload one file (optionally scoped to an action)
  GET    /{step_id}/files                         – list all files for a step
  GET    /{step_id}/files?action_type=X&action_index=Y – list files for a specific action
  DELETE /{step_id}/files/{step_file_id}          – detach + delete file
  GET    /{step_id}/files/{step_file_id}/download – serve file content

Action scoping (D6 per-action evidence)
----------------------------------------
Pass action_type and action_index as query params on POST to scope a file
to a specific corrective action row:

  POST /api/v1/steps/42/files?action_type=occurrence&action_index=0
  POST /api/v1/steps/42/files?action_type=detection&action_index=2

Files uploaded without these params are "step-level" — existing behaviour.
"""

import hashlib
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.file import File as FileModel
from app.models.step_file import StepFile
from app.models.report_step import ReportStep

router = APIRouter()


# ─── Config ───────────────────────────────────────────────────────────────────

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/uploads/8d"))
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


# ─── Helpers ──────────────────────────────────────────────────────────────────

from pathlib import Path

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


def _serialize(sf: StepFile, request: Request) -> dict:
    f = sf.file
    base_url = str(request.base_url).rstrip("/")
    download_url = f"{base_url}/api/v1/steps/{sf.report_step_id}/files/{sf.id}/download"
    return {
        "id":           sf.id,
        "file_id":      f.id,
        "filename":     f.original_name,
        "url":          download_url,
        "mime_type":    f.mime_type or "application/octet-stream",
        "size_bytes":   f.size_bytes,
        "size_label":   _human_size(f.size_bytes),
        "icon":         _file_icon(f.mime_type or ""),
        "is_image":     (f.mime_type or "").startswith("image/"),
        "uploaded_at":  f.created_at.isoformat() if f.created_at else None,
        "checksum":     f.checksum,
        # Action scope — null for step-level files
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
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    # ── Action scope (optional) ──────────────────────────────────────────────
    # Pass these to link the file to a specific D6 corrective action.
    # Omit both for step-level files (D1-D5, D7-D8, or step-wide D6 files).
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
    """
    Upload a single evidence file and attach it to the step.

    For D6 per-action evidence, also pass ?action_type=occurrence&action_index=0
    (or detection / any valid index). Files without these params are step-level.
    """
    _get_step_or_404(step_id, db)

    # Validate that action_type and action_index are either both set or both absent
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

    # Save to disk
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = _upload_dir(request) / stored_name
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

    # Insert step_file join row (with optional action scope)
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

    return _serialize(step_file, request)


@router.get("/{step_id}/files")
def list_files(
    step_id: int,
    request: Request,
    db: Session = Depends(get_db),
    # ── Optional filters ──────────────────────────────────────────────────────
    action_type: ActionType | None = Query(
        default=None,
        description="Filter to files scoped to this action type",
    ),
    action_index: int | None = Query(
        default=None,
        ge=0,
        description="Filter to files scoped to this action index",
    ),
):
    """
    Return evidence files attached to a step.

    - No filter params  → all files for the step (step-level + all action files)
    - ?action_type=occurrence&action_index=0  → only files for that specific action
    - ?action_type=occurrence  → all occurrence action files (any index)
    """
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

    return [_serialize(sf, request) for sf in q.all()]


@router.delete("/{step_id}/files/{step_file_id}")
def delete_file(
    step_id: int,
    step_file_id: int,
    db: Session = Depends(get_db),
):
    """Delete a step_files row, the underlying files row, and the file on disk."""
    sf = (
        db.query(StepFile)
        .filter(StepFile.id == step_file_id, StepFile.report_step_id == step_id)
        .first()
    )
    if not sf:
        raise HTTPException(status_code=404, detail="File attachment not found")

    disk_path = Path(sf.file.stored_path)
    file_record = sf.file

    db.delete(sf)
    db.flush()
    db.delete(file_record)
    db.commit()

    if disk_path.exists():
        disk_path.unlink()

    return {"deleted": True, "step_file_id": step_file_id}


@router.get("/{step_id}/files/{step_file_id}/download")
def download_file(
    step_id: int,
    step_file_id: int,
    db: Session = Depends(get_db),
):
    """Serve / preview a file inline."""
    sf = (
        db.query(StepFile)
        .filter(StepFile.id == step_file_id, StepFile.report_step_id == step_id)
        .first()
    )
    if not sf:
        raise HTTPException(status_code=404, detail="File not found")

    disk_path = Path(sf.file.stored_path)
    if not disk_path.exists():
        raise HTTPException(status_code=404, detail="File missing from disk")

    return FileResponse(
        path      =str(disk_path),
        filename  =sf.file.original_name,
        media_type=sf.file.mime_type or "application/octet-stream",
    )



@router.post("/{step_id}/files/{step_file_id}/copy")
def copy_file_to_action(
    step_id: int,
    step_file_id: int,
    action_type: ActionType = Query(...),
    action_index: int = Query(..., ge=0),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Copy an existing file attachment to a different action scope.
    Creates a NEW StepFile row pointing at the same File record.
    Deletions are independent — deleting from Action A won't affect Action B.

    POST /api/v1/steps/42/files/7/copy?action_type=detection&action_index=1
    """
    _get_step_or_404(step_id, db)

    source = (
        db.query(StepFile)
        .filter(StepFile.id == step_file_id, StepFile.report_step_id == step_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Source file not found")

    # Check it won't violate the new unique constraint
    exists = (
        db.query(StepFile)
        .filter(
            StepFile.report_step_id == step_id,
            StepFile.file_id == source.file_id,
            StepFile.action_type == action_type,
            StepFile.action_index == action_index,
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
        file_id=source.file_id,   # same underlying File — no re-upload
        action_type=action_type,
        action_index=action_index,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_sf)
    db.commit()
    db.refresh(new_sf)
    return _serialize(new_sf, request)