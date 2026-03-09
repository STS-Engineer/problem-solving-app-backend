"""
app/services/scheduler.py
"""
from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.escalation_service import check_and_escalate_all, retry_failed_emails
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

TEST_MODE = os.getenv("TEST_ESCALATION", "false").lower() == "true"
DEV_MODE  = os.getenv("DEV_MODE", "false").lower() == "true"

CHECK_INTERVAL_MINUTES = 2 if TEST_MODE else 30
RETRY_INTERVAL_MINUTES = 10

ESCALATION_MISFIRE_GRACE_S = 300   # 5 min — escalation is idempotent
EMAIL_RETRY_MISFIRE_GRACE_S = 120  # 2 min — email retry is time-sensitive

LOCK_ID_ESCALATION = 8_001
LOCK_ID_EMAIL_RETRY = 8_002

_scheduler: AsyncIOScheduler | None = None


async def _run_with_lock(lock_id: int, job_fn, job_name: str) -> None:
    """
    Acquire a Postgres advisory lock, run job_fn(db), then release the lock.
    Only one instance across all deployed replicas will execute the job per interval.

    In DEV_MODE the advisory lock is skipped entirely — the reloader spawns two
    processes locally which causes the second to always see the lock as held.
    """
    async with AsyncSessionLocal() as db:

        # ── Dev mode: skip advisory lock ──────────────────────────────────────
        if DEV_MODE:
            try:
                await job_fn(db)
                await db.commit()
            except Exception:
                logger.exception("%s failed unexpectedly", job_name)
                try:
                    await db.rollback()
                except Exception:
                    logger.exception("%s: rollback failed", job_name)
            return

        # ── Production: advisory lock ─────────────────────────────────────────

        # 1. Acquire
        try:
            result = await db.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            acquired = result.scalar()
        except Exception:
            logger.exception(
                "%s: failed to acquire advisory lock %d — skipping run",
                job_name, lock_id,
            )
            return

        if not acquired:
            logger.debug(
                "%s: advisory lock %d held by another instance — skipping.",
                job_name, lock_id,
            )
            return

        # 2. Run
        job_failed = False
        try:
            await job_fn(db)
        except Exception:
            logger.exception("%s failed unexpectedly", job_name)
            job_failed = True

        # 3. Release
        try:
            if job_failed:
                await db.rollback()  # clear aborted transaction before unlock
            await db.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            )
            await db.commit()  # flush the unlock to Postgres
        except Exception:
            logger.exception(
                "%s: failed to release advisory lock %d — "
                "lock will be held until the connection is recycled by the pool.",
                job_name, lock_id,
            )


async def _run_escalation_check() -> None:
    await _run_with_lock(LOCK_ID_ESCALATION, check_and_escalate_all, "Escalation check")


async def _run_email_retry() -> None:
    await _run_with_lock(LOCK_ID_EMAIL_RETRY, retry_failed_emails, "Email retry")


def start_scheduler() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")

    _scheduler.add_job(
        _run_escalation_check,
        trigger=IntervalTrigger(minutes=CHECK_INTERVAL_MINUTES),
        id="escalation_check",
        name="8D Step Escalation Check",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=ESCALATION_MISFIRE_GRACE_S,
    )
    _scheduler.add_job(
        _run_email_retry,
        trigger=IntervalTrigger(minutes=RETRY_INTERVAL_MINUTES),
        id="email_retry",
        name="Email Outbox Retry",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=EMAIL_RETRY_MISFIRE_GRACE_S,
    )

    _scheduler.start()
    mode = "TEST" if TEST_MODE else "PRODUCTION"
    dev  = " + DEV_MODE (no advisory lock)" if DEV_MODE else ""
    logger.info(
        "Scheduler started [%s%s] — escalation every %dmin, email retry every %dmin",
        mode, dev, CHECK_INTERVAL_MINUTES, RETRY_INTERVAL_MINUTES,
    )


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