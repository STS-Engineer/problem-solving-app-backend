from collections.abc import Generator, AsyncGenerator
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal, AsyncSessionLocal


def get_db() -> Generator[Session, None, None]:
    """Sync dependency — used by all regular routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Async dependency — used by routes that need AsyncSession (logger, etc.)."""
    async with AsyncSessionLocal() as db:
        yield db