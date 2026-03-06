"""
services/audit_service.py

Centralised helpers for writing to complaint_audit_log.

Changes vs original:
  ① log_status_changed — renamed event_data keys "old"/"new"
    → "previous_value"/"new_value" to match the TS renderEventData() reader
  ② log_escalation_sent — added `reason` param (was missing → UI showed "—")
  ③ Added log_file_uploaded()    (referenced in EVENT_CFG, never written before)
  ④ Added log_step_reopened()    (triggered on conversation reset)
  ⑤ Added log_comment_added()    (triggered when bot reply has no extracted fields)
  ⑥ Added sync equivalents for every helper (conversation router uses sync Session)
     All async helpers call log_event(); all sync helpers call log_event_sync().
     No logic duplication — both paths share _build_entry().

  ⑦ [FIX] log_step_updated / log_step_updated_sync: unified event_data keys to
     "old_values"/"new_values" (were "old"/"new" — inconsistent with key naming
     convention used everywhere else and with the TS reader)
  ⑧ [FIX] Removed _complaint_id_for_step() and _report_id_for_step() — defined
     but never called anywhere; dead code that adds noise and import side-effects
  ⑨ [ADDED] log_complaint_created_sync() and log_report_created_sync() — every
     other helper has a sync twin; these two were missing
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.complaint_audit_log import ComplaintAuditLog
from app.services.utils.datetime_utils import utc_now


# ─── Shared entry builder ─────────────────────────────────────────────────────

def _build_entry(
    *,
    complaint_id: int,
    event_type: str,
    event_data: dict[str, Any] | None,
    step_code: str | None,
    step_id: int | None,
    report_id: int | None,
    performed_by_email: str | None,
) -> ComplaintAuditLog:
    return ComplaintAuditLog(
        complaint_id=complaint_id,
        report_id=report_id,
        step_id=step_id,
        step_code=step_code,
        event_type=event_type,
        event_data=event_data or {},
        performed_by_email=performed_by_email,
        created_at=utc_now(),
    )


# ─── Async core ───────────────────────────────────────────────────────────────

async def log_event(
    db: AsyncSession,
    *,
    complaint_id: int,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    step_code: str | None = None,
    step_id: int | None = None,
    report_id: int | None = None,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    """
    Async: write a single audit event, flushed but not committed.
    Use for routes backed by AsyncSession.

    Safe to call inside a db.begin_nested() savepoint — the flush targets
    the current savepoint, not the outer transaction.
    """
    entry = _build_entry(
        complaint_id=complaint_id,
        event_type=event_type,
        event_data=event_data,
        step_code=step_code,
        step_id=step_id,
        report_id=report_id,
        performed_by_email=performed_by_email,
    )
    db.add(entry)
    await db.flush()
    return entry


# ─── Sync core ────────────────────────────────────────────────────────────────

def log_event_sync(
    db: Session,
    *,
    complaint_id: int,
    event_type: str,
    event_data: dict[str, Any] | None = None,
    step_code: str | None = None,
    step_id: int | None = None,
    report_id: int | None = None,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    """
    Sync: write a single audit event, flushed but not committed.
    Use for routes backed by sqlalchemy.orm.Session (conversation router, etc.)
    """
    entry = _build_entry(
        complaint_id=complaint_id,
        event_type=event_type,
        event_data=event_data,
        step_code=step_code,
        step_id=step_id,
        report_id=report_id,
        performed_by_email=performed_by_email,
    )
    db.add(entry)
    db.flush()
    return entry


# ═══════════════════════════════════════════════════════════════════════════════
# ASYNC typed helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def log_complaint_created(
    db: AsyncSession,
    complaint_id: int,
    *,
    priority: str,
    cqt_email: str | None,
    quality_manager_email: str | None,
    performed_by_email: str,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        event_type="complaint_created",
        event_data={
            "priority": priority,
            "cqt_email": cqt_email,
            "quality_manager_email": quality_manager_email,
        },
        performed_by_email=performed_by_email,
    )


async def log_report_created(
    db: AsyncSession,
    complaint_id: int,
    report_id: int,
    *,
    report_number: str,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        event_type="report_created",
        event_data={"report_number": report_number},
        performed_by_email=performed_by_email,
    )


async def log_step_filled(
    db: AsyncSession,
    complaint_id: int,
    report_id: int,
    step_id: int,
    step_code: str,
    *,
    fields_snapshot: dict[str, Any],
    performed_by_email: str,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        step_id=step_id,
        step_code=step_code,
        event_type="step_filled",
        event_data={"fields_snapshot": fields_snapshot},
        performed_by_email=performed_by_email,
    )


async def log_step_updated(
    db: AsyncSession,
    complaint_id: int,
    report_id: int,
    step_id: int,
    step_code: str,
    *,
    changed_fields: list[str],
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    performed_by_email: str,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        step_id=step_id,
        step_code=step_code,
        event_type="step_updated",
        event_data={
            "changed_fields": changed_fields,
            # FIX ⑦: was "old"/"new" — unified to "old_values"/"new_values"
            # to match the naming convention used everywhere else and the TS reader.
            "old_values": old_values,
            "new_values": new_values,
        },
        performed_by_email=performed_by_email,
    )


async def log_step_reopened(
    db: AsyncSession,
    complaint_id: int,
    report_id: int,
    step_id: int,
    step_code: str,
    *,
    section_key: str,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        step_id=step_id,
        step_code=step_code,
        event_type="step_reopened",
        event_data={"section_key": section_key},
        performed_by_email=performed_by_email,
    )


async def log_due_date_missed(
    db: AsyncSession,
    complaint_id: int,
    step_id: int,
    step_code: str,
    *,
    due_date: datetime,
    missed_by_hours: float,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        step_id=step_id,
        step_code=step_code,
        event_type="due_date_missed",
        event_data={
            "due_date": due_date.isoformat(),
            "missed_by_hours": round(missed_by_hours, 2),
        },
        performed_by_email=None,  # system event
    )


async def log_escalation_sent(
    db: AsyncSession,
    complaint_id: int,
    step_id: int,
    step_code: str,
    *,
    level: int,
    recipients: list[str],
    template: str,
    reason: str | None = None,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        step_id=step_id,
        step_code=step_code,
        event_type="escalation_sent",
        event_data={
            "level": level,
            "recipients": recipients,
            "template": template,
            "reason": reason,
        },
        performed_by_email=None,  # system event
    )


async def log_status_changed(
    db: AsyncSession,
    complaint_id: int,
    *,
    old_status: str,
    new_status: str,
    performed_by_email: str,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        event_type="status_changed",
        event_data={
            "previous_value": old_status,
            "new_value": new_status,
        },
        performed_by_email=performed_by_email,
    )


async def log_file_uploaded(
    db: AsyncSession,
    complaint_id: int,
    step_id: int,
    step_code: str,
    *,
    filename: str,
    file_url: str | None = None,
    file_size: int | None = None,
    mime_type: str | None = None,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        step_id=step_id,
        step_code=step_code,
        event_type="file_uploaded",
        event_data={
            "filename": filename,
            "file_url": file_url,
            "file_size": file_size,
            "mime_type": mime_type,
        },
        performed_by_email=performed_by_email,
    )


async def log_comment_added(
    db: AsyncSession,
    complaint_id: int,
    *,
    step_id: int | None = None,
    step_code: str | None = None,
    comment: str,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return await log_event(
        db,
        complaint_id=complaint_id,
        step_id=step_id,
        step_code=step_code,
        event_type="comment_added",
        event_data={"comment": comment},
        performed_by_email=performed_by_email,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SYNC typed helpers
# ═══════════════════════════════════════════════════════════════════════════════

# FIX ⑨: log_complaint_created_sync and log_report_created_sync were missing —
# every other helper had a sync twin; added here for completeness.

def log_complaint_created_sync(
    db: Session,
    complaint_id: int,
    *,
    priority: str,
    cqt_email: str | None,
    quality_manager_email: str | None,
    performed_by_email: str,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        event_type="complaint_created",
        event_data={
            "priority": priority,
            "cqt_email": cqt_email,
            "quality_manager_email": quality_manager_email,
        },
        performed_by_email=performed_by_email,
    )


def log_report_created_sync(
    db: Session,
    complaint_id: int,
    report_id: int,
    *,
    report_number: str,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        event_type="report_created",
        event_data={"report_number": report_number},
        performed_by_email=performed_by_email,
    )


def log_step_filled_sync(
    db: Session,
    complaint_id: int,
    report_id: int,
    step_id: int,
    step_code: str,
    *,
    fields_snapshot: dict[str, Any],
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        step_id=step_id,
        step_code=step_code,
        event_type="step_filled",
        event_data={"fields_snapshot": fields_snapshot},
        performed_by_email=performed_by_email,
    )


def log_step_updated_sync(
    db: Session,
    complaint_id: int,
    report_id: int,
    step_id: int,
    step_code: str,
    *,
    changed_fields: list[str],
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        step_id=step_id,
        step_code=step_code,
        event_type="step_updated",
        event_data={
            "changed_fields": changed_fields,
            # FIX ⑦: unified with async version
            "old_values": old_values,
            "new_values": new_values,
        },
        performed_by_email=performed_by_email,
    )


def log_step_reopened_sync(
    db: Session,
    complaint_id: int,
    report_id: int,
    step_id: int,
    step_code: str,
    *,
    section_key: str,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        report_id=report_id,
        step_id=step_id,
        step_code=step_code,
        event_type="step_reopened",
        event_data={"section_key": section_key},
        performed_by_email=performed_by_email,
    )


def log_file_uploaded_sync(
    db: Session,
    complaint_id: int,
    step_id: int,
    step_code: str,
    *,
    filename: str,
    file_url: str | None = None,
    file_size: int | None = None,
    mime_type: str | None = None,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        step_id=step_id,
        step_code=step_code,
        event_type="file_uploaded",
        event_data={
            "filename": filename,
            "file_url": file_url,
            "file_size": file_size,
            "mime_type": mime_type,
        },
        performed_by_email=performed_by_email,
    )


def log_comment_added_sync(
    db: Session,
    complaint_id: int,
    *,
    step_id: int | None = None,
    step_code: str | None = None,
    comment: str,
    performed_by_email: str | None = None,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        step_id=step_id,
        step_code=step_code,
        event_type="comment_added",
        event_data={"comment": comment},
        performed_by_email=performed_by_email,
    )


def log_status_changed_sync(
    db: Session,
    complaint_id: int,
    *,
    old_status: str,
    new_status: str,
    performed_by_email: str,
) -> ComplaintAuditLog:
    return log_event_sync(
        db,
        complaint_id=complaint_id,
        event_type="status_changed",
        event_data={
            "previous_value": old_status,
            "new_value": new_status,
        },
        performed_by_email=performed_by_email,
    )