# app/utils/datetime_utils.py
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current UTC time as timezone-aware datetime — matches DateTime(timezone=True) columns."""
    return datetime.now(timezone.utc)
