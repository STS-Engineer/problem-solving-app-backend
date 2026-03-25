"""
app/main.py
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.router import api_router
from app.db.session import AsyncSessionLocal, async_engine
from app.services.scheduler import is_scheduler_running, start_scheduler, stop_scheduler
 
import logging
from contextlib import asynccontextmanager
 
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
 
from app.core.config import get_webhook_settings
from app.services.webhook_service import prune_old_jobs, recover_locked_jobs, run_one_poll



# ── Logging ───────────────────────────────────────────────────────────────────

def _configure_logging() -> None:
    # 1. Define the format
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    # 2. Set specific levels for your important loggers
    # We want to see EVERYTHING from our app and the scheduler
    for name in ("app", "apscheduler"):
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        log.propagate = True

    # 3. SILENCE THE NOISE
    # These are the ones currently flooding your Azure Log Stream
    noise_loggers = [
        "azure",
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.monitor.opentelemetry",
        "opentelemetry",
        "httpx",  # Silences the OpenAI/External API request logs
    ]
    for name in noise_loggers:
        log = logging.getLogger(name)
        log.setLevel(logging.WARNING) # Only show errors/warnings
        log.propagate = True

    # 4. Configure Root logger
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


_configure_logging()
logger = logging.getLogger(__name__)  

# ── CORS ──────────────────────────────────────────────────────────────────────
_AZURE_URL = os.getenv(
    "AZURE_FRONTEND_URL",
    "https://avocarbon-customer-complaint.azurewebsites.net",
)
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    _AZURE_URL,
]
if extra := os.getenv("FRONTEND_URL"):
    origins.append(extra)

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting AVOCarbon Complaints API...")
 
    # Verify DB is reachable before accepting traffic
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        logger.info("Database connectivity OK.")
    except Exception as exc:
        logger.critical("Cannot connect to database at startup: %s", exc)
        raise
 
    start_scheduler()
    cfg = get_webhook_settings()
 
    scheduler = BackgroundScheduler(
        job_defaults={
            "coalesce":           True,   # skip missed runs, never stack
            "max_instances":      1,      # one instance of each job per process
            "misfire_grace_time": 60,
        }
    )
 
    # ── Job 1: delivery worker ────────────────────────────────────────────────
    # Picks up one pending WebhookJob and delivers it.
    # 120 s is a safe default for your complaint volume.
    # Override with WEBHOOK_POLL_INTERVAL env var if needed.
    scheduler.add_job(
        run_one_poll,
        trigger="interval",
        seconds=cfg.webhook_poll_interval,   # default 120
        id="webhook_poll",
        name="Webhook delivery worker",
    )
 
    # ── Job 2: locked-job recovery ────────────────────────────────────────────
    # Resets jobs stuck in `locked` state after a process crash.
    # Runs every 15 minutes — well above the 10-minute lock TTL.
    scheduler.add_job(
        recover_locked_jobs,
        trigger="interval",
        seconds=900,
        id="webhook_lock_recovery",
        name="Webhook locked-job recovery",
    )
    # ------- Job3---------------
    # Nightly cleanup — runs at 03:00 UTC Deletes done/failed WebhookJob rows older than 7 days.
    scheduler.add_job(
        prune_old_jobs,
        trigger="cron",
        hour=3,
        minute=0,
        id="webhook_prune",
    )
 
    scheduler.start()
    logger.info(
        "Scheduler started — jobs: poll=%ds, recovery=900s, prune=daily@03:00 UTC | "
        "targets=%d",
        cfg.webhook_poll_interval,
        len(cfg.target_urls),
    )
    yield
    # ── Graceful shutdown ─────────────────────────────────────────────────────
    logger.info("Shutting down scheduler...")
    scheduler.shutdown(wait=True)
    stop_scheduler()
    await async_engine.dispose()
    logger.info("AVOCarbon API stopped.")


app = FastAPI(title="AVOCarbon Complaints / 8D Report API", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")

# ── Health endpoints ──────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness probe — returns 200 as long as the process is running."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["ops"])
async def readiness() -> JSONResponse:
    """
    Readiness probe — checks DB connectivity.
    Returns 503 if the app is not ready to serve requests.
    Note: scheduler health is not checked here because APScheduler runs
    in a background thread and a scheduler failure should not take the
    entire app offline — the webhook delivery is best-effort.
    """
    checks: dict[str, str] = {}
 
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
 
    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=200 if overall == "ok" else 503,
    )