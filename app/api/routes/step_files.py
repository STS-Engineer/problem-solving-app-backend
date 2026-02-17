# app/api/routes/step_files.py
"""
Evidence file upload / list / delete / serve for 8D report steps.

Uses your existing two-table schema:
  files      — stores the actual file record (path, size, mime, checksum)
  step_files — join table linking a file to a step

Auth: uploaded_by is nullable=False in the DB.
Until real auth exists we use a SYSTEM_USER_ID placeholder.

Run this once to create the system user row (adjust to match your users table):
    INSERT INTO users (id, email, name)
    VALUES (1, 'system@internal', 'System')
    ON CONFLICT (id) DO NOTHING;

Then set env var:  SYSTEM_USER_ID=1  (default 1)

Endpoints (all mounted under /api/v1/steps):
  POST   /{step_id}/files                        – upload one file
  GET    /{step_id}/files                        – list files for step
  DELETE /{step_id}/files/{step_file_id}         – detach + delete file
  GET    /{step_id}/files/{step_file_id}/download – serve file content
"""

import hashlib
import mimetypes
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.file import File as FileModel
from app.models.step_file import StepFile
from app.models.report_step import ReportStep

router = APIRouter()

# Placeholder user ID until auth is wired up.
# Set SYSTEM_USER_ID env var to match the id of a real row in your users table.
SYSTEM_USER_ID: int = int(os.environ.get("SYSTEM_USER_ID", "1"))

# ─── Config ───────────────────────────────────────────────────────────────────

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./uploads/evidence"))
MAX_SIZE_BYTES = 25 * 1024 * 1024   # 25 MB

# Only images + PDFs as requested
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


# ─── Helpers ──────────────────────────────────────────────────────────────────

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


def _serialize(sf: StepFile) -> dict:
    f = sf.file
    return {
        "id":           sf.id,           # step_files.id (used for delete)
        "file_id":      f.id,            # files.id
        "filename":     f.original_name,
        "stored_path":  f.stored_path,
        "mime_type":    f.mime_type or "application/octet-stream",
        "size_bytes":   f.size_bytes,
        "size_label":   _human_size(f.size_bytes),
        "icon":         _file_icon(f.mime_type or ""),
        "is_image":     (f.mime_type or "").startswith("image/"),
        "uploaded_at":  f.created_at.isoformat() if f.created_at else None,
        "checksum":     f.checksum,
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
):
    """
    Upload a single evidence file (image or PDF) and attach it to the step.
    Creates one row in `files` and one row in `step_files`.
    """
    _get_step_or_404(step_id, db)

    original_name = file.filename or "unnamed"
    ext = Path(original_name).suffix.lower()

    # ── Validate extension ─────────────────────────────────────────────────
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"File type '{ext}' not allowed. Accepted: images (jpg, png, gif, webp, bmp, tiff) and PDF.",
        )

    # ── Read content ───────────────────────────────────────────────────────
    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({_human_size(len(content))}). Max 25 MB.",
        )

    # ── Detect MIME ────────────────────────────────────────────────────────
    mime_type = (
        file.content_type
        or mimetypes.guess_type(original_name)[0]
        or "application/octet-stream"
    )
    # Normalise: some browsers send image/jpg instead of image/jpeg
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"

    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"MIME type '{mime_type}' is not allowed.",
        )

    # ── Save to disk ───────────────────────────────────────────────────────
    stored_name = f"{uuid.uuid4().hex}{ext}"
    dest = _upload_dir() / stored_name
    dest.write_bytes(content)

    # ── Insert into `files` table ──────────────────────────────────────────
    # uploaded_by is nullable=False in your schema.
    # SYSTEM_USER_ID is a placeholder until real auth is wired up.
    # Replace with: current_user.id  once you have a logged-in user.
    db_file = FileModel(
        purpose       = "evidence",
        original_name = original_name,
        stored_path   = str(dest),
        size_bytes    = len(content),
        mime_type     = mime_type,
        uploaded_by   = SYSTEM_USER_ID,   # ← swap for current_user.id when auth ready
        checksum      = _sha256(content),
        created_at    = datetime.now(timezone.utc),
    )
    db.add(db_file)
    db.flush()   # get db_file.id before inserting step_files row

    # ── Insert into `step_files` join table ────────────────────────────────
    step_file = StepFile(
        report_step_id = step_id,
        file_id        = db_file.id,
        created_at     = datetime.now(timezone.utc),
    )
    db.add(step_file)
    db.commit()
    db.refresh(step_file)

    return _serialize(step_file)


@router.get("/{step_id}/files")
def list_files(
    step_id: int,
    db: Session = Depends(get_db),
):
    """Return all evidence files attached to a step."""
    _get_step_or_404(step_id, db)

    step_files = (
        db.query(StepFile)
        .filter(StepFile.report_step_id == step_id)
        .join(StepFile.file)
        .order_by(FileModel.created_at)
        .all()
    )
    return [_serialize(sf) for sf in step_files]


@router.delete("/{step_id}/files/{step_file_id}")
def delete_file(
    step_id: int,
    step_file_id: int,
    db: Session = Depends(get_db),
):
    """
    Delete a step_files row, the underlying files row, and the file on disk.
    Uses step_file_id (step_files.id), not files.id.
    """
    sf = (
        db.query(StepFile)
        .filter(
            StepFile.id == step_file_id,
            StepFile.report_step_id == step_id,
        )
        .first()
    )
    if not sf:
        raise HTTPException(status_code=404, detail="File attachment not found")

    # Remove from disk first (before DB so we don't leave orphan files if DB fails)
    disk_path = Path(sf.file.stored_path)
    file_record = sf.file   # capture reference before deleting join row

    # Step 1: delete the join row (step_files)
    db.delete(sf)
    db.flush()

    # Step 2: delete the file record (files table)
    # The File model has cascade="all, delete-orphan" on step_files,
    # meaning SQLAlchemy manages StepFile children when File is deleted.
    # But since we already removed the StepFile row above, we delete File directly.
    db.delete(file_record)
    db.commit()

    # Remove from disk after successful DB commit
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
        .filter(
            StepFile.id == step_file_id,
            StepFile.report_step_id == step_id,
        )
        .first()
    )
    if not sf:
        raise HTTPException(status_code=404, detail="File not found")

    disk_path = Path(sf.file.stored_path)
    if not disk_path.exists():
        raise HTTPException(status_code=404, detail="File missing from disk")

    return FileResponse(
        path        = str(disk_path),
        filename    = sf.file.original_name,
        media_type  = sf.file.mime_type or "application/octet-stream",
    )