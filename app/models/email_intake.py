from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    JSON,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.enums import PlantEnum


class EmailIntake(Base):
    """
    Lenient staging row for a complaint that arrived by email.

    Nothing here is required beyond a unique message id, so incomplete or
    malformed emails can always be stored. A human reviews the row and, once
    the data is complete, promotes it into a real (validated) Complaint via
    ComplaintService.create_complaint.
    """

    __tablename__ = "email_intake"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ── Dedup / threading ──────────────────────────────────────────────────
    source_message_id = Column(
        String(998),
        nullable=False,
        unique=True,
        index=True,
        comment="RFC 5322 Message-ID — idempotency key, prevents duplicates",
    )
    conversation_id = Column(
        String(512),
        nullable=True,
        index=True,
        comment="Mail thread id — follow-ups attach to the same complaint",
    )

    # ── Sender (from the envelope, authoritative — NOT LLM-guessed) ────────
    sender_email = Column(String(255), nullable=True, index=True)
    sender_name = Column(String(255), nullable=True)

    # ── Raw email ──────────────────────────────────────────────────────────
    subject = Column(String(998), nullable=True)
    received_at = Column(DateTime, nullable=True)
    raw_body = Column(Text, nullable=True)
    raw_html = Column(Text, nullable=True)

    # List of attachment descriptors, e.g. [{"filename": "...", "url": "..."}]
    attachments = Column(JSON, nullable=False, default=list)

    # ── LLM output (all optional) ──────────────────────────────────────────
    extracted_data = Column(JSON, nullable=False, default=dict)
    ai_notes = Column(Text, nullable=True)
    missing_fields = Column(JSON, nullable=False, default=list)

    # Plant the agent could determine (drives notification routing). NULL when
    # unknown → falls back to the triage email.
    detected_plant = Column(
        SQLEnum(PlantEnum, name="plant_enum"),
        nullable=True,
        index=True,
    )

    # ── Workflow ───────────────────────────────────────────────────────────
    status = Column(
        String(30),
        nullable=False,
        default="pending_review",
        index=True,
        comment="pending_review | promoted | rejected | duplicate",
    )
    notified_to = Column(JSON, nullable=False, default=list, comment="Emails notified on intake")
    reject_reason = Column(Text, nullable=True)

    # ── CQT assignment (internal AVOCarbon Customer Quality Engineer) ──────
    # QM/PM are auto-resolved from plant_contacts; the CQT is assigned manually
    # by the QM via a form, then notified with a link to complete the complaint.
    assigned_cqe_email = Column(String(255), nullable=True, index=True)
    assigned_by = Column(String(255), nullable=True, comment="QM email who assigned the CQT")
    assigned_at = Column(DateTime, nullable=True)

    complaint_id = Column(
        Integer,
        ForeignKey("complaints.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Set when promoted to a real complaint",
    )
    reviewed_by = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    complaint = relationship("Complaint")

    def __repr__(self) -> str:
        return (
            f"<EmailIntake(id={self.id}, status={self.status!r}, "
            f"sender={self.sender_email!r}, plant={self.detected_plant})>"
        )
