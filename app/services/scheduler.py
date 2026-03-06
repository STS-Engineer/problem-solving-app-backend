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
CHECK_INTERVAL_MINUTES =  15 if TEST_MODE else 30
RETRY_INTERVAL_MINUTES = 10

_scheduler: AsyncIOScheduler | None = None


LOCK_ID_ESCALATION = 8_001
LOCK_ID_EMAIL_RETRY = 8_002


async def _run_with_lock(lock_id: int, job_fn, job_name: str) -> None:
    """
    Acquire pg_try_advisory_lock → run job_fn(db) → release lock.
    Centralised to avoid copy-pasting the try/finally for every job.

    FIX-A + FIX-B:
    The unlock is now in its own try/except. If job_fn raises an exception
    it may leave the SQLAlchemy connection in an aborted-transaction state
    (Postgres error state). Calling pg_advisory_unlock on an aborted
    connection raises an InFailedSqlTransaction error, which previously
    caused the lock to be silently retained.

    Fix sequence on job_fn failure:
      1. Catch the exception from job_fn.
      2. Attempt db.rollback() to clear the aborted transaction state.
      3. Attempt pg_advisory_unlock on the now-clean connection.
      4. If unlock still fails (e.g. network drop), log clearly — the lock
         will be released automatically when the pool recycles the connection
         (pool_recycle / pool_timeout). This is the expected fallback.
    """
    async with AsyncSessionLocal() as db:
        acquired = False
        try:
            result = await db.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            acquired = result.scalar()

            if not acquired:
                logger.debug(
                    "%s: advisory lock %d held by another instance — skipping.",
                    job_name, lock_id,
                )
                return

            await job_fn(db)

        except Exception:
            logger.exception("%s failed unexpectedly", job_name)

            if acquired:
                
                try:
                    await db.rollback()
                except Exception:
                    logger.exception(
                        "%s: rollback failed — connection may be broken", job_name
                    )

        finally:
           
            if acquired:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": lock_id},
                    )
                except Exception:
                    logger.exception(
                        "%s: failed to release advisory lock %d — "
                        "lock will be held until connection is recycled by the pool.",
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
        misfire_grace_time=300,  
    )

    _scheduler.add_job(
        _run_email_retry,
        trigger=IntervalTrigger(minutes=RETRY_INTERVAL_MINUTES),
        id="email_retry",
        name="Email Outbox Retry",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )

    _scheduler.start()
    mode = "TEST" if TEST_MODE else "PRODUCTION"
    logger.info(
        "Scheduler started [%s] — escalation every %dmin, retry every %dmin",
        mode,
        CHECK_INTERVAL_MINUTES,
        RETRY_INTERVAL_MINUTES,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
       
        _scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")


def is_scheduler_running() -> bool:
    """Exposed for /health/ready endpoint."""
    return _scheduler is not None and _scheduler.running