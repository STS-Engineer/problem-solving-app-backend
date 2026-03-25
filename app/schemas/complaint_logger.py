from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, ConfigDict


class StepSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str  # D1–D8
    name: str
    status: str  # not_started|draft|fulfilled|overdue|escalated
    due_date: datetime | None
    completed_at: datetime | None
    completed_by_email: str | None
    escalation_count: int
    cost: float | None
    hours_allowed: int  # SLA hours for this step
    is_overdue: bool


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    complaint_id: int
    report_id: int | None
    step_id: int | None
    step_code: str | None  # D1–D8 or None for complaint-level events
    event_type: str
    event_data: dict[str, Any]
    performed_by_email: str | None  # None = system event
    created_at: datetime


class ComplaintLogItem(BaseModel):
    """Used in the list view (sidebar)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    reference_number: str
    complaint_name: str
    customer: str
    plant: str
    status: str
    priority: str
    cqt_email: str | None
    quality_manager_email: str | None
    plant_manager_email: str | None
    created_at: datetime
    due_date: datetime | None
    closed_at: datetime | None
    steps: list[StepSummary]
    total_escalations: int
    last_activity: datetime | None


class ComplaintLoggerListResponse(BaseModel):
    items: list[ComplaintLogItem]
    total: int
    page: int
    page_size: int
    pages: int


class ComplaintLogsResponse(BaseModel):
    """Full audit log for one complaint (timeline tab)."""

    complaint_id: int
    reference_number: str
    logs: list[AuditLogEntry]
    total: int


class StepWithLogs(BaseModel):
    """One step + all its audit events (drilldown)."""

    step: StepSummary
    logs: list[AuditLogEntry]


class StepLogsResponse(BaseModel):
    """Per-step drilldown response — the main detail view."""

    complaint_id: int
    reference_number: str
    complaint_name: str
    customer: str
    cqt_email: str | None
    quality_manager_email: str | None
    plant_manager_email: str | None
    status: str
    priority: str
    due_date: datetime | None
    closed_at: datetime | None
    created_at: datetime
    steps: list[StepWithLogs]
    complaint_level_logs: list[AuditLogEntry]  # events not tied to a step
