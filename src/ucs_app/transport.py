"""Deterministic application helpers for public transport metadata."""

from datetime import datetime, timezone


def utc_timestamp() -> str:
    """Return an RFC 3339 UTC timestamp for public transport metadata."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
