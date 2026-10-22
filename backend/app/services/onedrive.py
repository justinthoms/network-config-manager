# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Microsoft OneDrive backup integration (Microsoft Graph).

Sign-in uses the OAuth 2.0 device-code flow with delegated permissions, the
same flow as a ``msal.PublicClientApplication.initiate_device_flow`` script:
NCM shows a short code, an admin opens https://microsoft.com/devicelogin and
signs in. No redirect URI and no HTTPS are needed, so it works when NCM is
opened by IP address.

The refresh token is stored encrypted, so uploads keep working after a
restart. Microsoft rotates refresh tokens, so every new one is saved.

Layout in OneDrive (``layout`` setting)::

    hostname:  <root>/<hostname>/<hostname>_YYYYmmdd_HHMMSS.conf
    date:      <root>/YYYY/MM/DD/<hostname>_YYYYmmdd_HHMMSS.conf

Graph creates missing folders automatically on a path-based upload.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.services import settings_store as store
from app.core.timeutil import utcnow

logger = logging.getLogger("ncm.onedrive")

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
SCOPES = "Files.ReadWrite offline_access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024   # Graph simple PUT limit; larger files use an upload session
CHUNK = 5 * 320 * 1024                  # upload-session chunks must be multiples of 320 KiB

K_CLIENT_ID = "onedrive.client_id"
K_TENANT = "onedrive.tenant_id"
K_REFRESH = "onedrive.refresh_token"       # encrypted
K_NAME = "onedrive.account_name"
K_EMAIL = "onedrive.account_email"
K_DRIVE_TYPE = "onedrive.drive_type"
K_CONNECTED_AT = "onedrive.connected_at"
K_AUTO = "onedrive.auto_sync"
K_ROOT_NAME = "onedrive.root_folder_name"
K_LAYOUT = "onedrive.layout"               # "hostname" | "date"
K_ROOT_URL = "onedrive.root_folder_url"
K_LAST_SYNC = "onedrive.last_sync_at"
K_LAST_ERROR = "onedrive.last_error"

LAYOUTS = ("hostname", "date")

_token_cache: dict = {"token": None, "exp": 0.0}
_lock = threading.Lock()
# Pending device-code sign-ins, keyed by NCM user id. The device_code never leaves the server.
_pending: dict[int, dict] = {}


class OneDriveError(RuntimeError):
    pass


# ───────────────────────── configuration ─────────────────────────

def client_config(db) -> tuple[str | None, str]:
    cid = settings.onedrive_client_id or store.get(db, K_CLIENT_ID)
    tenant = settings.onedrive_tenant_id or store.get(db, K_TENANT) or "common"
    return cid, tenant


def credentials_source(db) -> str:
    if settings.onedrive_client_id:
        return "env"
    if store.get(db, K_CLIENT_ID):
        return "ui"
    return "none"


def save_client_config(db, client_id: str, tenant_id: str | None):
    store.set(db, K_CLIENT_ID, client_id.strip() or None, commit=False)
    store.set(db, K_TENANT, (tenant_id or "").strip() or None, commit=False)
    db.commit()


def root_folder_name(db) -> str:
    return store.get(db, K_ROOT_NAME) or settings.onedrive_root_folder or "NCM-Backups"


def layout(db) -> str:
    value = store.get(db, K_LAYOUT, "hostname")
    return value if value in LAYOUTS else "hostname"


def is_connected(db) -> bool:
    return bool(store.get(db, K_REFRESH))


def status(db) -> dict:
    cid, tenant = client_config(db)
    return {
        "configured": bool(cid),
        "credentials_source": credentials_source(db),
        "client_id": cid,
        "tenant_id": tenant,
        "connected": is_connected(db),
        "account_name": store.get(db, K_NAME),
        "account_email": store.get(db, K_EMAIL),
        "drive_type": store.get(db, K_DRIVE_TYPE),
        "connected_at": store.get(db, K_CONNECTED_AT),
        "auto_sync": store.get(db, K_AUTO, "0") == "1",
        "root_folder": root_folder_name(db),
        "root_folder_url": store.get(db, K_ROOT_URL),
        "layout": layout(db),
        "last_sync_at": store.get(db, K_LAST_SYNC),
        "last_error": store.get(db, K_LAST_ERROR),
        "scope": SCOPES,
    }


def _token_url(tenant: str) -> str:
    return f"{LOGIN}/{tenant}/oauth2/v2.0/token"


def _err(data: dict, fallback: str) -> str:
    desc = data.get("error_description") or ""
    # Microsoft prefixes descriptions with an AADSTS code and appends trace ids; keep the first line.
    desc = desc.split("\r\n")[0].split("\n")[0].strip()
    return desc or data.get("error") or fallback


# ───────────────────────── device-code sign-in ─────────────────────────

def start_device_flow(db, user_id: int) -> dict:
    cid, tenant = client_config(db)
    if not cid:
        raise OneDriveError("OneDrive application (client) ID is not configured")
    resp = httpx.post(f"{LOGIN}/{tenant}/oauth2/v2.0/devicecode",
                      data={"client_id": cid, "scope": SCOPES}, timeout=30)
    data = resp.json()
    if resp.status_code != 200 or "device_code" not in data:
        raise OneDriveError(_err(data, "Could not start Microsoft sign-in"))
    expires_in = int(data.get("expires_in", 900))
    _pending[user_id] = {
        "device_code": data["device_code"],
        "interval": int(data.get("interval", 5)),
        "expires": time.time() + expires_in,
        "client_id": cid,
        "tenant": tenant,
    }
    return {
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri", "https://microsoft.com/devicelogin"),
        "message": data.get("message"),
        "interval": int(data.get("interval", 5)),
        "expires_in": expires_in,
    }


def poll_device_flow(db, user_id: int) -> dict:
    """One token request. Returns {"state": "pending" | "connected" | "error", ...}."""
    flow = _pending.get(user_id)
    if not flow:
        return {"state": "error", "message": "No sign-in in progress. Click Connect OneDrive again."}
    if time.time() > flow["expires"]:
        _pending.pop(user_id, None)
        return {"state": "error", "message": "The sign-in code expired. Click Connect OneDrive again."}

    resp = httpx.post(_token_url(flow["tenant"]), data={
        "grant_type": DEVICE_GRANT, "client_id": flow["client_id"], "device_code": flow["device_code"],
    }, timeout=30)
    data = resp.json()
    if resp.status_code == 200 and data.get("access_token"):
        try:
            _store_tokens(db, data)
        except Exception as exc:
            db.rollback()
            _pending.pop(user_id, None)
            return {"state": "error", "message": f"Signed in to Microsoft, but NCM could not save the token: {exc}"}
        _pending.pop(user_id, None)
        store.set(db, K_CONNECTED_AT, utcnow().isoformat(), commit=False)
        store.set(db, K_LAST_ERROR, None, commit=False)
        store.set(db, K_ROOT_URL, None, commit=False)
        db.commit()
        _load_account_info(db)
        return {"state": "connected", "status": status(db)}

    code = data.get("error")
    if code == "authorization_pending":
        return {"state": "pending", "interval": flow["interval"]}
    if code == "slow_down":
        flow["interval"] += 5
        return {"state": "pending", "interval": flow["interval"]}
    _pending.pop(user_id, None)
    if code == "authorization_declined":
        return {"state": "error", "message": "Sign-in was cancelled in the Microsoft window."}
    if code == "expired_token":
        return {"state": "error", "message": "The sign-in code expired. Click Connect OneDrive again."}
    return {"state": "error", "message": _err(data, "Microsoft sign-in failed")}


def cancel_device_flow(user_id: int):
    _pending.pop(user_id, None)


def _store_tokens(db, data: dict):
    if not data.get("refresh_token"):
        raise OneDriveError("Microsoft did not return a refresh token (offline_access was not granted).")
    store.set_secret(db, K_REFRESH, data["refresh_token"], commit=False)
    _token_cache.update(token=data["access_token"],
                        exp=time.time() + int(data.get("expires_in", 3600)) - 120)


def _load_account_info(db):
    try:
        drive = _request(db, "GET", f"{GRAPH}/me/drive",
                         params={"$select": "driveType,owner"}).json()
        user = (drive.get("owner") or {}).get("user") or {}
        store.set(db, K_NAME, user.get("displayName"), commit=False)
        store.set(db, K_EMAIL, user.get("email"), commit=False)
        store.set(db, K_DRIVE_TYPE, drive.get("driveType"), commit=False)
        db.commit()
    except Exception:  # account info is cosmetic only
        logger.exception("Could not read OneDrive account info")


def disconnect(db):
    # Microsoft has no simple refresh-token revoke endpoint for public clients;
    # access can be removed at https://myapps.microsoft.com or https://account.live.com/consent/Manage.
    for key in (K_REFRESH, K_NAME, K_EMAIL, K_DRIVE_TYPE, K_CONNECTED_AT, K_ROOT_URL, K_LAST_ERROR):
        store.set(db, key, None, commit=False)
    db.commit()
    _token_cache.update(token=None, exp=0)


# ───────────────────────── tokens & requests ─────────────────────────

def _access_token(db) -> str:
    with _lock:
        if _token_cache["token"] and time.time() < _token_cache["exp"]:
            return _token_cache["token"]
        refresh = store.get_secret(db, K_REFRESH)
        if not refresh:
            raise OneDriveError("OneDrive is not connected")
        cid, tenant = client_config(db)
        resp = httpx.post(_token_url(tenant), data={
            "grant_type": "refresh_token", "client_id": cid,
            "refresh_token": refresh, "scope": SCOPES,
        }, timeout=30)
        data = resp.json()
        if resp.status_code != 200:
            msg = _err(data, "Token refresh failed")
            if data.get("error") in ("invalid_grant", "interaction_required"):
                msg = "Microsoft sign-in expired or access was removed. Reconnect OneDrive."
            raise OneDriveError(msg)
        store.set_secret(db, K_REFRESH, data.get("refresh_token") or refresh)  # rotated token
        _token_cache.update(token=data["access_token"],
                            exp=time.time() + int(data.get("expires_in", 3600)) - 120)
        return _token_cache["token"]


def _request(db, method, url, **kw) -> httpx.Response:
    headers = kw.pop("headers", {})
    timeout = kw.pop("timeout", 60)
    headers["Authorization"] = f"Bearer {_access_token(db)}"
    resp = httpx.request(method, url, headers=headers, timeout=timeout, **kw)
    if resp.status_code == 401:
        _token_cache.update(token=None, exp=0)
        headers["Authorization"] = f"Bearer {_access_token(db)}"
        resp = httpx.request(method, url, headers=headers, timeout=timeout, **kw)
    return resp


def _raise_for(resp: httpx.Response, what: str):
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message")
        except Exception:
            msg = resp.text[:200]
        raise OneDriveError(f"{what} failed ({resp.status_code}): {msg}")


# ───────────────────────── files ─────────────────────────

def _safe(name: str) -> str:
    """OneDrive forbids these characters in names."""
    for ch in '"*:<>?/\\|':
        name = name.replace(ch, "-")
    return name.strip().rstrip(".") or "unnamed"


def item_path(db, hostname: str, when: datetime, file_name: str) -> str:
    root = _safe(root_folder_name(db))
    if layout(db) == "date":
        return f"{root}/{when:%Y/%m/%d}/{_safe(file_name)}"
    return f"{root}/{_safe(hostname)}/{_safe(file_name)}"


def _upload_large(db, encoded: str, content: bytes) -> dict:
    resp = _request(db, "POST", f"{GRAPH}/me/drive/root:/{encoded}:/createUploadSession",
                    json={"item": {"@microsoft.graph.conflictBehavior": "replace"}})
    _raise_for(resp, "Upload session")
    upload_url = resp.json()["uploadUrl"]
    total = len(content)
    for start in range(0, total, CHUNK):
        part = content[start:start + CHUNK]
        end = start + len(part) - 1
        # The upload URL is pre-authorised; it must NOT get an Authorization header.
        r = httpx.put(upload_url, content=part, timeout=120, headers={
            "Content-Length": str(len(part)), "Content-Range": f"bytes {start}-{end}/{total}"})
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code != 202:
            _raise_for(r, "Upload")
    raise OneDriveError("Upload did not complete")


def upload_bytes(db, content: bytes, path_in_drive: str) -> dict:
    encoded = quote(path_in_drive, safe="/")
    if len(content) > SIMPLE_UPLOAD_LIMIT:
        return _upload_large(db, encoded, content)
    resp = _request(db, "PUT", f"{GRAPH}/me/drive/root:/{encoded}:/content",
                    params={"@microsoft.graph.conflictBehavior": "replace"},
                    headers={"Content-Type": "text/plain"}, content=content, timeout=120)
    _raise_for(resp, "Upload")
    return resp.json()


def _remember_root_url(db):
    if store.get(db, K_ROOT_URL):
        return
    try:
        resp = _request(db, "GET", f"{GRAPH}/me/drive/root:/{quote(_safe(root_folder_name(db)))}",
                        params={"$select": "webUrl"})
        if resp.status_code == 200:
            store.set(db, K_ROOT_URL, resp.json().get("webUrl"), commit=False)
    except Exception:
        pass


def upload_backup(db, backup, hostname: str) -> dict:
    if not backup.file_path:
        raise OneDriveError("Backup has no file")
    path = Path(backup.file_path)
    if not path.is_file():
        raise OneDriveError("Backup file is missing on disk")
    when = backup.created_at or utcnow()
    name = f"{hostname}_{when:%Y%m%d_%H%M%S}.conf"
    result = upload_bytes(db, path.read_bytes(), item_path(db, hostname, when, name))
    backup.onedrive_item_id = result["id"]
    backup.onedrive_synced_at = utcnow()
    store.set(db, K_LAST_SYNC, utcnow().isoformat(), commit=False)
    store.set(db, K_LAST_ERROR, None, commit=False)
    _remember_root_url(db)
    db.commit()
    return {"id": result["id"], "name": result.get("name"), "link": result.get("webUrl"),
            "size": result.get("size")}


def test_upload(db) -> dict:
    """Write a small text file to <root>/_ncm-test.txt, like the standalone test script."""
    now = datetime.now()
    text = f"NCM OneDrive upload test successful.\nDate: {now.isoformat()}\n"
    result = upload_bytes(db, text.encode(), f"{_safe(root_folder_name(db))}/_ncm-test.txt")
    store.set(db, K_LAST_ERROR, None, commit=False)
    _remember_root_url(db)
    db.commit()
    return {"name": result.get("name"), "link": result.get("webUrl")}


def item_info(db, item_id: str) -> dict:
    resp = _request(db, "GET", f"{GRAPH}/me/drive/items/{quote(item_id, safe='')}",
                    params={"$select": "id,name,size,webUrl,parentReference,createdDateTime"})
    _raise_for(resp, "File lookup")
    return resp.json()


def download(db, item_id: str) -> tuple[bytes, str]:
    info = item_info(db, item_id)
    resp = _request(db, "GET", f"{GRAPH}/me/drive/items/{quote(item_id, safe='')}/content",
                    timeout=120, follow_redirects=True)
    _raise_for(resp, "Download")
    return resp.content, info.get("name", f"{item_id}.conf")


def record_error(db, message: str):
    store.set(db, K_LAST_ERROR, f"{utcnow().isoformat()} {message}")


# ───────────────────────── auto-sync hook ─────────────────────────

from app.services.workqueue import SerialWorker  # noqa: E402

_uploads = SerialWorker("onedrive-sync")     # one upload at a time, in backup order

def auto_upload_async(backup_id: int):
    """Called after every successful backup. Queued for one worker thread so SSH backups never wait on OneDrive."""
    def _job():
        from app.core.database import SessionLocal
        from app.models.backup import Backup
        from app.models.device import Device
        db = SessionLocal()
        try:
            if not is_connected(db) or store.get(db, K_AUTO, "0") != "1":
                return
            backup = db.get(Backup, backup_id)
            if not backup or backup.status != "SUCCESS" or backup.onedrive_item_id:
                return
            device = db.get(Device, backup.device_id)
            upload_backup(db, backup, device.hostname if device else f"device-{backup.device_id}")
        except Exception as exc:
            logger.warning("OneDrive auto-sync failed for backup %s: %s", backup_id, exc)
            try:
                record_error(db, str(exc))
            except Exception:
                pass
        finally:
            db.close()

    _uploads.submit(_job)
