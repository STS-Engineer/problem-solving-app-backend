"""
app/services/intake_attachments.py

Download email attachments referenced by the MCP intake, store them in Azure
Blob, and create File rows linked to the intake.

Flow (per attachment):
  download_url (short-lived MS Graph signed URL)  → GET bytes
  → validate size/type → upload to Blob (folder intake/{id})
  → create File(purpose='evidence', intake_id, source='email_intake')

Robustness: a failing attachment never breaks the intake. It is recorded in the
returned metadata with status='fetch_failed' | 'rejected' | 'skipped_inline' so
the review UI can show what happened; the intake keeps its extracted_data.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.models.file import File
from app.services import blob_storage

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 30  # seconds


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def process_intake_attachments(
    db: Session, intake_id: int, attachments: list[Any]
) -> list[dict]:
    """
    Process the raw attachment descriptors from the payload. Returns a list of
    enriched metadata dicts (safe to store on intake.attachments). Creates File
    rows for the ones successfully stored. Never raises for a single bad file.

    `attachments` items are AttachmentIn model dumps (dicts).
    """
    results: list[dict] = []

    for raw in attachments or []:
        item = dict(raw) if isinstance(raw, dict) else raw.model_dump()
        filename = item.get("filename") or "file"
        meta: dict = {
            "filename": filename,
            "mime_type": item.get("mime_type"),
            "size": item.get("size"),
            "description": item.get("description"),
            "is_inline": bool(item.get("is_inline")),
            "content_id": item.get("content_id"),
            "sha256": item.get("sha256"),
        }

        # Skip inline images (signatures / logos) by default.
        if meta["is_inline"]:
            meta["status"] = "skipped_inline"
            results.append(meta)
            continue

        # Preserve non-file follow-up markers untouched (see EmailIntakeService.ingest).
        if item.get("type") == "followup_email":
            results.append(item)
            continue

        source_url = item.get("download_url") or item.get("url")
        if not source_url:
            meta["status"] = "no_source"
            results.append(meta)
            logger.warning("intake %s: attachment %r has no URL", intake_id, filename)
            continue

        # 1. Download
        try:
            resp = requests.get(source_url, timeout=_DOWNLOAD_TIMEOUT)
            resp.raise_for_status()
            content = resp.content
        except Exception as exc:
            meta["status"] = "fetch_failed"
            meta["error"] = str(exc)[:300]
            results.append(meta)
            logger.warning(
                "intake %s: failed to download attachment %r: %s",
                intake_id,
                filename,
                exc,
            )
            continue

        # 2. Integrity check (best-effort — only if the agent supplied a hash)
        expected = (item.get("sha256") or "").strip().lower()
        actual = _sha256(content)
        if expected and expected != actual:
            meta["status"] = "checksum_mismatch"
            meta["error"] = f"expected {expected[:12]}…, got {actual[:12]}…"
            results.append(meta)
            logger.warning(
                "intake %s: checksum mismatch for %r", intake_id, filename
            )
            continue
        meta["sha256"] = actual

        # 3. Upload to Blob + create File row
        try:
            uploaded = blob_storage.upload_bytes(
                content=content,
                original_name=filename,
                folder=f"intake/{intake_id}",
                prefix=f"intake_{intake_id}",
                mime_type=item.get("mime_type"),
            )
        except Exception as exc:
            meta["status"] = "rejected"
            meta["error"] = str(getattr(exc, "detail", exc))[:300]
            results.append(meta)
            logger.warning(
                "intake %s: attachment %r rejected on upload: %s",
                intake_id,
                filename,
                exc,
            )
            continue

        file_row = File(
            purpose="evidence",
            original_name=uploaded["filename"],
            stored_path=uploaded["blob_name"],
            size_bytes=uploaded["size"],
            mime_type=uploaded["mimetype"],
            uploaded_by=None,
            source="email_intake",
            intake_id=intake_id,
            description=meta.get("description"),
            checksum=actual,
        )
        db.add(file_row)
        db.flush()  # get file_row.id

        meta.update(
            {
                "status": "stored",
                "file_id": file_row.id,
                "blob_name": uploaded["blob_name"],
                "url": uploaded["file_url"],  # inline: preview in browser
                "download_url": blob_storage.get_blob_download_url(
                    uploaded["blob_name"], uploaded["filename"]
                ),  # forces a download
                "mime_type": uploaded["mimetype"],
                "size": uploaded["size"],
            }
        )
        results.append(meta)
        logger.info(
            "intake %s: stored attachment %r (file_id=%s, %d bytes)",
            intake_id,
            filename,
            file_row.id,
            uploaded["size"],
        )

    return results


def link_intake_files_to_complaint(
    db: Session, intake_id: int, complaint_id: int
) -> int:
    """
    On promotion, attach the intake's stored files to the complaint.
    Returns the number of files linked.
    """
    updated = (
        db.query(File)
        .filter(File.intake_id == intake_id, File.complaint_id.is_(None))
        .update({File.complaint_id: complaint_id}, synchronize_session=False)
    )
    if updated:
        logger.info(
            "intake %s: linked %d file(s) to complaint %s",
            intake_id,
            updated,
            complaint_id,
        )
    return updated
