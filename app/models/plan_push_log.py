"""
app/models/plan_push_log.py
═══════════════════════════
Tracks every attempt to push D6 corrective actions to the external ERP.

status lifecycle:
  pending  → push not yet attempted (row just created/reset)
  success  → external API accepted, root_sujet_id stored
  failed   → last attempt errored, will be retried
"""

from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base


class PlanPushLog(Base):
    __tablename__ = "plan_push_log"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    # one row per report — upserted on every D6 fulfill/update
    report_id = Column(
        Integer,
        ForeignKey("reports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_id = Column(
        Integer, ForeignKey("report_steps.id", ondelete="CASCADE"), nullable=False
    )

    status = Column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending | success | failed",
    )
    attempt_count = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    # returned by external API on success
    external_root_sujet_id = Column(Integer, nullable=True)

    # full payload sent — invaluable for debugging
    payload = Column(JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # one active row per report (upsert on conflict)
    __table_args__ = (UniqueConstraint("report_id", name="uq_plan_push_log_report"),)

    def __repr__(self) -> str:
        return (
            f"<PlanPushLog(report_id={self.report_id}, "
            f"status='{self.status}', attempts={self.attempt_count})>"
        )
