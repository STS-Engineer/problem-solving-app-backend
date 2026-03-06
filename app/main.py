"""
app/main.py
"""
from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.router import api_router
from app.db.session import AsyncSessionLocal
from app.services.scheduler import is_scheduler_running, start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── CORS ─────────────────────────────────────────────────────────────────────
# Hardcoded dev origins + env-configurable production origin.
# Do NOT use allow_origins=["*"] with allow_credentials=True — browsers block it.
_AZURE_URL = os.getenv(
    "AZURE_FRONTEND_URL",
    "https://avocarbon-customer-complaint.azurewebsites.net",  # fallback
)
origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    _AZURE_URL,
]
extra = os.getenv("FRONTEND_URL")
if extra:
    origins.append(extra)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting AVOCarbon API...")

    # Verify DB connectivity before accepting traffic.
    # Fail fast here rather than serving 500s on every request.
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        logger.info("Database connectivity OK.")
    except Exception as exc:
        logger.critical("Cannot connect to database at startup: %s", exc)
        raise  # abort startup — Azure will restart the instance

    start_scheduler()
    # The pg_try_advisory_lock inside each job ensures only ONE of the two
    # Azure instances actually executes the job per interval.
    yield

    stop_scheduler()
    logger.info("AVOCarbon API stopped.")


app = FastAPI(title="AVOCarbon Complaints / 8D Report API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    # Restrict to the methods the API actually uses — reduces attack surface.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


# ── Health endpoints ──────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """
    Liveness probe — returns 200 as long as the process is running.
    Azure App Service / load balancer uses this to route traffic.
    """
    return {"status": "ok"}


@app.get("/health/ready", tags=["ops"])
async def readiness() -> dict:
    """
    Readiness probe — checks DB connectivity and scheduler health.
    Returns 503 if the instance is not ready to serve requests.
    """
    from fastapi import Response
    import fastapi

    checks: dict[str, str] = {}

    # DB check
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    # Scheduler check
    checks["scheduler"] = "ok" if is_scheduler_running() else "stopped"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    status_code = 200 if overall == "ok" else 503

    return fastapi.responses.JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=status_code,
    )