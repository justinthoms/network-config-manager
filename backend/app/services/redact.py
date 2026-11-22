# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Hide secrets in configuration text shown in the browser.

Only roles that may download backups (backups.download) can see a config with
its secrets. Everyone else gets the text with passwords, keys, communities and
hashes replaced by <MASKED>. A role that may see secrets can still ask for the
masked text, which is handy when sharing a screen.
"""
from app.core.rbac import has_permission

MASK_HEADER = "X-NCM-Masked"


def can_reveal(user) -> bool:
    return has_permission(user.role, "backups.download")


def must_mask(user, masked: bool | None) -> bool:
    """masked=None means 'the default': unmasked for roles that may see secrets."""
    return True if not can_reveal(user) else bool(masked)


def mask(text: str) -> str:
    from app.services.ai_review import mask_secrets
    return mask_secrets(text)


def for_user(text: str, user, masked: bool | None) -> tuple[str, bool]:
    if must_mask(user, masked):
        return mask(text), True
    return text, False
