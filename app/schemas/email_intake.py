from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PlantEnum


class AttachmentIn(BaseModel):
    """
    One email attachment as forwarded by the MCP agent.

    Preferred: the agent passes `download_url` (the short-lived Microsoft Graph
    signed URL from the Outlook connector) and the backend fetches the bytes at
    ingestion. `url` is an optional fallback for an already-hosted durable link.
    Inline images (signatures/logos) should set is_inline=True; they are skipped.
    """

    filename: str
    mime_type: Optional[str] = None
    size: Optional[int] = Field(None, description="Size in bytes, if known")
    download_url: Optional[str] = Field(
        None, description="Signed temporary download URL (e.g. MS Graph)"
    )
    url: Optional[str] = None
    sha256: Optional[str] = Field(None, description="SHA-256 for integrity check")
    description: Optional[str] = Field(
        None, description="Agent-generated description of the file's content"
    )
    is_inline: bool = Field(
        False, description="Inline/embedded image (signature, logo); skipped by default"
    )
    content_id: Optional[str] = None


class ExtractedData(BaseModel):
    """
    Structured complaint facts extracted from the email body and attachments.

    Keeping the common keys explicit gives the MCP/OpenAPI contract a cleaner
    shape for agents, while `extra="allow"` preserves lenient staging.
    """

    model_config = ConfigDict(extra="allow")

    complaint_name: Optional[str] = None
    customer: Optional[str] = None
    customer_plant_name: Optional[str] = None
    avocarbon_plant: Optional[str] = None
    avocarbon_product_type: Optional[str] = None
    product_line: Optional[str] = None
    quality_issue_warranty: Optional[str] = None
    potential_avocarbon_process_linked_to_problem: Optional[str] = None
    defects: Optional[str] = None
    complaint_description: Optional[str] = None
    customer_complaint_date: Optional[str] = None
    concerned_application: Optional[str] = None
    repetitive_complete_with_number: Optional[str] = None


class EmailIntakeCreate(BaseModel):
    """
    Lenient payload posted by the ChatGPT/MCP agent.

    The ONLY required field is source_message_id (the dedup key). Everything
    else is best-effort; the whole point of staging is that we accept
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

    extracted_data: ExtractedData = Field(default_factory=ExtractedData)
    ai_notes: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)

    # Optional explicit plant. If omitted, we try to read it from
    # extracted_data["avocarbon_plant"]; if still unknown -> fallback email.
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

    escalation_stage: Optional[str] = None
    escalation_count: int = 0
    escalation_sent_at: Optional[datetime] = None
    escalation_log: List[Any] = Field(default_factory=list)

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

    escalation_stage: Optional[str] = None
    escalation_count: int = 0
    escalation_sent_at: Optional[datetime] = None

    class Config:
        from_attributes = True
