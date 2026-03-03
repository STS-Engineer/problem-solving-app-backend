"""
app/services/scheduler.py

APScheduler setup for AVOCarbon 8D escalation checks.

Production : runs check_and_escalate_all() every 30 minutes.
Test mode  : set TEST_ESCALATION=true  → every 5 minutes, compressed SLA
             (D1 escalation fires after 30 min instead of 24 h)
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.db.session import AsyncSessionLocal
from app.services.escalation_service import check_and_escalate_all
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── Test / Production mode ────────────────────────────────────────────────────
TEST_MODE = os.getenv("TEST_ESCALATION", "false").lower() == "true"
CHECK_INTERVAL_MINUTES = 30 if TEST_MODE else 30


# ── Singleton ─────────────────────────────────────────────────────────────────
_scheduler: AsyncIOScheduler | None = None


async def _run_escalation_check() -> None:
    """Opens an async DB session and runs the escalation scan."""
    async with AsyncSessionLocal() as db:
        try:
            await check_and_escalate_all(db)
        except Exception:
            logger.exception("Escalation scheduler job failed")


def start_scheduler() -> None:
    global _scheduler

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_escalation_check,
        trigger=IntervalTrigger(minutes=CHECK_INTERVAL_MINUTES),
        id="escalation_check",
        name="8D Step Escalation Check",
        replace_existing=True,
        max_instances=1,          # never run two overlapping scans
        misfire_grace_time=300,   # 5-min grace if server was briefly down
    )
    _scheduler.start()

    mode = (
        f"TEST MODE — every {CHECK_INTERVAL_MINUTES} min, SLA ×(1/48)"
        if TEST_MODE
        else f"PRODUCTION — every {CHECK_INTERVAL_MINUTES} min"
    )
    logger.info("✅ Escalation scheduler started | %s", mode)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Escalation scheduler stopped")