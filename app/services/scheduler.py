"""
app/services/scheduler.py
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.escalation_service import check_and_escalate_all, retry_failed_emails

logger = logging.getLogger(__name__)
load_dotenv()

TEST_MODE = os.getenv("TEST_ESCALATION", "false").lower() == "true"
DEV_MODE  = os.getenv("DEV_MODE", "false").lower() == "true"

CHECK_INTERVAL_MINUTES = 3 if TEST_MODE else 30
RETRY_INTERVAL_MINUTES = 10

LOCK_ID_ESCALATION = 8_001
LOCK_ID_EMAIL_RETRY = 8_002

_scheduler: BackgroundScheduler | None = None


# ── Advisory lock helpers (sync) ──────────────────────────────────────────────

def _try_acquire_lock(db, lock_id: int) -> bool:
    result = db.execute(
        text("SELECT pg_try_advisory_lock(:lock_id)"),
        {"lock_id": lock_id}
    )
    return result.scalar()


def _release_lock(db, lock_id: int) -> None:
    db.execute(
        text("SELECT pg_advisory_unlock(:lock_id)"),
        {"lock_id": lock_id}
    )
    db.commit()


# ── Core job runner ───────────────────────────────────────────────────────────

def _run_job(lock_id: int, job_fn, job_name: str) -> None:
    """
    Run a sync job function with an optional Postgres advisory lock.
    DEV_MODE skips the lock to avoid false conflicts from the uvicorn reloader.
    """
    db = SessionLocal()
    try:
        if not DEV_MODE:
            try:
                acquired = _try_acquire_lock(db, lock_id)
            except Exception:
                logger.exception("%s: failed to acquire advisory lock — skipping", job_name)
                return

            if not acquired:
                logger.debug(
                    "%s: advisory lock %d held by another instance — skipping.",
                    job_name, lock_id,
                )
                return

        try:
            job_fn(db)
            db.commit()
        except Exception:
            logger.exception("%s failed unexpectedly", job_name)
            db.rollback()
        finally:
            if not DEV_MODE:
                try:
                    _release_lock(db, lock_id)
                except Exception:
                    logger.exception(
                        "%s: failed to release advisory lock %d — "
                        "will be released when connection is recycled.",
                        job_name, lock_id,
                    )
    finally:
        db.close()


# ── Job wrappers ──────────────────────────────────────────────────────────────

def _run_escalation_check() -> None:
    _run_job(LOCK_ID_ESCALATION, check_and_escalate_all, "Escalation check")


def _run_email_retry() -> None:
    _run_job(LOCK_ID_EMAIL_RETRY, retry_failed_emails, "Email retry")


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,       # if a job was missed multiple times, run it once
            "max_instances": 1,     # never overlap
            "misfire_grace_time": 300,
        },
    )
    

    _scheduler.add_job(
        _run_escalation_check,
        trigger=IntervalTrigger(minutes=CHECK_INTERVAL_MINUTES),
        id="escalation_check",
        name="8D Step Escalation Check",
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_email_retry,
        trigger=IntervalTrigger(minutes=RETRY_INTERVAL_MINUTES),
        id="email_retry",
        name="Email Outbox Retry",
        replace_existing=True,
    )

    _scheduler.start()
    mode = "TEST" if TEST_MODE else "PRODUCTION"
    dev  = " + DEV_MODE (no advisory lock)" if DEV_MODE else ""
    logger.info(
        "Scheduler started [%s%s] — escalation every %dmin, email retry every %dmin",
        mode, dev, CHECK_INTERVAL_MINUTES, RETRY_INTERVAL_MINUTES,
    )
    for job in _scheduler.get_jobs():
        logger.info("Scheduled: %s — next run at %s", job.name, job.next_run_time)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def is_scheduler_running() -> bool:
    """Exposed for /health/ready — confirms scheduler is up AND both jobs are registered."""
    if _scheduler is None or not _scheduler.running:
        return False
    job_ids = {job.id for job in _scheduler.get_jobs()}
    return {"escalation_check", "email_retry"}.issubset(job_ids)
