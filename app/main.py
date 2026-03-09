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


def _configure_logging() -> None:
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(fmt)

    for name in (
        "app",         
        "apscheduler",
    ):
        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)
        log.propagate = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(handler)


_configure_logging()
logger = logging.getLogger(__name__)  # ← must come AFTER _configure_logging()

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting AVOCarbon API...")

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        logger.info("Database connectivity OK.")
    except Exception as exc:
        logger.critical("Cannot connect to database at startup: %s", exc)
        raise

    start_scheduler()
    yield

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
    Readiness probe — checks DB and scheduler.
    Returns 503 if not ready to serve requests.
    """
    checks: dict[str, str] = {}

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    checks["scheduler"] = "ok" if is_scheduler_running() else "stopped"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=200 if overall == "ok" else 503,
    )