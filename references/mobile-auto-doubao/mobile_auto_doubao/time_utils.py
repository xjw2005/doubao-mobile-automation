from datetime import datetime, timezone


def now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stamp() -> str:
    """Return a filesystem-friendly timestamp string."""
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")
