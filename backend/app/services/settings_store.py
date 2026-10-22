# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Tiny helpers around the AppSetting key/value table."""
import json
from app.models.setting import AppSetting
from app.core.crypto import encrypt_secret, decrypt_secret


def get(db, key, default=None):
    row = db.get(AppSetting, key)
    return row.value if row and row.value is not None else default


def set(db, key, value, commit=True):  # noqa: A001 - intentional name
    row = db.get(AppSetting, key)
    if value is None:
        if row:
            db.delete(row)
    else:
        if not row:
            row = AppSetting(key=key)
            db.add(row)
        row.value = str(value)
    if commit:
        db.commit()


def get_secret(db, key):
    raw = get(db, key)
    if not raw:
        return None
    try:
        return decrypt_secret(raw)
    except Exception:
        return None


def set_secret(db, key, value, commit=True):
    set(db, key, encrypt_secret(value) if value else None, commit=commit)


def get_json(db, key, default=None):
    raw = get(db, key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def set_json(db, key, value, commit=True):
    set(db, key, json.dumps(value), commit=commit)
