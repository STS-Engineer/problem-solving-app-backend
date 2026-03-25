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
from app.services.file_storage import storage
from app.services.plan_push_service import PlanPushService

logger = logging.getLogger(__name__)

router = APIRouter()

# ── File upload config ────────────────────────────────────────────────────────

MAX_SIZE_BYTES = 25 * 1024 * 1024
SYSTEM_USER_ID: int = int(os.environ.get("SYSTEM_USER_ID", "1"))

ALLOWED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".pdf",
}
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "application/pdf",
}

_EVIDENCE_SYNC_SECTIONS = {"deviation", "implementation", "lessons_learned"}


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
        "id": sf.id,
        "file_id": f.id,
        "filename": f.original_name,
        "url": storage.url_for(f.stored_path),
        "mime_type": f.mime_type or "application/octet-stream",
        "size_bytes": f.size_bytes,
        "size_label": _human_size(f.size_bytes),
        "icon": _file_icon(f.mime_type or ""),
        "is_image": (f.mime_type or "").startswith("image/"),
        "uploaded_at": f.created_at.isoformat() if f.created_at else None,
        "checksum": f.checksum,
        "action_type": sf.action_type,
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
    from app.models.report_step import ReportStep
    from app.models.report import Report
    from app.models.complaint import Complaint

    step = (
        db.query(ReportStep, Report, Complaint)
        .join(Report, Report.id == ReportStep.report_id)
        .join(Complaint, Complaint.id == Report.complaint_id)
        .filter(ReportStep.id == step_id)
        .first()
    )
    if not step:
        raise HTTPException(status_code=404, detail=f"Step {step_id} not found")
    rs, r, c = step
    return _StepContext(
        complaint_id=c.id, report_id=r.id, step_code=rs.step_code, cqt_email=c.cqt_email
    )


# ── Guards ────────────────────────────────────────────────────────────────────

VALID_SECTION_KEYS = {
    "team_members",
    "five_w_2h",
    "deviation",
    "is_is_not",
    "containment",
    "restart",
    "four_m_occurrence",
    "four_m_non_detection",
    "corrective_occurrence",
    "corrective_detection",
    "implementation",
    "monitoring_checklist",
    "prevention",
    "knowledge",
    "lessons_learned",
    "closure",
    "root_cause",
    "corrective_actions",
}

VALID_ACTION_TYPES = {"occurrence", "detection", "lesson"}


def _require_section(section_key: str) -> None:
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
    parts = section_key.split(":")
    base_key = parts[0]

    enc_action_type: str | None = None
    enc_action_index: int | None = None
    if len(parts) == 3:
        enc_action_type = parts[1] if parts[1] in VALID_ACTION_TYPES else None
        try:
            enc_action_index = int(parts[2])
        except (ValueError, IndexError):
            enc_action_index = None

    action_type = (
        action_type_param if action_type_param is not None else enc_action_type
    )
    action_index = (
        action_index_param if action_index_param is not None else enc_action_index
    )

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
    action_type: str | None = None
    action_index: int | None = None


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
        step_id,
        section_key,
        complaint_context=complaint_context or None,
        kb_coaching=kb_coaching,
        twenty_rules=twenty_rules,
    )


@router.post(
    "/{step_id}/conversation/{section_key}", response_model=SendMessageResponse
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
            twenty_rules=twenty_rules,
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
            first_fill = _is_first_fill(previous_state, new_state)
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
            db.commit()
            # if new_state == "fulfilled" and section_key == "implementation":
            #     try:
            #         PlanPushService(db).push_on_d6_fulfilled(
            #             step_id=step_id,
            #             cqt_email=ctx.cqt_email,
            #         )
            #     except Exception as push_exc:
            #         logger.warning("plan_push: hook error for step %s: %s", step_id, push_exc, exc_info=True)

            # return response
        except Exception as audit_exc:
            logger.warning(
                "Failed to write audit log for step %s: %s",
                step_id,
                audit_exc,
                exc_info=True,
            )
            try:
                db.rollback()
            except Exception:
                pass
    else:
        try:
            db.commit()
            # if new_state == "fulfilled" and section_key == "implementation":
            #     try:
            #         PlanPushService(db).push_on_d6_fulfilled(
            #             step_id=step_id,
            #             cqt_email=ctx.cqt_email,
            #         )
            #     except Exception as push_exc:
            #         logger.warning("plan_push: hook error for step %s: %s", step_id, push_exc, exc_info=True)

            # return response
        except Exception:
            logger.warning(
                "Failed to commit conversation update for step %s",
                step_id,
                exc_info=True,
            )
            try:
                db.rollback()
            except Exception:
                pass

    return response


@router.post("/{step_id}/conversation/{section_key}/upload")
async def upload_conversation_files(
    step_id: int,
    section_key: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    action_type: str | None = Query(default=None),
    action_index: int | None = Query(default=None, ge=0),
):
    """Upload evidence file(s) from the ChatCoach panel to GitHub."""
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
            raise HTTPException(
                status_code=422, detail=f"File '{original_name}' is empty."
            )
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
            raise HTTPException(
                status_code=422, detail=f"MIME type '{mime_type}' is not allowed."
            )

        # ── Upload to GitHub ──────────────────────────────────────────────────
        try:
            result = await storage.upload(
                content=content,
                original_name=original_name,
                mime_type=mime_type,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"GitHub upload failed: {exc}")

        # ── Persist to DB ─────────────────────────────────────────────────────
        db_file = FileModel(
            purpose="evidence",
            original_name=original_name,
            stored_path=result["stored_name"],
            size_bytes=len(content),
            mime_type=mime_type,
            uploaded_by=SYSTEM_USER_ID,
            checksum=_sha256(content),
            created_at=datetime.now(timezone.utc),
        )
        db.add(db_file)
        db.flush()

        step_file = StepFile(
            report_step_id=step_id,
            file_id=db_file.id,
            action_type=resolved_action_type,
            action_index=resolved_action_index,
            created_at=datetime.now(timezone.utc),
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
        "uploaded": results,
        "filenames": [r["filename"] for r in results],
    }


@router.delete(
    "/{step_id}/conversation/{section_key}", response_model=ConversationResponse
)
def reset_conversation(step_id: int, section_key: str, db: Session = Depends(get_db)):
    ctx = _resolve_step_context(db, step_id)
    _require_section(section_key)
    svc = ConversationService(db)
    result = svc.reset_conversation(step_id, section_key)
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
            step_id,
            audit_exc,
            exc_info=True,
        )
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
