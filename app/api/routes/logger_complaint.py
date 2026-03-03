from __future__ import annotations

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

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
)

router = APIRouter()


# ─── SLA table (calendar days → hours) ───────────────────────────────────────
# Must stay in sync with complaint_service._STEP_SLA_DAYS

_SLA_HOURS: dict[str, int] = {
    "D1": 24,
    "D2": 72,
    "D3": 24,
    "D4": 168,   # 7 days
    "D5": 336,   # 14 days
    "D6": 720,   # 30 days
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
    has_escalation: bool | None = Query(None, description="Filter complaints that have at least one escalation"),
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
    count_result = await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )
    total: int = count_result.scalar_one()

    paged_q = base_q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(paged_q)
    complaints: list[Complaint] = result.scalars().unique().all()

    items: list[ComplaintLogItem] = []
    for c in complaints:
        steps = c.report.steps if c.report else []
        last_log = (
            max((lg.created_at for lg in c.audit_logs), default=None)
            if c.audit_logs else None
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
                quality_manager_email=c.quality_manager_email,
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


# ─── GET /{complaint_id}/logs ─────────────────────────────────────────────────

@router.get("/{complaint_id}/logs", response_model=ComplaintLogsResponse)
async def get_complaint_logs(
    complaint_id: int,
    step_code: str | None = Query(None),
    event_type: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
) -> ComplaintLogsResponse:
    """Full ordered audit log for a single complaint, optionally filtered."""

    c_result = await db.execute(
        select(Complaint).where(Complaint.id == complaint_id)
    )
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
        quality_manager_email=complaint.quality_manager_email,
        plant_manager_email=complaint.plant_manager_email,
        status=complaint.status,
        priority=complaint.priority,
        due_date=complaint.due_date,
        closed_at=complaint.closed_at,
        created_at=complaint.created_at,
        steps=step_items,
        complaint_level_logs=[_serialize_log(lg) for lg in complaint_level_logs],
    )