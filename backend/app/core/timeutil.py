# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time without tzinfo, the form NCM stores in the database.

    Replaces datetime.utcnow(), which is deprecated since Python 3.12.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
