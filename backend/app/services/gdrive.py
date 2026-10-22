# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Google Drive backup integration.

Uses the OAuth 2.0 web-server flow with the minimal ``drive.file`` scope:
NCM can only see and manage the files and folders it created itself, never
the rest of the user's Drive.

Layout in Drive::

    <GDRIVE_ROOT_FOLDER>/            e.g. NCM-Backups/
        <hostname>/
            <hostname>_YYYYmmdd_HHMMSS.conf
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from jose import jwt, JWTError

from app.core.config import settings
from app.core.security import ALGORITHM
from app.services import settings_store as store
from app.core.timeutil import utcnow

logger = logging.getLogger("ncm.gdrive")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"

K_CLIENT_ID = "gdrive.client_id"
K_CLIENT_SECRET = "gdrive.client_secret"      # encrypted
K_REFRESH = "gdrive.refresh_token"            # encrypted
K_EMAIL = "gdrive.account_email"
K_NAME = "gdrive.account_name"
K_CONNECTED_AT = "gdrive.connected_at"
K_AUTO = "gdrive.auto_sync"
K_ROOT_NAME = "gdrive.root_folder_name"
K_FOLDERS = "gdrive.folder_cache"             # json {path: id}
K_LAST_SYNC = "gdrive.last_sync_at"
K_LAST_ERROR = "gdrive.last_error"

_token_cache: dict = {"token": None, "exp": 0.0}
_lock = threading.Lock()


class DriveError(RuntimeError):
    pass


# ───────────────────────── configuration ─────────────────────────

def redirect_uri() -> str:
    return settings.public_base_url.rstrip("/") + "/api/cloud/gdrive/callback"


def client_credentials(db):
    cid = settings.google_client_id or store.get(db, K_CLIENT_ID)
    secret = settings.google_client_secret or store.get_secret(db, K_CLIENT_SECRET)
    return cid, secret


def credentials_source(db) -> str:
    if settings.google_client_id and settings.google_client_secret:
        return "env"
    if store.get(db, K_CLIENT_ID):
        return "ui"
    return "none"


def save_client_credentials(db, client_id: str, client_secret: str | None):
    store.set(db, K_CLIENT_ID, client_id.strip() or None, commit=False)
    if client_secret:
        store.set_secret(db, K_CLIENT_SECRET, client_secret.strip(), commit=False)
    db.commit()


def root_folder_name(db) -> str:
    return store.get(db, K_ROOT_NAME) or settings.gdrive_root_folder or "NCM-Backups"


def is_connected(db) -> bool:
    return bool(store.get(db, K_REFRESH))


def status(db) -> dict:
    cid, secret = client_credentials(db)
    return {
        "configured": bool(cid and secret),
        "credentials_source": credentials_source(db),
        "client_id_hint": (cid[:12] + "…" + cid[-24:]) if cid and len(cid) > 40 else cid,
        "connected": is_connected(db),
        "account_email": store.get(db, K_EMAIL),
        "account_name": store.get(db, K_NAME),
        "connected_at": store.get(db, K_CONNECTED_AT),
        "auto_sync": store.get(db, K_AUTO, "0") == "1",
        "root_folder": root_folder_name(db),
        "last_sync_at": store.get(db, K_LAST_SYNC),
        "last_error": store.get(db, K_LAST_ERROR),
        "redirect_uri": redirect_uri(),
        "scope": SCOPE,
    }


# ───────────────────────── OAuth flow ─────────────────────────

def build_auth_url(db, user_id: int) -> str:
    cid, secret = client_credentials(db)
    if not (cid and secret):
        raise DriveError("Google OAuth client ID and secret are not configured")
    state = jwt.encode(
        {"sub": str(user_id), "purpose": "gdrive-oauth",
         "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        settings.secret_key, algorithm=ALGORITHM,
    )
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def verify_state(state: str) -> int:
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise DriveError("Sign-in link expired or invalid. Start the connection again.") from exc
    if payload.get("purpose") != "gdrive-oauth":
        raise DriveError("Invalid OAuth state")
    return int(payload["sub"])


def exchange_code(db, code: str) -> dict:
    cid, secret = client_credentials(db)
    resp = httpx.post(TOKEN_URL, data={
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
    }, timeout=30)
    data = resp.json()
    if resp.status_code != 200:
        raise DriveError(data.get("error_description") or data.get("error") or "Token exchange failed")
    refresh = data.get("refresh_token")
    if not refresh:
        raise DriveError("Google did not return a refresh token. Remove NCM from your Google "
                         "account's third-party access and connect again.")
    store.set_secret(db, K_REFRESH, refresh, commit=False)
    store.set(db, K_CONNECTED_AT, utcnow().isoformat(), commit=False)
    store.set(db, K_FOLDERS, None, commit=False)
    store.set(db, K_LAST_ERROR, None, commit=False)
    db.commit()
    _token_cache.update(token=data.get("access_token"), exp=time.time() + int(data.get("expires_in", 3600)) - 60)

    try:
        about = _request(db, "GET", f"{API}/about", params={"fields": "user(emailAddress,displayName)"}).json()
        store.set(db, K_EMAIL, about.get("user", {}).get("emailAddress"), commit=False)
        store.set(db, K_NAME, about.get("user", {}).get("displayName"), commit=False)
        db.commit()
    except Exception:  # account info is cosmetic only
        logger.exception("Could not read Google account info")
    return status(db)


def disconnect(db):
    refresh = store.get_secret(db, K_REFRESH)
    if refresh:
        try:
            httpx.post(REVOKE_URL, data={"token": refresh}, timeout=15)
        except Exception:
            pass
    for key in (K_REFRESH, K_EMAIL, K_NAME, K_CONNECTED_AT, K_FOLDERS, K_LAST_ERROR):
        store.set(db, key, None, commit=False)
    db.commit()
    _token_cache.update(token=None, exp=0)


def _access_token(db) -> str:
    with _lock:
        if _token_cache["token"] and time.time() < _token_cache["exp"]:
            return _token_cache["token"]
        refresh = store.get_secret(db, K_REFRESH)
        if not refresh:
            raise DriveError("Google Drive is not connected")
        cid, secret = client_credentials(db)
        resp = httpx.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": secret,
            "refresh_token": refresh, "grant_type": "refresh_token",
        }, timeout=30)
        data = resp.json()
        if resp.status_code != 200:
            msg = data.get("error_description") or data.get("error") or "Token refresh failed"
            if data.get("error") == "invalid_grant":
                msg = "Google access was revoked or expired. Reconnect Google Drive."
            raise DriveError(msg)
        _token_cache.update(token=data["access_token"], exp=time.time() + int(data.get("expires_in", 3600)) - 60)
        return _token_cache["token"]


def _request(db, method, url, **kw) -> httpx.Response:
    headers = kw.pop("headers", {})
    headers["Authorization"] = f"Bearer {_access_token(db)}"
    resp = httpx.request(method, url, headers=headers, timeout=kw.pop("timeout", 60), **kw)
    if resp.status_code == 401:
        _token_cache.update(token=None, exp=0)
        headers["Authorization"] = f"Bearer {_access_token(db)}"
        resp = httpx.request(method, url, headers=headers, timeout=60, **kw)
    return resp


def _raise_for(resp: httpx.Response, what: str):
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message")
        except Exception:
            msg = resp.text[:200]
        raise DriveError(f"{what} failed ({resp.status_code}): {msg}")


# ───────────────────────── folders & files ─────────────────────────

def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_or_create_folder(db, name: str, parent: str | None) -> str:
    q = f"mimeType='{FOLDER_MIME}' and name='{_q(name)}' and trashed=false"
    q += f" and '{parent}' in parents" if parent else " and 'root' in parents"
    resp = _request(db, "GET", f"{API}/files", params={"q": q, "fields": "files(id,name)", "pageSize": 5})
    _raise_for(resp, "Folder lookup")
    files = resp.json().get("files", [])
    if files:
        return files[0]["id"]
    body = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        body["parents"] = [parent]
    resp = _request(db, "POST", f"{API}/files", params={"fields": "id"}, json=body)
    _raise_for(resp, "Folder create")
    return resp.json()["id"]


def _folder_for(db, hostname: str) -> str:
    cache = store.get_json(db, K_FOLDERS, {}) or {}
    root_name = root_folder_name(db)
    root_key = f"/{root_name}"
    if root_key not in cache:
        cache[root_key] = _find_or_create_folder(db, root_name, None)
    key = f"{root_key}/{hostname}"
    if key not in cache:
        cache[key] = _find_or_create_folder(db, hostname, cache[root_key])
    store.set_json(db, K_FOLDERS, cache)
    return cache[key]


def upload_file(db, path: Path, hostname: str, drive_name: str | None = None) -> dict:
    """Upload one local file into <root>/<hostname>/. Retries once if a cached folder vanished."""
    content = path.read_bytes()
    name = drive_name or path.name
    for attempt in range(2):
        folder = _folder_for(db, hostname)
        boundary = "ncm-boundary-7d1f"
        meta = json.dumps({"name": name, "parents": [folder], "description": f"NCM backup of {hostname}"})
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
            f"--{boundary}\r\nContent-Type: text/plain\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--".encode()
        resp = _request(db, "POST", f"{UPLOAD_API}/files",
                        params={"uploadType": "multipart", "fields": "id,name,webViewLink,size"},
                        headers={"Content-Type": f"multipart/related; boundary={boundary}"},
                        content=body, timeout=120)
        if resp.status_code == 404 and attempt == 0:
            store.set(db, K_FOLDERS, None)   # folder deleted in Drive → rebuild
            continue
        _raise_for(resp, "Upload")
        return resp.json()
    raise DriveError("Upload failed")


def upload_backup(db, backup, hostname: str) -> dict:
    if not backup.file_path:
        raise DriveError("Backup has no file")
    path = Path(backup.file_path)
    if not path.is_file():
        raise DriveError("Backup file is missing on disk")
    stamp = (backup.created_at or utcnow()).strftime("%Y%m%d_%H%M%S")
    result = upload_file(db, path, hostname, f"{hostname}_{stamp}.conf")
    backup.gdrive_file_id = result["id"]
    backup.gdrive_synced_at = utcnow()
    store.set(db, K_LAST_SYNC, utcnow().isoformat(), commit=False)
    store.set(db, K_LAST_ERROR, None, commit=False)
    db.commit()
    return result


def list_files(db, limit: int = 100) -> list[dict]:
    cache = store.get_json(db, K_FOLDERS, {}) or {}
    names = {v: k.rsplit("/", 1)[-1] for k, v in cache.items()}
    resp = _request(db, "GET", f"{API}/files", params={
        "q": f"mimeType!='{FOLDER_MIME}' and trashed=false",
        "orderBy": "createdTime desc",
        "pageSize": max(1, min(limit, 500)),
        "fields": "files(id,name,size,createdTime,webViewLink,parents)",
    })
    _raise_for(resp, "List files")
    out = []
    for f in resp.json().get("files", []):
        parent = (f.get("parents") or [None])[0]
        out.append({
            "id": f["id"], "name": f["name"], "size": int(f.get("size") or 0),
            "created_at": f.get("createdTime"), "link": f.get("webViewLink"),
            "folder": names.get(parent, "—"),
        })
    return out


def download(db, file_id: str) -> tuple[bytes, str]:
    meta = _request(db, "GET", f"{API}/files/{file_id}", params={"fields": "name"})
    _raise_for(meta, "File lookup")
    resp = _request(db, "GET", f"{API}/files/{file_id}", params={"alt": "media"}, timeout=120)
    _raise_for(resp, "Download")
    return resp.content, meta.json().get("name", f"{file_id}.conf")


def record_error(db, message: str):
    store.set(db, K_LAST_ERROR, f"{utcnow().isoformat()} {message}")


# ───────────────────────── auto-sync hook ─────────────────────────

from app.services.workqueue import SerialWorker  # noqa: E402

_uploads = SerialWorker("gdrive-sync")     # one upload at a time, in backup order

def auto_upload_async(backup_id: int):
    """Called after every successful backup. Queued for one worker thread so SSH backups never wait on Drive."""
    def _job():
        from app.core.database import SessionLocal
        from app.models.backup import Backup
        from app.models.device import Device
        db = SessionLocal()
        try:
            if not is_connected(db) or store.get(db, K_AUTO, "0") != "1":
                return
            backup = db.get(Backup, backup_id)
            if not backup or backup.status != "SUCCESS":
                return
            device = db.get(Device, backup.device_id)
            upload_backup(db, backup, device.hostname if device else f"device-{backup.device_id}")
        except Exception as exc:
            logger.warning("Google Drive auto-sync failed for backup %s: %s", backup_id, exc)
            try:
                record_error(db, str(exc))
            except Exception:
                pass
        finally:
            db.close()

    _uploads.submit(_job)
