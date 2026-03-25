from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    Text,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    String,
)
from sqlalchemy.orm import relationship
from app.db.base import Base


class StepFile(Base):
    """Junction table linking files to report steps"""

    __tablename__ = "step_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_step_id = Column(
        Integer,
        ForeignKey("report_steps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_id = Column(
        Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # ── Action scope (D6 per-action evidence) ──────────────────────────────
    # NULL on both columns → step-level file (legacy / D1-D5 / D7-D8)
    # Non-NULL            → scoped to a specific corrective action in D6
    action_type = Column(
        String(20),
        nullable=True,
        comment="'occurrence' | 'detection' | NULL for step-level files",
    )
    action_index = Column(
        Integer, nullable=True, comment="0-based index into the corrective action array"
    )

    attachment_order = Column(Integer, default=0)
    description = Column(Text)
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    report_step = relationship("ReportStep", back_populates="step_files")
    file = relationship("File", back_populates="step_files")

    # Constraints
    __table_args__ = (
        UniqueConstraint(
            "report_step_id",
            "file_id",
            "action_type",
            "action_index",
            name="unique_step_file_scoped",
        ),
    )

    def __repr__(self):
        return f"<StepFile(id={self.id}, report_step_id={self.report_step_id}, file_id={self.file_id})>"
