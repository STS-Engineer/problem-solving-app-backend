"""
app/db/session.py

Provides both:
  • get_db()          — sync generator for regular FastAPI routes (your existing code)
  • AsyncSessionLocal — async session factory for the APScheduler escalation job
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.core.config import settings

# ── Sync engine (existing — used by all your current routes) ──────────────────
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """Standard FastAPI sync dependency."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _make_async_url(sync_url: str) -> str:
    """Swap the sync driver prefix for an async one."""
    replacements = {
        "postgresql://":    "postgresql+asyncpg://",
        "postgres://":      "postgresql+asyncpg://",       # Heroku-style
        "sqlite:///":       "sqlite+aiosqlite:///",
        "mysql://":         "mysql+aiomysql://",
    }
    for old, new in replacements.items():
        if sync_url.startswith(old):
            return sync_url.replace(old, new, 1)
    # Already has an async driver prefix — return as-is
    return sync_url


ASYNC_DATABASE_URL = _make_async_url(settings.DATABASE_URL)

async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  
)
