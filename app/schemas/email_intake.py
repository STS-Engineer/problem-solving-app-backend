from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models.enums import PlantEnum


class AttachmentIn(BaseModel):
    filename: str
    url: Optional[str] = None
    mime_type: Optional[str] = None


class EmailIntakeCreate(BaseModel):
    """
    Lenient payload posted by the ChatGPT/MCP agent.

    The ONLY required field is source_message_id (the dedup key). Everything
    else is best-effort — the whole point of staging is that we accept
    incomplete emails.
    """

    source_message_id: str = Field(..., max_length=998)
    conversation_id: Optional[str] = Field(None, max_length=512)

    sender_email: Optional[str] = Field(None, max_length=255)
    sender_name: Optional[str] = Field(None, max_length=255)

    subject: Optional[str] = Field(None, max_length=998)
    received_at: Optional[datetime] = None
    raw_body: Optional[str] = None
    raw_html: Optional[str] = None
    attachments: List[AttachmentIn] = Field(default_factory=list)

    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    ai_notes: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)

    # Optional explicit plant. If omitted, we try to read it from
    # extracted_data["avocarbon_plant"]; if still unknown → fallback email.
    detected_plant: Optional[PlantEnum] = None


class EmailIntakeAssign(BaseModel):
    """QM assigns a CQT (internal AVOCarbon Customer Quality Engineer)."""

    cqe_email: str = Field(..., max_length=255)
    assigned_by: Optional[str] = Field(None, max_length=255, description="QM email")


class EmailIntakeSetPlant(BaseModel):
    """Triage: set/correct the responsible plant on an intake."""

    plant: PlantEnum
    renotify: bool = Field(
        True, description="Notify the plant's QM/PM after setting the plant"
    )


class EmailIntakeResult(BaseModel):
    """Response returned to the agent so its ChatGPT summary is accurate."""

    status: str  # created | duplicate | attached_to_existing
    intake_id: int
    notified_to: List[str] = Field(default_factory=list)


class EmailIntakePromoteResult(BaseModel):
    """Result of promoting an intake to a real complaint."""

    status: str  # created | already_promoted | incomplete
    complaint_id: Optional[int] = None
    reference_number: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)


class EmailIntakeRead(BaseModel):
    id: int
    source_message_id: str
    conversation_id: Optional[str]
    sender_email: Optional[str]
    sender_name: Optional[str]
    subject: Optional[str]
    received_at: Optional[datetime]
    raw_body: Optional[str]
    raw_html: Optional[str]
    attachments: List[Any]
    extracted_data: Dict[str, Any]
    ai_notes: Optional[str]
    missing_fields: List[Any]
    detected_plant: Optional[PlantEnum]
    status: str
    notified_to: List[Any]
    reject_reason: Optional[str]
    assigned_cqe_email: Optional[str]
    assigned_by: Optional[str]
    assigned_at: Optional[datetime]
    complaint_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class EmailIntakeListItem(BaseModel):
    id: int
    source_message_id: str
    sender_email: Optional[str]
    sender_name: Optional[str]
    subject: Optional[str]
    detected_plant: Optional[PlantEnum]
    status: str
    missing_fields: List[Any]
    assigned_cqe_email: Optional[str]
    complaint_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True
