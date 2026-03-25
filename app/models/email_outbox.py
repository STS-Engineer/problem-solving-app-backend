from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Valid status values — keep in sync with CheckConstraint below
VALID_STATUSES = {"pending", "sent", "failed", "abandoned"}


class EmailOutbox(Base):
    """Persistent outbox for escalation emails."""

    __tablename__ = "email_outbox"

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ── Foreign keys ─────────────────────────────────────────────────────────
    # Nullable so outbox rows survive cascade deletes on parent records.
    step_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("report_steps.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    complaint_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("complaints.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Escalation metadata ───────────────────────────────────────────────────
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Routing data ──────────────────────────────────────────────────────────
    # Stored at insert time because the complaint state can change before delivery.
    recipients: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False
    )  # ["a@b.com", ...]
    cc: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)

    # ── Delivery state ────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # next_retry_at drives the retry loop; updated with exponential backoff on failure.
    # Indexed together with status so the retry query is index-only.
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    # ── Constraints & indexes ─────────────────────────────────────────────────
    __table_args__ = (
        # Enforce valid status values at the DB level.
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'abandoned')",
            name="ck_email_outbox_status",
        ),
        # This is a partial unique index — only applies while the row is 'pending',
        # so retries (failed → pending) and historical 'sent' rows are not affected.
        # SQLAlchemy renders this via Index with postgresql_where.
        Index(
            "uq_outbox_pending_escalation",
            "complaint_id",
            "step_id",
            "escalation_level",
            unique=True,
            postgresql_where="status = 'pending'",
        ),
        # Composite index for the retry-job query:
        #   WHERE status IN ('pending', 'failed') AND next_retry_at <= now()
        Index("idx_outbox_status_retry", "status", "next_retry_at"),
    )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def mark_sent(self) -> None:
        """Call this after a successful send."""
        self.status = "sent"
        self.sent_at = _utcnow()
        self.last_error = None

    def mark_failed(self, error: str, retry_delay_seconds: int = 300) -> None:
        """
        Call this after a failed send attempt.
        Applies exponential backoff capped at ~1 hour.
        Transitions to 'abandoned' when max_attempts is reached.
        """
        from datetime import timedelta

        self.attempts += 1
        self.last_error = error[:2000]  # guard against enormous tracebacks

        if self.attempts >= self.max_attempts:
            self.status = "abandoned"
        else:
            self.status = "failed"
            # Exponential backoff: 5 min, 10 min, 20 min … capped at 60 min
            backoff = min(retry_delay_seconds * (2 ** (self.attempts - 1)), 3600)
            self.next_retry_at = _utcnow() + timedelta(seconds=backoff)

    def __repr__(self) -> str:
        return (
            f"<EmailOutbox id={self.id} complaint_id={self.complaint_id} "
            f"step_id={self.step_id} level={self.escalation_level} "
            f"status={self.status} attempts={self.attempts}/{self.max_attempts}>"
        )
