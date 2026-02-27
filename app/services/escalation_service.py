"""
services/escalation_service.py

Uses real send_email(subject, recipients, body_html, cc) and
build_escalation_email() for professional HTML emails.

──────────────────────────────────────────────────────────────────
PRODUCTION escalation ladder (hours past due_date):
  L1  +24h  → quality_manager_email
  L2  +48h  → quality_manager_email + cqt_email
  L3  +72h  → plant_manager_email + quality_manager_email + cqt_email
  L4  +96h  → ALL above — final notice, no further sends

TEST MODE  (TEST_ESCALATION=true in .env):
  SLA timings compressed by factor 1/48 so 24h → 30min.
  D1 fires its L1 escalation 30 min after due_date is set.

  L1  +30min
  L2  +60min
  L3  +90min
  L4  +120min
──────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
import logging
import os
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.report_step import ReportStep
from app.models.complaint import Complaint
from app.services.audit_service import log_due_date_missed, log_escalation_sent
from app.services.email_templates import build_escalation_email
from app.core.email import send_email

logger = logging.getLogger(__name__)

# ── SLA thresholds ─────────────────────────────────────────────────────────────
# Production: hours. Test: divide by 48 → 30-min per level.
_TEST_MODE = os.getenv("TEST_ESCALATION", "false").lower() == "true"
_SCALE     = (1 / 48) if _TEST_MODE else 1.0   # compress hours → ~30min units

# (hours_threshold, escalation_level)
# In test mode these become (0.5h, 1), (1.0h, 2), (1.5h, 3), (2.0h, 4)
ESCALATION_THRESHOLDS: list[tuple[float, int]] = [
    (24 * _SCALE, 1),
    (48 * _SCALE, 2),
    (72 * _SCALE, 3),
    (96 * _SCALE, 4),
]

if _TEST_MODE:
    logger.warning(
        "TEST_ESCALATION=true — SLA compressed: L1=30min, L2=60min, L3=90min, L4=120min"
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _recipients(
    level: int,
    *,
    cqt: str | None,
    qm: str | None,
    pm: str | None,
) -> list[str]:
    seen: set[str] = set()
    out:  list[str] = []

    def add(e: str | None) -> None:
        if e and e not in seen:
            seen.add(e)
            out.append(e)

    if level >= 1: add(qm)
    if level >= 2: add(cqt)
    if level >= 3: add(pm)
    return out


def _hours_overdue(step: ReportStep) -> float | None:
    """Return hours past due_date, or None if not overdue / already completed."""
    if step.completed_at or not step.due_date:
        return None
    now = datetime.now(timezone.utc)
    due = (
        step.due_date
        if step.due_date.tzinfo
        else step.due_date.replace(tzinfo=timezone.utc)
    )
    delta = (now - due).total_seconds() / 3600
    return delta if delta > 0 else None


def _next_level(hours: float, current_count: int) -> int | None:
    """
    Given hours overdue and escalation_count already sent,
    return the next level to send or None if nothing new.
    """
    triggered = max(
        (lvl for thr, lvl in ESCALATION_THRESHOLDS if hours >= thr),
        default=0,
    )
    if triggered == 0:
        return None
    next_l = current_count + 1
    return next_l if next_l <= triggered else None


# ── Main scheduler task ────────────────────────────────────────────────────────

async def check_and_escalate_all(db: AsyncSession) -> None:
    """
    Called every N minutes by the APScheduler job.
    Scans all non-completed overdue steps and fires the next escalation level.
    Idempotent — uses step.escalation_count to avoid re-sending.
    """
    result = await db.execute(
        select(ReportStep)
        .where(
            ReportStep.completed_at.is_(None),
            ReportStep.due_date.isnot(None),
            ReportStep.status.in_(["draft", "not_started", "overdue"]),
        )
        .options(
            selectinload(ReportStep.report).selectinload("complaint")
        )
    )
    steps: list[ReportStep] = result.scalars().all()
    logger.debug("Escalation scan: %d active steps checked", len(steps))

    for step in steps:
        try:
            await _process_step(db, step)
        except Exception:
            logger.exception(
                "Escalation error: step_id=%s complaint_id=%s",
                step.id,
                getattr(step.report, "complaint_id", "?"),
            )

    await db.commit()


async def _process_step(db: AsyncSession, step: ReportStep) -> None:
    hours = _hours_overdue(step)
    if hours is None:
        return

    c: Complaint = step.report.complaint
    level = _next_level(hours, step.escalation_count or 0)
    if level is None:
        return

    # Log missed deadline only on the very first escalation
    if (step.escalation_count or 0) == 0:
        await log_due_date_missed(
            db,
            complaint_id=c.id,
            step_id=step.id,
            step_code=step.step_code,
            due_date=step.due_date,
            missed_by_hours=hours,
        )
        step.is_overdue = True
        step.status     = "overdue"

    recs = _recipients(
        level,
        cqt=c.cqt_email,
        qm=c.quality_manager_email,
        pm=c.plant_manager_email,
    )
    if not recs:
        logger.warning(
            "No recipients configured for L%s escalation on step %s — skipping",
            level, step.id,
        )
        return

    # Build HTML email
    subject, body_html = build_escalation_email(
        level=level,
        complaint_reference=c.reference_number,
        complaint_name=c.complaint_name,
        customer=c.customer or "",
        step_code=step.step_code,
        step_name=getattr(step, "step_name", None),
        hours_overdue=hours,
        due_date=step.due_date.isoformat(),
        cqt_email=c.cqt_email,
        quality_manager_email=c.quality_manager_email,
        plant_manager_email=c.plant_manager_email,
    )

    # L3+ → also CC plant manager for explicit thread visibility
    cc: list[str] | None = (
        [c.plant_manager_email]
        if level >= 3 and c.plant_manager_email
        else None
    )

    await send_email(
        subject=subject,
        recipients=recs,
        body_html=body_html,
        cc=cc,
    )

    # Persist counters
    step.escalation_count   = level
    step.escalation_sent_at = datetime.now(timezone.utc)

    await log_escalation_sent(
        db,
        complaint_id=c.id,
        step_id=step.id,
        step_code=step.step_code,
        level=level,
        recipients=recs,
        template=f"step_overdue_l{level}",
    )

    logger.info(
        "✓ Escalation L%s sent | %s / %s | overdue %.1fh | to: %s",
        level,
        c.reference_number,
        step.step_code,
        hours,
        recs,
    )