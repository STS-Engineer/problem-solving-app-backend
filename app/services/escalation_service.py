"""
app/services/escalation_service.py

"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.complaint import Complaint
from app.models.email_outbox import EmailOutbox
from app.models.report import Report
from app.models.report_step import ReportStep
from app.services.audit_service import log_due_date_missed, log_escalation_sent
from app.services.email_templates import build_escalation_email
from app.core.email import send_email
from app.services.utils.datetime_utils import utc_now

logger = logging.getLogger(__name__)

# ── SLA config ────────────────────────────────────────────────────────────────
COO_EMAIL = os.getenv("COO_EMAIL", "hayfa.rajhi@avocarbon.com")
CEO_EMAIL = os.getenv("CEO_EMAIL", "hayfa.rajhi@avocarbon.com")

# Retry backoff delays (minutes): attempt 1→10min, 2→30min, 3→60min
_RETRY_BACKOFF_MINUTES = [10, 30, 60]

# How long a 'pending' entry can sit untouched before retry_failed_emails
# treats it as stuck (process crashed before _attempt_send ran).
# Must be > scheduler check interval (30 min) to avoid false positives.
_STUCK_PENDING_THRESHOLD_MINUTES = 45


# ── Threshold helpers ─────────────────────────────────────────────────────────

def _get_thresholds() -> list[tuple[float, int]]:
    """Re-read env each call so TEST_ESCALATION toggle is hot-reloadable."""
    test_mode = os.getenv("TEST_ESCALATION", "false").lower() == "true"
    scale = (1 / 48) if test_mode else 1.0
    return [
        (24 * scale, 1),
        (48 * scale, 2),
        (72 * scale, 3),
        (96 * scale, 4),
    ]


def _is_test_mode() -> bool:
    return os.getenv("TEST_ESCALATION", "false").lower() == "true"


def _hours_label(hours: float) -> str:
    if _is_test_mode():
        return f"{hours * 60:.0f}min"
    return f"{hours:.1f}h"


# ── Domain helpers ────────────────────────────────────────────────────────────

def _hours_overdue(step: ReportStep) -> float | None:
    """Returns hours overdue, or None if step is not overdue or already completed."""
    if step.completed_at is not None:
        return None
    if not step.due_date:
        return None
    due = (
        step.due_date
        if step.due_date.tzinfo
        else step.due_date.replace(tzinfo=timezone.utc)
    )
    delta = (datetime.now(timezone.utc) - due).total_seconds() / 3600
    return delta if delta > 0 else None


def _level_to_send(hours: float, already_sent: int) -> int | None:
    """
    Returns the next escalation level to send, or None if nothing to send.

    already_sent = step.escalation_count — the highest level SUCCESSFULLY DELIVERED.
    This must never be incremented speculatively; only on confirmed send success.
    """
    thresholds = _get_thresholds()
    triggered = max(
        (lvl for thr, lvl in thresholds if hours >= thr),
        default=0,
    )
    if triggered == 0:
        return None
    next_level = already_sent + 1
    return next_level if next_level <= triggered else None


def _build_recipients(level: int, complaint: Complaint) -> list[str]:
    match level:
        case 1: return [e for e in [complaint.quality_manager_email] if e]
        case 2: return [e for e in [complaint.plant_manager_email] if e]
        case 3: return [COO_EMAIL]
        case 4: return [CEO_EMAIL]
        case _: return []


def _build_cc(level: int, complaint: Complaint) -> list[str] | None:
    match level:
        case 3:
            cc = [e for e in [complaint.plant_manager_email, complaint.quality_manager_email] if e]
            return cc or None
        case 4:
            return [COO_EMAIL]
        case _:
            return None


def _build_email(
    complaint: Complaint,
    step: ReportStep,
    level: int,
    hours: float,
) -> tuple[str, str]:
    """Build (subject, body_html) from live DB data. Called at insert and retry time."""
    return build_escalation_email(
        level=level,
        complaint_reference=complaint.reference_number,
        complaint_name=complaint.complaint_name,
        customer=complaint.customer or "",
        step_code=step.step_code,
        step_name=getattr(step, "step_name", None),
        hours_overdue=hours,
        due_date=step.due_date.isoformat() if step.due_date else "",
        cqt_email=complaint.cqt_email,
        quality_manager_email=complaint.quality_manager_email,
        plant_manager_email=complaint.plant_manager_email,
    )


# ── Main jobs ─────────────────────────────────────────────────────────────────

async def check_and_escalate_all(db: AsyncSession) -> None:
    """
    Scans all overdue steps and creates outbox entries + attempts sends.
    Called by the scheduler, which already holds the advisory lock.

    Each step is processed in its own SAVEPOINT so a failure on one step
    does not roll back changes already made for previous steps.
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
        step_id, step_code = step.id, step.step_code
        try:
            async with db.begin_nested():
                sent = await _process_step(db, step)
                if sent:
                    fired += 1
        except Exception:
            logger.exception(
                "Escalation error on step_id=%s (%s) — step rolled back, continuing",
                step_id, step_code,
            )

    await db.commit()
    logger.info("Escalation scan complete — %d email(s) queued/sent", fired)


async def retry_failed_emails(db: AsyncSession) -> None:
    """
    Retries:
      - 'failed' outbox entries whose next_retry_at has passed
      - 'pending' entries older than _STUCK_PENDING_THRESHOLD_MINUTES
        (flushed to DB but process crashed before _attempt_send ran)

    Called by the scheduler, which already holds the advisory lock.
    Each entry is retried in its own savepoint.

   
    """
    now = utc_now()
    stuck_threshold = now - timedelta(minutes=_STUCK_PENDING_THRESHOLD_MINUTES)

    result = await db.execute(
        select(EmailOutbox)
        .where(
            (
                (EmailOutbox.status == "failed")
                & (EmailOutbox.attempts < EmailOutbox.max_attempts)
                & (EmailOutbox.next_retry_at <= now)
            )
            |
            (
                (EmailOutbox.status == "pending")
                & (EmailOutbox.created_at <= stuck_threshold)
            )
        )
        .with_for_update(skip_locked=True)
    )
    entries = result.scalars().all()

    if not entries:
        logger.debug("Email retry: nothing to retry")
        return

    logger.info("Email retry: %d entry/entries to process", len(entries))

    for entry in entries:
        try:
            async with db.begin_nested():
                await _retry_outbox_entry(db, entry)
        except Exception:
            logger.exception(
                "Unexpected error retrying outbox_id=%s — skipping", entry.id
            )

    await db.commit()


# ── Step processing ───────────────────────────────────────────────────────────

async def _process_step(db: AsyncSession, step: ReportStep) -> bool:
    """
    Evaluate a single step and fire an escalation email if due.
    Returns True if an outbox entry was created (email queued or sent).
    Must be called inside a savepoint (db.begin_nested()) by the caller.

   
    """
    hours = _hours_overdue(step)
    complaint: Complaint = step.report.complaint

    logger.debug(
        "Step %s | %s | complaint=%s | due=%s | completed=%s | "
        "overdue=%s | escalation_count=%s | qm=%s | pm=%s",
        step.id, step.step_code, complaint.reference_number,
        step.due_date, step.completed_at,
        f"{hours:.2f}h" if hours else "N/A",
        step.escalation_count,
        complaint.quality_manager_email,
        complaint.plant_manager_email,
    )

    if hours is None:
        logger.debug("Step %s (%s): not overdue or completed — skip", step.id, step.step_code)
        return False

    level = _level_to_send(hours, step.escalation_count or 0)
    if level is None:
        logger.debug(
            "Step %s (%s): %.1fh overdue, escalation_count=%s — no new level to send",
            step.id, step.step_code, hours, step.escalation_count,
        )
        return False

    recipients = _build_recipients(level, complaint)
    if not recipients:
        logger.warning(
            "Step %s (%s): L%s due but NO RECIPIENTS — "
            "qm_email=%r pm_email=%r complaint=%s. "
            "Set quality_manager_email to receive L1 escalations.",
            step.id, step.step_code, level,
            complaint.quality_manager_email,
            complaint.plant_manager_email,
            complaint.reference_number,
        )
        return False

    cc = _build_cc(level, complaint)
    subject, body_html = _build_email(complaint, step, level, hours)

  

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

    # escalation_sent_at records when we last *attempted* — useful for ops visibility.
    step.escalation_sent_at = utc_now()

    outbox_entry = EmailOutbox(
        step_id=step.id,
        complaint_id=complaint.id,
        escalation_level=level,
        recipients=recipients,
        cc=cc,
        status="pending",
        attempts=0,
        next_retry_at=utc_now(),
    )
    db.add(outbox_entry)

    try:
        await db.flush()
    except IntegrityError:
        # Partial unique index blocked a duplicate pending row for this
        # (complaint_id, step_id, escalation_level) — another instance already
        # has this covered. Roll back just this savepoint and move on.
        logger.warning(
            "Step %s (%s): outbox entry for L%s already exists "
            "(duplicate blocked by unique index) — skipping",
            step.id, step.step_code, level,
        )
        raise  # caller's savepoint catches and rolls back cleanly

    assert outbox_entry.id is not None, (
        f"outbox_entry.id is None after flush for step_id={step.id} level={level}. "
        "Ensure AsyncSessionLocal is configured with expire_on_commit=False."
    )

    await _attempt_send(db, outbox_entry, step, complaint, hours, level, subject, body_html)
    return True


async def _attempt_send(
    db: AsyncSession,
    entry: EmailOutbox,
    step: ReportStep,
    complaint: Complaint,
    hours: float,
    level: int,
    subject: str,
    body_html: str,
) -> None:
    """
    Attempt email delivery. Updates outbox entry and step state in-place.
    Does NOT raise on send failure — failure is persisted for retry.

    """
    try:
        await send_email(
            subject=subject,
            recipients=entry.recipients,
            body_html=body_html,
            cc=entry.cc or None,
        )

        # ── Confirmed delivery — update all state ─────────────────────────────
        entry.attempts += 1
        entry.status = "sent"
        entry.sent_at = utc_now()
        entry.last_error = None


        step.escalation_count = level

        await log_escalation_sent(
            db,
            complaint_id=complaint.id,
            step_id=step.id,
            step_code=step.step_code,
            level=level,
            recipients=entry.recipients,
            template=f"step_overdue_l{level}",
            reason=(
                f"Step {step.step_code} is overdue by {_hours_label(hours)} "
                f"(deadline: {step.due_date.strftime('%Y-%m-%d %H:%M') if step.due_date else '?'}). "
                f"Escalation level {level} triggered."
            ),
        )

        logger.info(
            "✓ Escalation L%s sent | complaint=%s | step=%s | overdue=%s | to=%s",
            level, complaint.reference_number, step.step_code,
            _hours_label(hours), entry.recipients,
        )

    except Exception as exc:
        # ── Send failed — persist for retry, do NOT touch escalation_count ────
        entry.attempts += 1
        entry.status = "failed"
        entry.last_error = str(exc)[:500]

        delay_idx = min(entry.attempts - 1, len(_RETRY_BACKOFF_MINUTES) - 1)
        delay = _RETRY_BACKOFF_MINUTES[delay_idx]
        entry.next_retry_at = utc_now() + timedelta(minutes=delay)

        logger.error(
            "✗ Escalation L%s FAILED (outbox_id=%s) | step=%s | error: %s "
            "— escalation_count unchanged, retry in %dmin",
            level, entry.id, step.step_code, exc, delay,
        )
        # Do NOT re-raise — the failed state must be committed for retry pickup.


async def _retry_outbox_entry(db: AsyncSession, entry: EmailOutbox) -> None:
    """
    Retry a single outbox entry (failed or stuck-pending).
    Reloads step + complaint from DB — data may have changed since insert.
    Must be called inside a savepoint (db.begin_nested()) by the caller.

   
    """
    result = await db.execute(
        select(ReportStep)
        .where(ReportStep.id == entry.step_id)
        .options(selectinload(ReportStep.report).selectinload(Report.complaint))
    )
    step = result.scalar_one_or_none()

    if step is None:
        entry.status = "abandoned"
        entry.last_error = "Step no longer exists in DB"
        logger.warning(
            "Outbox entry %s abandoned — step_id=%s deleted",
            entry.id, entry.step_id,
        )
        return

    if step.completed_at is not None:
        entry.status = "abandoned"
        entry.last_error = "Step completed before retry — escalation no longer relevant"
        logger.info(
            "Outbox entry %s abandoned — step_id=%s was completed",
            entry.id, entry.step_id,
        )
        return

    complaint = step.report.complaint

    # hours=0.0 pollute log messages or the stored subject.
    hours_now = _hours_overdue(step)
    hours_for_body = hours_now if hours_now is not None else 0.0

    subject_rebuilt, body_html = _build_email(
        complaint, step, entry.escalation_level, hours_for_body
    )
    subject = entry.subject or subject_rebuilt  # stored subject is accurate to trigger time

    try:
        await send_email(
            subject=subject,
            recipients=entry.recipients,
            body_html=body_html,
            cc=entry.cc or None,
        )

        # ── Confirmed delivery ─────────────────────────────────────────────────
        entry.attempts += 1
        entry.status = "sent"
        entry.sent_at = utc_now()
        entry.last_error = None

        # this level on the next run
        step.escalation_count = entry.escalation_level

        overdue_ctx = _hours_label(hours_now) if hours_now is not None else "no longer overdue"
        logger.info(
            "✓ Retry OK (attempt %d) — outbox_id=%s step_id=%s | overdue=%s | "
            "escalation_count updated to %d",
            entry.attempts, entry.id, entry.step_id,
            overdue_ctx, entry.escalation_level,
        )

    except Exception as exc:
        entry.attempts += 1
        entry.last_error = str(exc)[:500]

        if entry.attempts >= entry.max_attempts:
            entry.status = "abandoned"
            logger.error(
                "✗ ABANDONED after %d attempts — outbox_id=%s step_id=%s | error: %s",
                entry.attempts, entry.id, entry.step_id, exc,
            )
        else:
            delay_idx = min(entry.attempts - 1, len(_RETRY_BACKOFF_MINUTES) - 1)
            delay = _RETRY_BACKOFF_MINUTES[delay_idx]
            entry.status = "failed"
            entry.next_retry_at = utc_now() + timedelta(minutes=delay)

            logger.warning(
                "✗ Retry %d/%d failed — outbox_id=%s step_id=%s | "
                "next attempt in %dmin | error: %s",
                entry.attempts, entry.max_attempts,
                entry.id, entry.step_id, delay, exc,
            )