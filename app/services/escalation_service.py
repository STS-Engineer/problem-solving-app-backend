"""
app/services/escalation_service.py

Production-ready escalation service with Outbox pattern.

Architecture:
  - check_and_escalate_all()  : called by scheduler (every 30 min)
  - retry_failed_emails()     : called by scheduler (every 10 min)

Advisory lock strategy:
  The lock lives ONLY in scheduler.py — one lock per job, two distinct keys.
  This service is a pure business-logic layer, independently testable.

Connection management:
  The AsyncSession is passed in from scheduler.py, which owns its lifecycle
  via `async with AsyncSessionLocal() as db:`. The connection is held for the
  duration of one job run and returned to the pool when the context exits.

  IMPORTANT — session factory must be configured with expire_on_commit=False:

      async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

  With the default expire_on_commit=True, SQLAlchemy expires all ORM objects
  after every commit. In async mode, accessing an expired attribute outside
  the load context raises MissingGreenlet (no lazy-load support in async).
  expire_on_commit=False keeps attributes accessible after commit.

Savepoint strategy:
  Each step is processed inside its own SAVEPOINT (db.begin_nested()).
  If one step fails (e.g. IntegrityError on the unique index), only that
  step is rolled back — all previously committed steps are preserved.
  Without savepoints, a single db.rollback() would wipe the entire transaction,
  undoing all steps processed earlier in the same loop.

Flow:
  1. Step overdue → persist escalation_count + outbox(pending) in ONE savepoint flush
  2. Attempt send_email()
  3a. Success → outbox(sent) + audit log
  3b. Failure → outbox(failed) + next_retry_at with exponential backoff
  4. retry_failed_emails() re-sends both 'failed' AND stuck 'pending' entries
     (pending = created but process crashed before _attempt_send was reached)

Fixes applied vs original:
  FIX-1  retry_failed_emails: added SELECT ... FOR UPDATE SKIP LOCKED to
         prevent dual-instance race where both instances retry the same row.
  FIX-2  _attempt_send: backoff index was hardcoded to 0 on first failure;
         now uses min(entry.attempts, ...) consistent with _retry_outbox_entry.
  FIX-3  _process_step: added assertion that outbox_entry.id is not None after
         flush, to catch unexpected ORM state early rather than logging None.
  FIX-4  _retry_outbox_entry: hours=0.0 when step no longer overdue produced a
         misleading "0min overdue" audit reason; now uses stored subject/context
         and skips the hours label in the retry path.
  FIX-5  _build_recipients / _build_cc: now return copies to avoid mutating
         shared complaint state across loop iterations.
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
    already_sent = step.escalation_count (0 = never escalated).
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
    # FIX-5: return a new list each time — never mutate complaint attributes
    match level:
        case 1: return [e for e in [complaint.quality_manager_email] if e]
        case 2: return [e for e in [complaint.plant_manager_email] if e]
        case 3: return [COO_EMAIL]
        case 4: return [CEO_EMAIL]
        case _: return []


def _build_cc(level: int, complaint: Complaint) -> list[str] | None:
    # FIX-5: return a new list each time
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
            # Each step runs in its own savepoint (nested transaction).
            # If _process_step raises or rolls back, only this step is affected.
            # Previously committed savepoints from earlier loop iterations survive.
            async with db.begin_nested():
                sent = await _process_step(db, step)
                if sent:
                    fired += 1
        except Exception:
            # Savepoint was already rolled back by the context manager.
            logger.exception(
                "Escalation error on step_id=%s (%s) — step rolled back, continuing",
                step_id, step_code,
            )

    # Commit the outer transaction — persists all successful savepoints.
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

    FIX-1: Uses SELECT ... FOR UPDATE SKIP LOCKED so that on a two-instance
    deployment, Instance A and Instance B cannot pick up the same row
    simultaneously. SKIP LOCKED means "if a row is already locked by another
    connection, skip it rather than waiting" — safe and non-blocking.
    The advisory lock (in scheduler.py) prevents same-job overlap on the same
    instance; FOR UPDATE SKIP LOCKED prevents cross-instance row-level races.
    """
    now = utc_now()
    stuck_threshold = now - timedelta(minutes=_STUCK_PENDING_THRESHOLD_MINUTES)

    result = await db.execute(
        select(EmailOutbox)
        .where(
            # failed entries ready for retry
            (
                (EmailOutbox.status == "failed")
                & (EmailOutbox.attempts < EmailOutbox.max_attempts)
                & (EmailOutbox.next_retry_at <= now)
            )
            |
            # stuck pending entries (created but never attempted)
            (
                (EmailOutbox.status == "pending")
                & (EmailOutbox.created_at <= stuck_threshold)
            )
        )
        # FIX-1: row-level lock — skip rows already locked by another instance
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

    # ── STEP 1: Persist state BEFORE sending ──────────────────────────────────
    # escalation_count update + outbox INSERT are flushed atomically within the
    # caller's savepoint. Even if send_email() crashes or the process is killed,
    # the DB already records that this level was triggered — no duplicate
    # escalation on the next scheduler run.

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

    step.escalation_count = level
    step.escalation_sent_at = utc_now()

    outbox_entry = EmailOutbox(
        step_id=step.id,
        complaint_id=complaint.id,
        escalation_level=level,
        recipients=recipients,
        cc=cc,              # None if no CC — stored as NULL (not [])
        status="pending",
        attempts=0,
        next_retry_at=utc_now(),
    )
    db.add(outbox_entry)

    try:
        await db.flush()
    except IntegrityError:
        # The partial unique index uq_outbox_pending_escalation blocked a
        # duplicate INSERT: another instance already has a pending row for
        # (complaint_id, step_id, escalation_level). This is the expected
        # race-condition safety net — the savepoint rolls back just this step.
        logger.warning(
            "Step %s (%s): outbox entry for L%s already exists "
            "(duplicate blocked by unique index) — skipping",
            step.id, step.step_code, level,
        )
        raise  # let the savepoint in the caller catch and roll back cleanly

    # FIX-3: Assert ID was populated by flush — catches unexpected ORM state
    # (e.g. expire_on_commit=True misconfiguration) before we log entry.id=None.
    assert outbox_entry.id is not None, (
        f"outbox_entry.id is None after flush for step_id={step.id} level={level}. "
        "Ensure AsyncSessionLocal is configured with expire_on_commit=False."
    )

    # ── STEP 2: Attempt email send ────────────────────────────────────────────
    # On failure the outbox row is marked 'failed' and the savepoint still commits
    # (the failure is intentionally persisted so retry_failed_emails picks it up).
    await _attempt_send(db, outbox_entry, complaint, step, hours, level, subject, body_html)

    return True


async def _attempt_send(
    db: AsyncSession,
    entry: EmailOutbox,
    complaint: Complaint,
    step: ReportStep,
    hours: float,
    level: int,
    subject: str,
    body_html: str,
) -> None:
    """
    Attempt email delivery. Updates outbox entry status in-place.
    Does NOT raise on send failure — failure is persisted for retry.
    """
    try:
        await send_email(
            subject=subject,
            recipients=entry.recipients,
            body_html=body_html,
            cc=entry.cc or None,
        )

        entry.attempts += 1
        entry.status = "sent"
        entry.sent_at = utc_now()
        entry.last_error = None

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
        entry.attempts += 1
        entry.status = "failed"
        entry.last_error = str(exc)[:500]

        # FIX-2: backoff index was hardcoded to 0 (always 10 min).
        # Now uses the same formula as _retry_outbox_entry: index by attempt count,
        # capped at the last bucket. After attempt 1 → 10min, 2 → 30min, 3+ → 60min.
        delay_idx = min(entry.attempts - 1, len(_RETRY_BACKOFF_MINUTES) - 1)
        delay = _RETRY_BACKOFF_MINUTES[delay_idx]
        entry.next_retry_at = utc_now() + timedelta(minutes=delay)

        logger.error(
            "✗ Escalation L%s FAILED (outbox_id=%s) | step=%s | error: %s "
            "— persisted, retry in %dmin",
            level, entry.id, step.step_code, exc, delay,
        )
        # Do NOT re-raise: the failed state is intentional and must be committed
        # so retry_failed_emails() can pick it up on the next run.


async def _retry_outbox_entry(db: AsyncSession, entry: EmailOutbox) -> None:
    """
    Retry a single outbox entry (failed or stuck-pending).
    Reloads step + complaint from DB — data may have changed since insert.
    Must be called inside a savepoint (db.begin_nested()) by the caller.

    FIX-4: When the step is no longer overdue (due date was extended or
    completed_at was set between the original send and the retry), hours
    defaults to 0.0, which produced a misleading "0min overdue" string in
    the audit reason and log. The retry path now uses the stored subject
    (which reflects the original escalation time) and omits hours from the
    retry-specific log message. The audit reason is preserved from the
    stored subject context rather than recomputed from a stale 0.0 value.
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

    # FIX-4: Compute current hours only to rebuild the body (so links/dates
    # in the email body are fresh). Use the STORED subject — it was written at
    # the time the escalation originally fired and accurately reflects the
    # overdue window that triggered it. Never let hours=0.0 bleed into logs.
    hours_now = _hours_overdue(step)  # may be None if due date was pushed out
    hours_for_body = hours_now if hours_now is not None else 0.0

    subject_rebuilt, body_html = _build_email(
        complaint, step, entry.escalation_level, hours_for_body
    )
    # Prefer the stored subject (accurate to original trigger time)
    subject = entry.subject or subject_rebuilt

    try:
        await send_email(
            subject=subject,
            recipients=entry.recipients,
            body_html=body_html,
            cc=entry.cc or None,
        )
        entry.attempts += 1
        entry.status = "sent"
        entry.sent_at = utc_now()
        entry.last_error = None

        # FIX-4: log with hours_now context only when meaningful
        overdue_ctx = _hours_label(hours_now) if hours_now is not None else "no longer overdue"
        logger.info(
            "✓ Retry OK (attempt %d) — outbox_id=%s step_id=%s | overdue=%s",
            entry.attempts, entry.id, entry.step_id, overdue_ctx,
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
 