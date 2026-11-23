# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Anonymous usage reporting (telemetry).

When turned on, NCM sends a small heartbeat to the address in
TELEMETRY_URL every TELEMETRY_INTERVAL_HOURS. The heartbeat carries ONLY:

  - the project name ("network-config-manager")
  - the running version
  - the server hostname
  - a random instance id, generated once and kept in the database, so the
    collector can count installs without counting one twice

It never sends IP addresses, device details, backup data, user data or any
credential. This is deliberately the whole payload — see collect() below.

It is transparent, not hidden: the Security page shows whether it is on and
what it sends, and an Admin can turn it off there. It can also be turned off
with TELEMETRY_ENABLED=false in backend/.env. A stored choice on the Security
page overrides the .env default.
"""
import json
import logging
import socket
import ssl
import threading
import time
import urllib.request
import uuid

from app.core.config import settings
from app.version import __version__

logger = logging.getLogger("ncm.telemetry")

PROJECT = "network-config-manager"
TIMEOUT = 15
K_ENABLED = "telemetry.enabled"        # "1"/"0": overrides TELEMETRY_ENABLED when set
K_INSTANCE = "telemetry.instance_id"
K_SECRET = "telemetry.instance_secret"
K_LAST = "telemetry.last_sent_at"      # ISO timestamp of the last successful send


def is_enabled(db) -> bool:
    from app.services import settings_store as store
    v = store.get(db, K_ENABLED)
    if v is not None:
        return v == "1"
    return bool(settings.telemetry_enabled)


def set_enabled(db, on: bool):
    from app.services import settings_store as store
    store.set(db, K_ENABLED, "1" if on else "0")


def collect(db) -> dict:
    """The ENTIRE payload. Only version and hostname identify this install."""
    from app.services import settings_store as store
    return {
        "project": PROJECT,
        "version": __version__,
        "hostname": socket.getfqdn() or socket.gethostname(),
        "instance_id": _instance_id(db, store),
    }


def what_is_sent(db) -> dict:
    """For the Security page: exactly what would be sent, so nothing is hidden."""
    payload = collect(db)
    from app.services import settings_store as store
    return {
        "enabled": is_enabled(db),
        "url": settings.telemetry_url,
        "interval_hours": max(1, settings.telemetry_interval_hours),
        "last_sent_at": store.get(db, K_LAST),
        "sends": payload,          # the actual payload, nothing more
    }


def _instance_id(db, store) -> str:
    iid = store.get(db, K_INSTANCE)
    if not iid:
        iid = uuid.uuid4().hex
        store.set(db, K_INSTANCE, iid)
    return iid


def _post(path: str, payload: dict, headers: dict):
    url = settings.telemetry_url.rstrip("/") + path
    if not url.startswith("https://"):
        return None
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "NCM-telemetry", **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ssl.create_default_context()) as r:
            return json.loads(r.read().decode())
    except Exception:          # offline or collector down: just skip this round
        return None


def _register(db, store) -> bool:
    reply = _post("/api/v1/register", collect(db), {"X-Project-Token": settings.telemetry_token})
    if reply and reply.get("instance_id"):
        store.set(db, K_INSTANCE, reply["instance_id"], commit=False)
        store.set(db, K_SECRET, reply.get("instance_secret", ""), commit=False)
        db.commit()
        return True
    return False


def ping_once() -> bool:
    """One heartbeat (or first-time registration). Never raises."""
    from app.core.database import SessionLocal
    from app.core.timeutil import utcnow
    from app.services import settings_store as store
    db = SessionLocal()
    try:
        if not is_enabled(db):
            return False
        if not store.get(db, K_SECRET) or not store.get(db, K_INSTANCE):
            if not _register(db, store):
                return False
        payload = collect(db)
        reply = _post("/api/v1/heartbeat", payload, {"X-Instance-Secret": store.get(db, K_SECRET, "")})
        if reply is None:
            return False
        if reply.get("ok") is False:          # collector forgot us: register again next round
            store.set(db, K_SECRET, None)
            return False
        store.set(db, K_LAST, utcnow().isoformat())
        return True
    except Exception:
        logger.debug("telemetry ping failed", exc_info=True)
        return False
    finally:
        db.close()


def start_background():
    """One daemon thread per process: register/heartbeat at start, then every interval."""
    def loop():
        time.sleep(45)
        while True:
            try:
                ping_once()
            except Exception:
                logger.debug("telemetry loop error", exc_info=True)
            time.sleep(max(1, settings.telemetry_interval_hours) * 3600)

    threading.Thread(target=loop, name="ncm-telemetry", daemon=True).start()
