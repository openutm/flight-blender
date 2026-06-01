"""
Shared datetime parsing utilities.
"""

from datetime import datetime, timezone


def parse_iso_utc(value: str | int | float | object | None, fallback: datetime | None = None) -> datetime | None:
    """Parse an ISO-8601 string or Unix timestamp into a timezone-aware UTC datetime.

    Returns *fallback* (default ``None``) when *value* is ``None`` or unparseable.
    """
    if value is None:
        return fallback
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return fallback


def ensure_utc(dt: datetime) -> datetime:
    """Return *dt* as a timezone-aware UTC datetime (naive → assumed UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
