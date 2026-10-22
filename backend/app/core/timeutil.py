from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time without tzinfo, the form NCM stores in the database.

    Replaces datetime.utcnow(), which is deprecated since Python 3.12.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
