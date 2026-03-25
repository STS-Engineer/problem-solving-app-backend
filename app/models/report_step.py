from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from sqlalchemy.dialects.postgresql import JSONB
from datetime import datetime, timezone


class ReportStep(Base):
    """Individual steps (D1-D8) within investigation reports"""

    __tablename__ = "report_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_code = Column(String(10), nullable=False, comment="D1|D2|D3|D4|D5|D6|D7|D8")
    step_name = Column(String(255), nullable=False)
    status = Column(
        String(50),
        nullable=False,
        default="draft",
        index=True,
        comment="draft|fulfilled",
    )
    data = Column(JSONB, comment="Flexible JSON storage for step-specific data")
    completed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="SLA-based deadline for this step",
    )
    escalation_sent_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Timestamp of the most recent escalation email",
    )

    is_overdue = Column(
        Boolean,
        nullable=False,
        server_default="false",
        comment="True once the due_date has passed without completion",
    )
    escalation_count = Column(
        Integer,
        nullable=False,
        server_default="0",
        comment=(
            "Highest escalation level SUCCESSFULLY DELIVERED (0–4). "
            "Only incremented on confirmed send — never speculatively. "
            "Used by _level_to_send() to determine the next level. "
            "L1=quality_manager, L2=plant_manager, L3=COO, L4=CEO."
        ),
    )
    cost = Column(Integer, nullable=True, comment="Cost attributed to this step")
    # Relationships
    report = relationship("Report", back_populates="steps")
    completer = relationship(
        "User", foreign_keys=[completed_by], back_populates="completed_steps"
    )
    step_files = relationship(
        "StepFile", back_populates="report_step", cascade="all, delete-orphan"
    )
    conversations = relationship(
        "StepConversation",
        back_populates="report_step",
        cascade="all, delete-orphan",
        lazy="dynamic",  # optional — avoids loading all messages when loading a step
    )
    # Constraints
    __table_args__ = (
        UniqueConstraint("report_id", "step_code", name="unique_report_step"),
        Index("idx_report_steps_data", "data", postgresql_using="gin"),
    )

    def __repr__(self):
        return f"<ReportStep(id={self.id}, code='{self.step_code}', status='{self.status}')>"
