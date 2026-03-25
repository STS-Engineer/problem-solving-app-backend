from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SQLEnum,
    Integer,
    String,
    Text,
    Index,
)

from app.db.base import Base  # your declarative Base


class WebhookStatus(str, enum.Enum):
    pending = "pending"  # waiting to be picked up
    locked = "locked"  # claimed by a worker right now
    done = "done"  # delivered successfully
    failed = "failed"  # all retries exhausted
    abandoned = "abandoned"  # manually disabled


class WebhookJob(Base):
    __tablename__ = "webhook_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # What triggered this job
    complaint_id = Column(Integer, nullable=False, index=True)
    complaint_ref = Column(String(50), nullable=False)
    complaint_type = Column(
        String(50), nullable=False, comment="CS1 or CS2 — stored for auditing"
    )
    event = Column(String(100), nullable=False, default="complaint.created")

    # Delivery target (one row per URL — if you have 3 URLs, 3 rows)
    target_url = Column(String(2048), nullable=False)

    # Queue control
    status = Column(
        SQLEnum(WebhookStatus, name="webhook_status_enum"),
        nullable=False,
        default=WebhookStatus.pending,
    )
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    retry_after = Column(
        DateTime, nullable=True, comment="Earliest time next attempt may run"
    )

    # Payload snapshot (stored once, reused on retries)
    payload_json = Column(Text, nullable=False)

    # Outcome tracking
    last_http_status = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)

    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Index that the worker query hits: pending jobs ready to run, oldest first
    __table_args__ = (
        Index(
            "ix_webhook_jobs_worker",
            "status",
            "retry_after",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookJob id={self.id} status={self.status.value} "
            f"attempt={self.attempt} url={self.target_url!r}>"
        )
