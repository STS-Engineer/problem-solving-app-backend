"""
services/escalation_service.py

Escalation ladder (hours past due_date):
  Production:  L1=24h  L2=48h  L3=72h  L4=96h
  Test mode:   L1=30m  L2=60m  L3=90m  L4=120m  (TEST_ESCALATION=true)

Recipients per level:
  L1 → quality_manager_email
  L2 → quality_manager_email + cqt_email
  L3 → all three + plant_manager_email (also CC'd)
  L4 → same as L3, final notice
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.complaint import Complaint
from app.models.report import Report
from app.models.report_step import ReportStep
from app.services.audit_service import log_due_date_missed, log_escalation_sent
from app.services.email_templates import build_escalation_email
from app.core.email import send_email
from app.services.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

# ── SLA config ────────────────────────────────────────────────────────────────

_TEST_MODE = os.getenv("TEST_ESCALATION", "false").lower() == "true"
_SCALE = (1 / 48) if _TEST_MODE else 1.0   # 24h → 30min in test mode
logger.info("TEST_MODE=%s | SCALE=%s | L1_threshold=%.2fh", _TEST_MODE, _SCALE, 24 * _SCALE)
# (minimum hours overdue, escalation level)
THRESHOLDS: list[tuple[float, int]] = [
    (24 * _SCALE, 1),
    (48 * _SCALE, 2),
    (72 * _SCALE, 3),
    (96 * _SCALE, 4),
]

if _TEST_MODE:
    logger.warning("TEST_ESCALATION=true — L1=30min L2=60min L3=90min L4=120min")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hours_overdue(step: ReportStep) -> float | None:
    if step.completed_at is not None:
        return None
    if not step.due_date:
        return None

    due = step.due_date if step.due_date.tzinfo else step.due_date.replace(tzinfo=timezone.utc)
    
    delta = (datetime.now(timezone.utc) - due).total_seconds() / 3600
    return delta if delta > 0 else None


def _level_to_send(hours: float, already_sent: int) -> int | None:
    """
    Returns the next escalation level to send, or None.
    Example: 50h overdue, already_sent=0 → triggered=2, next=1 ✓
             50h overdue, already_sent=1 → triggered=2, next=2 ✓
             50h overdue, already_sent=2 → triggered=2, next=3 ✗ (3 > triggered)
    """
    triggered = max((lvl for thr, lvl in THRESHOLDS if hours >= thr), default=0)
    if triggered == 0:
        return None
    next_level = already_sent + 1
    return next_level if next_level <= triggered else None


def _build_recipients(level: int, complaint: Complaint) -> list[str]:
    """Ordered, deduplicated recipient list for a given escalation level."""
    candidates: list[str | None] = []
    if level >= 1: candidates.append(complaint.quality_manager_email)
    if level >= 2: candidates.append(complaint.cqt_email)
    if level >= 3: candidates.append(complaint.plant_manager_email)
    seen: set[str] = set()
    return [e for e in candidates if e and not (e in seen or seen.add(e))]  # type: ignore[func-returns-value]


# ── Main job ──────────────────────────────────────────────────────────────────

async def check_and_escalate_all(db: AsyncSession) -> None:
    """
    Called every N minutes by APScheduler.
    Loads all active (non-completed) steps that have a due_date and fires
    the next escalation level for any that are overdue.
    """
    result = await db.execute(
        select(ReportStep)
        .where(
            ReportStep.completed_at.is_(None),
            ReportStep.due_date.isnot(None),
        )
        .options(
            selectinload(ReportStep.report).selectinload(Report.complaint)
        )
    )
    steps = result.scalars().all()
    logger.info("Escalation scan: %d active step(s) with due_date", len(steps))

    fired = 0
    for step in steps:
        step_id = step.id      
        step_code = step.step_code
        try:
            sent = await _process_step(db, step)
            if sent:
                fired += 1
        except Exception:
            logger.exception("Escalation error on step_id=%s (%s)", step_id, step_code)

    logger.info("Escalation scan complete — %d email(s) sent", fired)
    await db.commit()


async def _process_step(db: AsyncSession, step: ReportStep) -> bool:
    """Evaluate a single step and fire an escalation email if due. Returns True if sent."""
    step_id = step.id
    step_code = step.step_code

    hours = _hours_overdue(step)
    complaint: Complaint = step.report.complaint

    # ── Detailed diagnostics so you can see exactly why a step is skipped ────
    logger.debug(
        "Step %s | %s | complaint=%s | due=%s | completed=%s | overdue_hours=%s | escalation_count=%s | qm=%s | cqt=%s | pm=%s",
        step_id,
        step_code,
        complaint.reference_number,
        step.due_date,
        step.completed_at,
        f"{hours:.2f}" if hours else "N/A",
        step.escalation_count,
        complaint.quality_manager_email,
        complaint.cqt_email,
        complaint.plant_manager_email,
    )

    if hours is None:
        logger.info("Step %s (%s): not overdue or already completed — skip", step_id, step_code)
        return False

    level = _level_to_send(hours, step.escalation_count or 0)
    if level is None:
        logger.info(
            "Step %s (%s): %.1fh overdue, escalation_count=%s — no new level to send",
            step_id, step_code, hours, step.escalation_count,
        )
        return False

    recipients = _build_recipients(level, complaint)
    if not recipients:
        # This is the most common silent failure — log it prominently
        logger.warning(
            "Step %s (%s): L%s due but NO RECIPIENTS — "
            "qm_email=%r cqt_email=%r pm_email=%r on complaint %s. "
            "Set at least quality_manager_email to receive L1 escalations.",
            step.id, step.step_code, level,
            complaint.quality_manager_email,
            complaint.cqt_email,
            complaint.plant_manager_email,
            complaint.reference_number,
        )
        return False

    # ── First escalation: log the missed deadline and mark step overdue ───────
    if (step.escalation_count or 0) == 0:
        await log_due_date_missed(
            db,
            complaint_id=complaint.id,
            step_id=step.id,
            step_code=step.step_code,
            due_date=step.due_date,
            missed_by_hours=hours,
        )
        step.is_overdue = True
        
        step.status = "overdue"

    # ── Build and send email ──────────────────────────────────────────────────
    subject, body_html = build_escalation_email(
        level=level,
        complaint_reference=complaint.reference_number,
        complaint_name=complaint.complaint_name,
        customer=complaint.customer or "",
        step_code=step.step_code,
        step_name=getattr(step, "step_name", None),
        hours_overdue=hours,
        due_date=step.due_date.isoformat(),
        cqt_email=complaint.cqt_email,
        quality_manager_email=complaint.quality_manager_email,
        plant_manager_email=complaint.plant_manager_email,
    )

    cc = [complaint.plant_manager_email] if level >= 3 and complaint.plant_manager_email else None

    await send_email(subject=subject, recipients=recipients, body_html=body_html, cc=cc)

    # ── Persist state ─────────────────────────────────────────────────────────
    step.escalation_count = level
    step.escalation_sent_at = utc_now()



    # replace the hardcoded _TEST_MODE reference at the bottom of _process_step
    _test_mode = os.getenv("TEST_ESCALATION", "false").lower() == "true"
    hours_label = f"{hours * 60:.0f}min" if _test_mode else f"{hours:.1f}h"
    await log_escalation_sent(
        db,
        complaint_id=complaint.id,
        step_id=step.id,
        step_code=step.step_code,
        level=level,
        recipients=recipients,
        template=f"step_overdue_l{level}",
        reason=(
            f"Step {step.step_code} is {hours_label} past its due date "
            f"({step.due_date.strftime('%Y-%m-%d %H:%M') if step.due_date else '?'}). "
            f"Escalation level {level} triggered."
        ),
    )

    logger.info(
        "✓ Escalation L%s sent | complaint=%s | step=%s | overdue=%s | recipients=%s",
        level, complaint.reference_number, step.step_code, hours_label, recipients,
    )
    return True


def _get_thresholds() -> list[tuple[float, int]]:
    """Re-read env var each call so it works correctly regardless of import order."""
    test_mode = os.getenv("TEST_ESCALATION", "false").lower() == "true"
    scale = (1 / 48) if test_mode else 1.0
    return [
        (24 * scale, 1),
        (48 * scale, 2),
        (72 * scale, 3),
        (96 * scale, 4),
    ]


def _level_to_send(hours: float, already_sent: int) -> int | None:
    triggered = max(
        (lvl for thr, lvl in _get_thresholds() if hours >= thr), default=0
    )
    if triggered == 0:
        return None
    next_level = already_sent + 1
    return next_level if next_level <= triggered else None