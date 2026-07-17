from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    Depends,
    Query,
    HTTPException,
    UploadFile,
    File,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.services import blob_storage
from app.api.deps import get_async_db
from app.models.complaint import Complaint
from app.models.complaint_audit_log import ComplaintAuditLog
from app.models.report import Report
from app.models.report_step import ReportStep
from app.schemas.complaint_logger import (
    ComplaintLoggerListResponse,
    ComplaintLogItem,
    StepSummary,
    AuditLogEntry,
    ComplaintLogsResponse,
    StepLogsResponse,
    StepWithLogs,
    EscalationActionRequest,
    EscalationLevelTrack,
    EscalationTrackResponse,
    EscalationActionItem,
    EscalationActionsResponse,
    ESCALATION_ACTION_LABELS,
)
from app.services.audit_service import log_escalation_action

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── SLA table (calendar days → hours) ───────────────────────────────────────
# Must stay in sync with complaint_service._STEP_SLA_DAYS

_SLA_HOURS: dict[str, int] = {
    "D1": 24,
    "D2": 72,
    "D3": 24,
    "D4": 168,  # 7 days
    "D5": 336,  # 14 days
    "D6": 720,  # 30 days
    "D7": 1080,  # 45 days
    "D8": 1440,  # 60 days
}


def _sla_hours(code: str) -> int:
    return _SLA_HOURS.get(code, 24)


# ─── Serialisers ──────────────────────────────────────────────────────────────


def _build_step_summary(steps: list[ReportStep]) -> list[StepSummary]:
    return [
        StepSummary(
            code=s.step_code,
            name=s.step_name,
            status=s.status,
            due_date=s.due_date,
            completed_at=s.completed_at,
            completed_by_email=None,
            escalation_count=s.escalation_count or 0,
            cost=None,
            hours_allowed=_sla_hours(s.step_code),
            is_overdue=getattr(s, "is_overdue", False),
        )
        for s in sorted(steps, key=lambda x: x.step_code)
    ]


def _serialize_log(log: ComplaintAuditLog) -> AuditLogEntry:
    return AuditLogEntry(
        id=log.id,
        complaint_id=log.complaint_id,
        report_id=log.report_id,
        step_id=log.step_id,
        step_code=log.step_code,
        event_type=log.event_type,
        event_data=log.event_data,
        performed_by_email=log.performed_by_email,
        created_at=log.created_at,
    )


# ─── GET /logger ──────────────────────────────────────────────────────────────


@router.get("/", response_model=ComplaintLoggerListResponse)
async def list_complaints_for_logger(
    search: str | None = Query(None),
    status: str | None = Query(None),
    plant: str | None = Query(None),
    priority: str | None = Query(None),
    customer: str | None = Query(None),
    has_escalation: bool | None = Query(
        None, description="Filter complaints that have at least one escalation"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
) -> ComplaintLoggerListResponse:
    """
    Paginated complaint list with step summary and last-activity timestamp.

    Filters:
      - search: matches reference_number, complaint_name, customer
      - status: complaint status
      - plant: avocarbon_plant
      - priority: critical / high / medium / low
      - has_escalation: true → only complaints with ≥1 escalation sent
    """
    base_q = (
        select(Complaint)
        .options(
            selectinload(Complaint.report).selectinload(Report.steps),
            selectinload(Complaint.audit_logs),
        )
        .order_by(Complaint.created_at.desc())
    )

    if search:
        like = f"%{search}%"
        base_q = base_q.where(
            Complaint.reference_number.ilike(like)
            | Complaint.complaint_name.ilike(like)
            | Complaint.customer.ilike(like)
            | Complaint.customer.ilike(f"%{customer}%")
        )
    if status:
        base_q = base_q.where(Complaint.status == status)
    if plant:
        base_q = base_q.where(Complaint.avocarbon_plant == plant)
    if priority:
        base_q = base_q.where(Complaint.priority == priority)

    # Count before pagination
    count_result = await db.execute(select(func.count()).select_from(base_q.subquery()))
    total: int = count_result.scalar_one()

    paged_q = base_q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(paged_q)
    complaints: list[Complaint] = result.scalars().unique().all()

    items: list[ComplaintLogItem] = []
    for c in complaints:
        steps = c.report.steps if c.report else []
        last_log = (
            max((lg.created_at for lg in c.audit_logs), default=None)
            if c.audit_logs
            else None
        )
        total_esc = sum(s.escalation_count or 0 for s in steps)

        # Apply has_escalation filter post-load (simpler than a subquery join)
        if has_escalation is True and total_esc == 0:
            continue
        if has_escalation is False and total_esc > 0:
            continue

        items.append(
            ComplaintLogItem(
                id=c.id,
                reference_number=c.reference_number,
                complaint_name=c.complaint_name,
                customer=c.customer or "",
                plant=c.avocarbon_plant if c.avocarbon_plant else "",
                status=c.status,
                priority=c.priority,
                cqt_email=c.cqt_email,
                quality_manager_emails=c.quality_manager_emails or [],
                plant_manager_email=c.plant_manager_email,
                created_at=c.created_at,
                due_date=c.due_date,
                closed_at=c.closed_at,
                steps=_build_step_summary(steps),
                total_escalations=total_esc,
                last_activity=last_log,
            )
        )

    return ComplaintLoggerListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )


# ─── GET /escalation-actions ──────────────────────────────────────────────────


@router.get("/escalation-actions", response_model=EscalationActionsResponse)
async def list_escalation_actions(
    responsible: str | None = Query(
        None, description="Filter by the responder's email (case-insensitive contains)"
    ),
    resolved: bool | None = Query(
        None, description="Filter by whether the action marked the escalation resolved"
    ),
    search: str | None = Query(
        None, description="Matches complaint reference, name or customer"
    ),
    db: AsyncSession = Depends(get_async_db),
) -> EscalationActionsResponse:
    """
    Every recorded escalation action across all complaints, flattened with its
    complaint context and ordered newest-first.

    Powers the "By Responsible" view so a manager can see all escalation
    actions a person has taken without opening each complaint one by one.
    """
    q = (
        select(ComplaintAuditLog, Complaint)
        .join(Complaint, ComplaintAuditLog.complaint_id == Complaint.id)
        .where(ComplaintAuditLog.event_type == "escalation_action")
        .order_by(ComplaintAuditLog.created_at.desc())
    )

    if responsible:
        q = q.where(ComplaintAuditLog.performed_by_email.ilike(f"%{responsible}%"))
    if search:
        like = f"%{search}%"
        q = q.where(
            Complaint.reference_number.ilike(like)
            | Complaint.complaint_name.ilike(like)
            | Complaint.customer.ilike(like)
        )

    result = await db.execute(q)
    rows = result.all()

    items: list[EscalationActionItem] = []
    for log, complaint in rows:
        data = log.event_data or {}
        is_resolved = bool(data.get("resolved"))
        if resolved is not None and is_resolved != resolved:
            continue
        action_type = data.get("action_type")
        level = data.get("level")
        items.append(
            EscalationActionItem(
                log_id=log.id,
                complaint_id=complaint.id,
                reference_number=complaint.reference_number,
                complaint_name=complaint.complaint_name,
                customer=complaint.customer or "",
                plant=complaint.avocarbon_plant or "",
                priority=complaint.priority,
                complaint_status=complaint.status,
                step_code=log.step_code,
                level=level if isinstance(level, int) else None,
                action_type=action_type,
                action_label=(
                    ESCALATION_ACTION_LABELS.get(action_type, action_type)
                    if action_type
                    else None
                ),
                note=data.get("note"),
                resolved=is_resolved,
                attachment_url=data.get("attachment_url"),
                attachment_name=data.get("attachment_name"),
                performed_by_email=log.performed_by_email,
                created_at=log.created_at,
            )
        )

    return EscalationActionsResponse(items=items, total=len(items))


# ─── GET /{complaint_id}/logs ─────────────────────────────────────────────────


@router.get("/{complaint_id}/logs", response_model=ComplaintLogsResponse)
async def get_complaint_logs(
    complaint_id: int,
    step_code: str | None = Query(None),
    event_type: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
) -> ComplaintLogsResponse:
    """Full ordered audit log for a single complaint, optionally filtered."""

    c_result = await db.execute(select(Complaint).where(Complaint.id == complaint_id))
    complaint = c_result.scalar_one_or_none()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    q = (
        select(ComplaintAuditLog)
        .where(ComplaintAuditLog.complaint_id == complaint_id)
        .order_by(ComplaintAuditLog.created_at.desc())
    )
    if step_code:
        q = q.where(ComplaintAuditLog.step_code == step_code)
    if event_type:
        q = q.where(ComplaintAuditLog.event_type == event_type)

    result = await db.execute(q)
    logs = result.scalars().all()

    return ComplaintLogsResponse(
        complaint_id=complaint_id,
        reference_number=complaint.reference_number,
        logs=[_serialize_log(lg) for lg in logs],
        total=len(logs),
    )


# ─── GET /{complaint_id}/logs/steps ──────────────────────────────────────────


@router.get("/{complaint_id}/logs/steps", response_model=StepLogsResponse)
async def get_complaint_step_logs(
    complaint_id: int,
    db: AsyncSession = Depends(get_async_db),
) -> StepLogsResponse:
    """Each step (D1–D8) with metadata + all audit log entries for that step."""

    c_result = await db.execute(
        select(Complaint)
        .where(Complaint.id == complaint_id)
        .options(
            selectinload(Complaint.report).selectinload(Report.steps),
            selectinload(Complaint.audit_logs),
        )
    )
    complaint = c_result.scalar_one_or_none()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    steps = complaint.report.steps if complaint.report else []

    # Group logs by step_code (None → complaint-level)
    logs_by_step: dict[str | None, list[ComplaintAuditLog]] = {}
    for log in complaint.audit_logs:
        logs_by_step.setdefault(log.step_code, []).append(log)

    step_items: list[StepWithLogs] = []
    for step in sorted(steps, key=lambda s: s.step_code):
        step_logs = sorted(
            logs_by_step.get(step.step_code, []),
            key=lambda lg: lg.created_at,
            reverse=True,
        )
        step_items.append(
            StepWithLogs(
                step=StepSummary(
                    code=step.step_code,
                    name=step.step_name,
                    status=step.status,
                    due_date=step.due_date,
                    completed_at=step.completed_at,
                    completed_by_email=None,
                    escalation_count=step.escalation_count or 0,
                    cost=None,
                    hours_allowed=_sla_hours(step.step_code),
                    is_overdue=getattr(step, "is_overdue", False),
                ),
                logs=[_serialize_log(lg) for lg in step_logs],
            )
        )

    complaint_level_logs = sorted(
        logs_by_step.get(None, []),
        key=lambda lg: lg.created_at,
        reverse=True,
    )

    return StepLogsResponse(
        complaint_id=complaint_id,
        reference_number=complaint.reference_number,
        complaint_name=complaint.complaint_name,
        customer=complaint.customer or "",
        cqt_email=complaint.cqt_email,
        quality_manager_emails=complaint.quality_manager_emails or [],
        plant_manager_email=complaint.plant_manager_email,
        status=complaint.status,
        priority=complaint.priority,
        due_date=complaint.due_date,
        closed_at=complaint.closed_at,
        created_at=complaint.created_at,
        steps=step_items,
        complaint_level_logs=[_serialize_log(lg) for lg in complaint_level_logs],
    )


# ─── POST /{complaint_id}/escalation-action ───────────────────────────────────


@router.post("/{complaint_id}/escalation-action", response_model=AuditLogEntry)
async def record_escalation_action(
    complaint_id: int,
    payload: EscalationActionRequest,
    db: AsyncSession = Depends(get_async_db),
) -> AuditLogEntry:
    """
    Record what an L1/L2 responder did in response to an escalation email:
    called the responsible, reassigned the complaint, approved a purchase, etc.

    Stored as an `escalation_action` audit event so it shows up in the timeline
    and the escalation track alongside the `escalation_sent` event it answers.
    """
    c_result = await db.execute(
        select(Complaint)
        .where(Complaint.id == complaint_id)
        .options(selectinload(Complaint.report).selectinload(Report.steps))
    )
    complaint = c_result.scalar_one_or_none()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    steps = complaint.report.steps if complaint.report else []
    step = next((s for s in steps if s.step_code == payload.step_code), None)
    if step is None:
        raise HTTPException(
            status_code=404,
            detail=f"Step {payload.step_code} not found on this complaint",
        )

    entry = await log_escalation_action(
        db,
        complaint_id=complaint_id,
        step_id=step.id,
        step_code=step.step_code,
        level=payload.level,
        action_type=payload.action_type.value,
        performed_by_email=payload.performed_by_email,
        note=payload.note,
        resolved=payload.resolved,
        attachment_url=payload.attachment_url,
        attachment_name=payload.attachment_name,
        attachment_blob_name=payload.attachment_blob_name,
    )
    await db.commit()
    await db.refresh(entry)
    return _serialize_log(entry)


# ─── GET /{complaint_id}/escalation-track ─────────────────────────────────────


@router.get("/{complaint_id}/escalation-track", response_model=EscalationTrackResponse)
async def get_escalation_track(
    complaint_id: int,
    db: AsyncSession = Depends(get_async_db),
) -> EscalationTrackResponse:
    """
    Consolidated escalation track for the Owner and L2: one entry per
    (step, level) that was escalated, pairing the sent notification with the
    responder actions recorded against it.
    """
    c_result = await db.execute(
        select(Complaint)
        .where(Complaint.id == complaint_id)
        .options(selectinload(Complaint.audit_logs))
    )
    complaint = c_result.scalar_one_or_none()
    if not complaint:
        raise HTTPException(status_code=404, detail="Complaint not found")

    # Group sent + action events by (step_code, level).
    # groups[(step_code, level)] = {"sent": log|None, "actions": [log, ...]}
    groups: dict[tuple[str | None, int], dict] = {}

    def _bucket(step_code: str | None, level: int) -> dict:
        return groups.setdefault(
            (step_code, level), {"sent": None, "actions": []}
        )

    for log in complaint.audit_logs:
        level = (log.event_data or {}).get("level")
        if not isinstance(level, int):
            continue
        if log.event_type == "escalation_sent":
            b = _bucket(log.step_code, level)
            # keep the earliest sent event for this level
            if b["sent"] is None or log.created_at < b["sent"].created_at:
                b["sent"] = log
        elif log.event_type == "escalation_action":
            _bucket(log.step_code, level)["actions"].append(log)

    levels: list[EscalationLevelTrack] = []
    for (step_code, level) in sorted(groups, key=lambda k: (k[0] or "", k[1])):
        b = groups[(step_code, level)]
        sent_log = b["sent"]
        actions = sorted(b["actions"], key=lambda lg: lg.created_at)
        resolved = any((a.event_data or {}).get("resolved") for a in actions)
        levels.append(
            EscalationLevelTrack(
                level=level,
                step_code=step_code or "",
                sent_at=sent_log.created_at if sent_log else None,
                recipients=(
                    (sent_log.event_data or {}).get("recipients", [])
                    if sent_log
                    else []
                ),
                actions=[_serialize_log(a) for a in actions],
                resolved=resolved,
            )
        )

    return EscalationTrackResponse(
        complaint_id=complaint_id,
        reference_number=complaint.reference_number,
        complaint_name=complaint.complaint_name,
        levels=levels,
    )


# ─── Escalation-action attachments (Azure Blob Storage) ───────────────────────
#
# Files are uploaded to Azure Blob Storage via app.services.blob_storage.
# The upload returns a long-lived SAS URL (stored on the action as
# attachment_url) plus the blob_name (stored as attachment_blob_name so the
# file can be deleted together with the action). No extra DB table is needed.


@router.post("/{complaint_id}/escalation-action/attachment")
async def upload_escalation_attachment(
    complaint_id: int,
    step_code: str = Query(..., description="D1–D8 the attachment belongs to"),
    level: int = Query(1, ge=1, le=4),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """
    Upload an attachment (signed PO, screenshot, email export…) for an
    escalation action. Returns {url, blob_name, filename, mime_type} — the
    caller submits url/filename/blob_name on the action itself.
    """
    exists = await db.execute(
        select(Complaint.id).where(Complaint.id == complaint_id)
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Complaint not found")

    result = await blob_storage.upload_escalation_attachment(
        file=file,
        complaint_id=complaint_id,
        step_code=step_code,
        level=level,
    )
    return {
        "url": result["file_url"],
        "blob_name": result["blob_name"],
        "filename": result["filename"],
        "mime_type": result["mimetype"],
    }


@router.delete("/{complaint_id}/escalation-action/attachment")
async def delete_escalation_attachment(
    complaint_id: int,
    blob_name: str = Query(..., description="Blob path returned by the upload"),
) -> dict:
    """
    Delete an uploaded attachment blob. Used to clean up an orphan file when the
    responder removes it before saving the action.
    """
    deleted = await blob_storage.delete_blob(blob_name)
    return {"deleted": deleted, "blob_name": blob_name}


@router.delete("/{complaint_id}/escalation-action/{log_id}")
async def delete_escalation_action(
    complaint_id: int,
    log_id: int,
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    """
    Delete a recorded escalation action. If the action had an uploaded
    attachment, the blob is removed too (best-effort).
    """
    result = await db.execute(
        select(ComplaintAuditLog).where(
            ComplaintAuditLog.id == log_id,
            ComplaintAuditLog.complaint_id == complaint_id,
            ComplaintAuditLog.event_type == "escalation_action",
        )
    )
    log = result.scalar_one_or_none()
    if log is None:
        raise HTTPException(status_code=404, detail="Escalation action not found")

    blob_name = (log.event_data or {}).get("attachment_blob_name")
    if blob_name and blob_storage.is_configured():
        try:
            await blob_storage.delete_blob(blob_name)
        except Exception:
            logger.warning(
                "Could not delete blob %s for action %s — deleting action anyway",
                blob_name,
                log_id,
            )

    await db.delete(log)
    await db.commit()
    return {"deleted": True, "log_id": log_id}
