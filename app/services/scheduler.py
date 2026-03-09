"""
app/services/scheduler.py

Uses BackgroundScheduler (thread-based) instead of AsyncIOScheduler because
Azure App Service's process model interferes with AsyncIOScheduler's event loop
attachment, causing jobs to silently not execute despite the scheduler reporting
as healthy.

Bridge pattern: BackgroundScheduler fires jobs in threads. Each thread uses
asyncio.run_coroutine_threadsafe() to schedule the async job onto the main
FastAPI event loop and blocks until it completes. This reuses the existing
async engine / connection pool instead of creating a new one per job.
"""
from __future__ import annotations

import asyncio
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from app.db.session import AsyncSessionLocal
from app.services.escalation_service import check_and_escalate_all, retry_failed_emails
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

TEST_MODE = os.getenv("TEST_ESCALATION", "false").lower() == "true"
DEV_MODE  = os.getenv("DEV_MODE", "false").lower() == "true"

CHECK_INTERVAL_MINUTES = 2 if TEST_MODE else 5
RETRY_INTERVAL_MINUTES = 10

ESCALATION_MISFIRE_GRACE_S = 300   # 5 min — escalation is idempotent
EMAIL_RETRY_MISFIRE_GRACE_S = 120  # 2 min — email retry is time-sensitive

LOCK_ID_ESCALATION = 8_001
LOCK_ID_EMAIL_RETRY = 8_002

_scheduler: BackgroundScheduler | None = None
_main_loop: asyncio.AbstractEventLoop | None = None


# ── Core async job logic ──────────────────────────────────────────────────────

async def _run_with_lock(lock_id: int, job_fn, job_name: str) -> None:
    """
    Acquire a Postgres advisory lock, run job_fn(db), then release the lock.
    Only one instance across all deployed replicas will execute the job per interval.

    In DEV_MODE the advisory lock is skipped — the reloader spawns two processes
    locally which causes the second to always see the lock as held.
    """
    async with AsyncSessionLocal() as db:

        # ── Dev mode: no advisory lock ────────────────────────────────────────
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
                await db.rollback()
            await db.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            )
            await db.commit()
        except Exception:
            logger.exception(
                "%s: failed to release advisory lock %d — "
                "lock will be held until the connection is recycled by the pool.",
                job_name, lock_id,
            )


# ── Thread → event loop bridge ────────────────────────────────────────────────

def _dispatch(coro) -> None:
    """
    Submit a coroutine to the main FastAPI event loop from a background thread
    and block until it completes. This reuses the existing async engine and
    connection pool rather than creating a new one per job invocation.
    """
    if _main_loop is None or _main_loop.is_closed():
        logger.error("Main event loop is not available — job skipped")
        return
    future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    try:
        future.result()  # block the scheduler thread until the coroutine finishes
    except Exception:
        logger.exception("Job raised an exception on the main event loop")


# ── Job wrappers (sync — called by BackgroundScheduler threads) ───────────────

def _run_escalation_check() -> None:
    _dispatch(_run_with_lock(LOCK_ID_ESCALATION, check_and_escalate_all, "Escalation check"))


def _run_email_retry() -> None:
    _dispatch(_run_with_lock(LOCK_ID_EMAIL_RETRY, retry_failed_emails, "Email retry"))


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def start_scheduler() -> None:
    global _scheduler, _main_loop

    # Capture the FastAPI event loop at startup — must be called from async context
    _main_loop = asyncio.get_event_loop()

    _scheduler = BackgroundScheduler(timezone="UTC")

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
        # wait=False: jobs dispatch onto the main loop which is already shutting down
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def is_scheduler_running() -> bool:
    """Exposed for /health/ready — confirms scheduler is up AND both jobs are registered."""
    if _scheduler is None or not _scheduler.running:
        return False
    job_ids = {job.id for job in _scheduler.get_jobs()}
    return {"escalation_check", "email_retry"}.issubset(job_ids)