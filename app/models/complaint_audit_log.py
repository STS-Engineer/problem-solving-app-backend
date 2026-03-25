from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from app.db.base import Base

# Escalation level definitions (documented here for reference):
#
#   L1 (24h overdue)  → quality_manager_email
#   L2 (48h overdue)  → plant_manager_email
#   L3 (72h overdue)  → inform Coo
#   L4 (96h overdue)  → inform ceo


class ComplaintAuditLog(Base):
    __tablename__ = "complaint_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ── References ────────────────────────────────────────────────────────────
    complaint_id = Column(
        Integer,
        ForeignKey("complaints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    step_id = Column(
        Integer,
        ForeignKey("report_steps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Event classification ───────────────────────────────────────────────────
    step_code = Column(
        String(10),
        nullable=True,
        index=True,
        comment="D1–D8 or NULL for complaint-level events",
    )
    event_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment=(
            "complaint_created|report_created|step_filled|"
            "step_updated|step_reopened|due_date_missed|"
            "escalation_sent|status_changed|comment_added|file_uploaded"
        ),
    )

    # ── Actor ─────────────────────────────────────────────────────────────────
    # NULL means a system/scheduler event (escalation check, auto-status change)
    performed_by_email = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Email of actor, or NULL for system events",
    )

    # ── Payload ───────────────────────────────────────────────────────────────
    event_data = Column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Delta/context — see EVENT SCHEMAS in module docstring",
    )

    # ── Timestamp ─────────────────────────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Relationships ──────────────────────────────────────────────────────────
    complaint = relationship("Complaint", back_populates="audit_logs")
    report = relationship("Report")
    step = relationship("ReportStep")

    # ── Indexes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("idx_audit_complaint_step", "complaint_id", "step_code"),
        Index("idx_audit_complaint_type", "complaint_id", "event_type"),
        Index("idx_audit_data_gin", "event_data", postgresql_using="gin"),
    )

    def __repr__(self):
        return (
            f"<ComplaintAuditLog(id={self.id}, complaint={self.complaint_id}, "
            f"type='{self.event_type}', step='{self.step_code}')>"
        )
