from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class EscalationActionType(str, Enum):
    """Predefined actions an L1/L2 responder can record against an escalation."""

    called_responsible = "called_responsible"
    reassigned = "reassigned"
    approved_resource = "approved_resource"
    meeting_held = "meeting_held"
    other = "other"


# Human-readable labels (frontend may use these as the dropdown labels).
ESCALATION_ACTION_LABELS: dict[str, str] = {
    "called_responsible": "Called responsible",
    "reassigned": "Reassigned complaint",
    "approved_resource": "Approved purchase / resource",
    "meeting_held": "Meeting held",
    "other": "Other",
}


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


# ─── Escalation response recording ────────────────────────────────────────────


class EscalationActionRequest(BaseModel):
    """
    Payload an L1/L2 responder submits to record what they did in response to
    an escalation email (call the responsible, reassign, approve a purchase, …).
    """

    step_code: str = Field(..., description="D1–D8 — the step that was escalated")
    level: int = Field(..., ge=1, le=4, description="Escalation level being responded to")
    action_type: EscalationActionType
    note: str | None = Field(
        None, max_length=4000, description="What was done / the outcome"
    )
    resolved: bool = Field(
        False, description="Mark the escalation at this level as handled/closed"
    )
    attachment_url: str | None = Field(
        None, max_length=2000, description="Optional link/file URL (signed PO, screenshot…)"
    )
    attachment_name: str | None = Field(None, max_length=255)
    attachment_blob_name: str | None = Field(
        None,
        max_length=1024,
        description="Azure blob path (kept so the file can be deleted with the action)",
    )
    performed_by_email: str = Field(
        ..., description="Email of the responder recording the action"
    )


class EscalationLevelTrack(BaseModel):
    """One escalation level for a step: when it was sent + all recorded responses."""

    level: int
    step_code: str
    sent_at: datetime | None  # when the escalation email for this level was sent
    recipients: list[str]
    actions: list[AuditLogEntry]  # escalation_action events at this level
    resolved: bool  # true once any recorded action marked it resolved


class EscalationTrackResponse(BaseModel):
    """
    Consolidated escalation track for a complaint — the view for the Owner and L2.
    One entry per (step, level) that has been escalated, newest level last.
    """

    complaint_id: int
    reference_number: str
    complaint_name: str
    levels: list[EscalationLevelTrack]
