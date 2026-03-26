"""
app/services/escalation_service.py

Production-ready escalation service with Outbox pattern.
Fully synchronous — designed for BackgroundScheduler (thread-based).

Architecture:
  - check_and_escalate_all(db)  : called by scheduler every 30 min
  - retry_failed_emails(db)     : called by scheduler every 10 min

  Alert levels:
    L1 (24h overdue) → quality_manager_email
    L2 (48h overdue) → plant_manager_email
    L3 (72h overdue) → COO
    L4 (96h overdue) → CEO


Outbox pattern:
  1. Insert outbox(pending) + update step markers → flush
  2. Attempt SMTP send
  3a. Success → outbox(sent) + step.escalation_count = level
  3b. Failure → outbox(failed) + exponential backoff next_retry_at
  4. retry_failed_emails() picks up failed + stuck-pending entries
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload

from app.models.complaint import Complaint
from app.models.email_outbox import EmailOutbox
from app.models.report import Report
from app.models.report_step import ReportStep
from app.services.email_templates import build_escalation_email
from app.services.audit_service import log_event_sync as _log_event
from app.services.utils.datetime_utils import utc_now
from app.core.email import _send_sync as _send_email

logger = logging.getLogger(__name__)

# ── SLA config ────────────────────────────────────────────────────────────────
COO_EMAIL = os.getenv("COO_EMAIL", "hayfa.rajhi@avocarbon.com")
CEO_EMAIL = os.getenv("CEO_EMAIL", "hayfa.rajhi@avocarbon.com")

_RETRY_BACKOFF_MINUTES = [10, 30, 60]  # attempt 1→10min, 2→30min, 3+→60min
_STUCK_PENDING_THRESHOLD_MINUTES = 45  # must be > scheduler check interval (30min)


# ── Threshold helpers ─────────────────────────────────────────────────────────


def _get_thresholds() -> list[tuple[float, int]]:
    """
    Production : L1=24h, L2=48h, L3=72h, L4=96h
    Test mode  : L1=2min, L2=4min, L3=6min, L4=8min (set due_date ~3min ago to test)
    Re-read env each call so TEST_ESCALATION is hot-reloadable without restart.
    """
    if os.getenv("TEST_ESCALATION", "false").lower() == "true":
        m = 1 / 60  # 1 minute as a fraction of an hour
        return [(2 * m, 1), (4 * m, 2), (6 * m, 3), (8 * m, 4)]
    return [(24.0, 1), (48.0, 2), (72.0, 3), (96.0, 4)]


def _is_test_mode() -> bool:
    return os.getenv("TEST_ESCALATION", "false").lower() == "true"


def _hours_label(hours: float) -> str:
    return f"{hours * 60:.0f}min" if _is_test_mode() else f"{hours:.1f}h"


# ── Domain helpers ────────────────────────────────────────────────────────────


def _hours_overdue(step: ReportStep) -> float | None:
    """Returns hours overdue, or None if completed or not yet overdue."""
    if step.completed_at is not None or not step.due_date:
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
    Return the next escalation level to send, or None if nothing to send.
    already_sent = step.escalation_count (highest level CONFIRMED delivered).
    Never pass a speculatively incremented count here.
    """
    triggered = max(
        (lvl for thr, lvl in _get_thresholds() if hours >= thr),
        default=0,
    )
    if triggered == 0:
        return None
    next_level = already_sent + 1
    return next_level if next_level <= triggered else None


def _build_recipients(level: int, complaint: Complaint) -> list[str]:
    # FIX-5: always return a new list
    match level:
        case 1:
            return [e for e in [complaint.quality_manager_email] if e]
        case 2:
            return [e for e in [complaint.plant_manager_email] if e]
        case 3:
            return [COO_EMAIL]
        case 4:
            return [CEO_EMAIL]
        case _:
            return []


def _build_cc(level: int, complaint: Complaint) -> list[str] | None:
    # FIX-5: always return a new list
    match level:
        case 3:
            cc = [
                e
                for e in [
                    complaint.plant_manager_email,
                    complaint.quality_manager_email,
                ]
                if e
            ]
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
    """Build (subject, body_html). Called at first send and on retry."""
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


# ── Main jobs (called by scheduler.py) ───────────────────────────────────────


def check_and_escalate_all(db: Session) -> None:
    """
    Scan all overdue steps and send escalation emails.
    Each step is committed independently — one failure does not affect others.
    """
    steps = (
        db.query(ReportStep)
        .filter(
            ReportStep.completed_at.is_(None),
            ReportStep.due_date.isnot(None),
        )
        .options(joinedload(ReportStep.report).joinedload(Report.complaint))
        .all()
    )

    logger.info("Escalation scan: %d active step(s) with due_date", len(steps))
    fired = 0

    for step in steps:
        try:
            if _process_step(db, step):
                db.commit()
                fired += 1
        except Exception:
            logger.exception(
                "Escalation error on step_id=%s (%s) — rolling back, continuing",
                step.id,
                step.step_code,
            )
            db.rollback()

    logger.info("Escalation scan complete — %d email(s) queued/sent", fired)


def retry_failed_emails(db: Session) -> None:
    """
    Retry failed and stuck-pending outbox entries.

    FIX-1: SELECT FOR UPDATE SKIP LOCKED prevents two Azure instances from
    picking up the same row simultaneously — safe, non-blocking.
    """
    now = utc_now()
    stuck_threshold = now - timedelta(minutes=_STUCK_PENDING_THRESHOLD_MINUTES)

    entries = (
        db.query(EmailOutbox)
        .filter(
            (
                (EmailOutbox.status == "failed")
                & (EmailOutbox.attempts < EmailOutbox.max_attempts)
                & (EmailOutbox.next_retry_at <= now)
            )
            | (
                (EmailOutbox.status == "pending")
                & (EmailOutbox.created_at <= stuck_threshold)
            )
        )
        .with_for_update(skip_locked=True)
        .all()
    )

    if not entries:
        logger.debug("Email retry: nothing to retry")
        return

    logger.info("Email retry: %d entry/entries to process", len(entries))

    for entry in entries:
        try:
            _retry_outbox_entry(db, entry)
            db.commit()
        except Exception:
            logger.exception("Error retrying outbox_id=%s — skipping", entry.id)
            db.rollback()


# ── Step processing ───────────────────────────────────────────────────────────


def _process_step(db: Session, step: ReportStep) -> bool:
    """
    Evaluate one step. Returns True if an outbox entry was created.
    escalation_count is NOT updated here — only on confirmed delivery (FIX-3).
    """
    hours = _hours_overdue(step)
    complaint = step.report.complaint

    logger.debug(
        "Step %s | %s | complaint=%s | due=%s | overdue=%s | escalation_count=%s | qm=%s | pm=%s",
        step.id,
        step.step_code,
        complaint.reference_number,
        step.due_date,
        f"{hours:.2f}h" if hours else "N/A",
        step.escalation_count,
        complaint.quality_manager_email,
        complaint.plant_manager_email,
    )

    if hours is None:
        logger.debug(
            "Step %s (%s): not overdue or completed — skip", step.id, step.step_code
        )
        return False

    level = _level_to_send(hours, step.escalation_count or 0)
    if level is None:
        logger.debug(
            "Step %s (%s): %.1fh overdue, escalation_count=%s — no new level to send",
            step.id,
            step.step_code,
            hours,
            step.escalation_count,
        )
        return False

    recipients = _build_recipients(level, complaint)
    if not recipients:
        logger.warning(
            "Step %s (%s): L%s due but NO RECIPIENTS — qm=%r pm=%r complaint=%s",
            step.id,
            step.step_code,
            level,
            complaint.quality_manager_email,
            complaint.plant_manager_email,
            complaint.reference_number,
        )
        return False

    cc = _build_cc(level, complaint)
    subject, body = _build_email(complaint, step, level, hours)

    # Mark overdue on first escalation only
    if (step.escalation_count or 0) == 0:
        _log_event(
            db,
            complaint_id=complaint.id,
            step_id=step.id,
            step_code=step.step_code,
            event_type="due_date_missed",
            event_data={
                "due_date": step.due_date.isoformat(),
                "missed_by_hours": round(hours, 2),
            },
        )
        # step.is_overdue = True
        # step.status = "overdue"

    # Record attempt timestamp (not success — just that we tried)
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
        db.flush()  # assign ID + enforce unique index
    except Exception:
        logger.warning(
            "Step %s (%s): outbox entry for L%s already exists — skipping",
            step.id,
            step.step_code,
            level,
        )
        raise  # caller rolls back this step only

    _attempt_send(db, outbox_entry, step, complaint, hours, level, subject, body)
    return True


def _attempt_send(
    db: Session,
    entry: EmailOutbox,
    step: ReportStep,
    complaint: Complaint,
    hours: float,
    level: int,
    subject: str,
    body_html: str,
) -> None:
    """
    Attempt SMTP send. Updates escalation_count ONLY on success (FIX-3).
    Persists failure state for retry — does not raise.
    """
    try:
        _send_email(
            subject=subject,
            recipients=entry.recipients,
            body_html=body_html,
            cc=entry.cc or None,
        )

        entry.attempts += 1
        entry.status = "sent"
        entry.sent_at = utc_now()
        entry.last_error = None
        step.escalation_count = level  # FIX-3: confirmed delivery only

        _log_event(
            db,
            complaint_id=complaint.id,
            step_id=step.id,
            step_code=step.step_code,
            event_type="escalation_sent",
            event_data={
                "level": level,
                "recipients": entry.recipients,
                "template": f"step_overdue_l{level}",
                "reason": (
                    f"Step {step.step_code} overdue by {_hours_label(hours)} "
                    f"(deadline: {step.due_date.strftime('%Y-%m-%d %H:%M') if step.due_date else '?'}). "
                    f"Escalation level {level} triggered."
                ),
            },
        )

        logger.info(
            "✓ L%s sent | complaint=%s | step=%s | overdue=%s | to=%s",
            level,
            complaint.reference_number,
            step.step_code,
            _hours_label(hours),
            entry.recipients,
        )

    except Exception as exc:
        entry.attempts += 1
        entry.status = "failed"
        entry.last_error = str(exc)[:500]

        # FIX-2: backoff by attempt count, not hardcoded 0
        delay_idx = min(entry.attempts - 1, len(_RETRY_BACKOFF_MINUTES) - 1)
        delay = _RETRY_BACKOFF_MINUTES[delay_idx]
        entry.next_retry_at = utc_now() + timedelta(minutes=delay)

        logger.error(
            "✗ L%s FAILED (outbox_id=%s) | step=%s | error: %s — retry in %dmin",
            level,
            entry.id,
            step.step_code,
            exc,
            delay,
        )
        # Do NOT raise — failed state must be committed so retry picks it up


def _retry_outbox_entry(db: Session, entry: EmailOutbox) -> None:
    """
    Retry one outbox entry. Reloads step + complaint from DB (data may have changed).
    Updates step.escalation_count on confirmed success (FIX-4).

    FIX-6: Uses stored subject (accurate to original trigger time) rather than
    recomputing from current hours which may be 0 if due_date was extended.
    """
    step = (
        db.query(ReportStep)
        .filter(ReportStep.id == entry.step_id)
        .options(joinedload(ReportStep.report).joinedload(Report.complaint))
        .one_or_none()
    )

    if step is None:
        entry.status = "abandoned"
        entry.last_error = "Step no longer exists in DB"
        logger.warning(
            "Outbox %s abandoned — step_id=%s deleted", entry.id, entry.step_id
        )
        return

    if step.completed_at is not None:
        entry.status = "abandoned"
        entry.last_error = "Step completed before retry — no longer relevant"
        logger.info(
            "Outbox %s abandoned — step_id=%s completed", entry.id, entry.step_id
        )
        return

    complaint = step.report.complaint
    hours_now = _hours_overdue(step)  # may be None if due_date was pushed out
    hours_for_body = hours_now if hours_now is not None else 0.0

    # FIX-6: rebuild body with fresh data but keep the stored subject
    _, body_html = _build_email(complaint, step, entry.escalation_level, hours_for_body)
    subject = f"[Escalation L{entry.escalation_level}] Step {step.step_code}"

    try:
        _send_email(
            subject=subject,
            recipients=entry.recipients,
            body_html=body_html,
            cc=entry.cc or None,
        )

        entry.attempts += 1
        entry.status = "sent"
        entry.sent_at = utc_now()
        entry.last_error = None
        step.escalation_count = entry.escalation_level  # FIX-4

        ctx = _hours_label(hours_now) if hours_now is not None else "no longer overdue"
        logger.info(
            "✓ Retry OK (attempt %d) — outbox_id=%s step=%s | %s | escalation_count → %d",
            entry.attempts,
            entry.id,
            step.step_code,
            ctx,
            entry.escalation_level,
        )

    except Exception as exc:
        entry.attempts += 1
        entry.last_error = str(exc)[:500]

        if entry.attempts >= entry.max_attempts:
            entry.status = "abandoned"
            logger.error(
                "✗ ABANDONED after %d attempts — outbox_id=%s | error: %s",
                entry.attempts,
                entry.id,
                exc,
            )
        else:
            delay_idx = min(entry.attempts - 1, len(_RETRY_BACKOFF_MINUTES) - 1)
            delay = _RETRY_BACKOFF_MINUTES[delay_idx]
            entry.status = "failed"
            entry.next_retry_at = utc_now() + timedelta(minutes=delay)
            logger.warning(
                "✗ Retry %d/%d failed — outbox_id=%s | next in %dmin | error: %s",
                entry.attempts,
                entry.max_attempts,
                entry.id,
                delay,
                exc,
            )
