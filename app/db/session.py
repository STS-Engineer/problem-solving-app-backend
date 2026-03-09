"""
app/db/session.py

Provides both:
  • get_db()          — sync generator for regular FastAPI routes
  • AsyncSessionLocal — async session factory for the scheduler jobs
  • async_engine      — exported so main.py can dispose the pool on shutdown
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# ── Sync engine (used by all standard routes) ─────────────────────────────────
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Standard FastAPI sync dependency."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Async engine (used by scheduler jobs) ────────────────────────────────────

def _make_async_url(sync_url: str) -> str:
    """
    Swap the sync driver prefix for its async equivalent.
    Raises ValueError on unrecognised schemes so misconfiguration is caught
    at startup rather than producing a cryptic driver error at runtime.
    """
    replacements = {
        "postgresql://": "postgresql+asyncpg://",
        "postgres://":   "postgresql+asyncpg://",  # Heroku-style
        "sqlite:///":    "sqlite+aiosqlite:///",
        "mysql://":      "mysql+aiomysql://",
    }
    for old, new in replacements.items():
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)

    # Already has a known async prefix — pass through unchanged.
    known_async = ("postgresql+asyncpg://", "sqlite+aiosqlite://", "mysql+aiomysql://")
    if any(sync_url.startswith(p) for p in known_async):
        return sync_url

    raise ValueError(
        f"No async driver mapping for DATABASE_URL: {sync_url!r}. "
        "Add an entry to _make_async_url() or use an explicit async URL."
    )


async_engine = create_async_engine(
    _make_async_url(settings.DATABASE_URL),
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # required — prevents MissingGreenlet on attribute access after commit
)