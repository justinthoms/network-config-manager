# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    app_name: str = "Network Config Manager"

    secret_key: str = "CHANGE_ME"
    device_encryption_key: str = ""

    database_url: str = "sqlite:///./ncm.db"
    backup_root: str = "./backups"

    access_token_expire_minutes: int = 480

    # Devices backed up at the same time (SSH sessions in parallel), for
    # "Back up all devices" and for scheduled backups. 1-50.
    backup_workers: int = 10

    # ── Security ──
    # Sign a user out after this many minutes without activity in the browser (0 = never).
    session_idle_minutes: int = 30
    # Failed sign-ins before a username (or source address) is locked, and for how long.
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15
    # Interactive API docs at /docs and /redoc. Off by default; turn on only when needed.
    enable_api_docs: bool = False
    # Make files NCM writes (backups, database, keys) readable by the NCM user only.
    restrict_file_permissions: bool = True

    default_admin_username: str = "admin"
    default_admin_password: str = "admin123"

    # ── Google Drive backup (optional) ──
    # Public URL that browsers use to reach NCM. Google redirects back to
    # {PUBLIC_BASE_URL}/api/cloud/gdrive/callback after consent.
    public_base_url: str = "http://localhost:8000"
    # OAuth client from Google Cloud Console (type: Web application).
    # These may also be saved from the Cloud page by a Super Admin.
    google_client_id: str = ""
    google_client_secret: str = ""
    gdrive_root_folder: str = "NCM-Backups"

    # ── Microsoft OneDrive backup (optional) ──
    # App registration in Microsoft Entra ID (Azure portal) with
    # "Allow public client flows" = Yes. Sign-in uses the device-code flow,
    # so no redirect URI or HTTPS is needed. Can also be set from the Cloud page.
    onedrive_client_id: str = ""
    # Directory (tenant) ID, or "common" (work + personal accounts),
    # "organizations" (work/school only) or "consumers" (personal only).
    onedrive_tenant_id: str = ""
    onedrive_root_folder: str = "NCM-Backups"

    # ── AI config review with Google Gemini (optional) ──
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-flash-preview"

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()

# ───────────────────────── signing key ─────────────────────────
# The SECRET_KEY signs login tokens. A known or short value lets anyone forge
# an admin token, so a weak value is never used: NCM generates a random key
# once and keeps it in backend/.secret_key (readable by the NCM user only).
import secrets as _secrets  # noqa: E402

WEAK_SECRETS = {"", "change_me", "changeme", "change_this_to_a_long_random_secret", "secret", "secret_key",
                "please_change", "your_secret_key", "replace_me", "ncm"}
SECRET_FILE = BASE_DIR / ".secret_key"
secret_key_source = "env"


def _secret_is_weak(value: str) -> bool:
    return value.strip().lower() in WEAK_SECRETS or len(value.strip()) < 32


if _secret_is_weak(settings.secret_key):
    try:
        if SECRET_FILE.is_file() and len(SECRET_FILE.read_text().strip()) >= 32:
            settings.secret_key = SECRET_FILE.read_text().strip()
        else:
            settings.secret_key = _secrets.token_urlsafe(48)
            SECRET_FILE.write_text(settings.secret_key + "\n", encoding="utf-8")
            try:
                SECRET_FILE.chmod(0o600)
            except OSError:
                pass
        secret_key_source = "generated"
    except OSError:
        # read-only install: still never use the weak value; tokens end at restart
        settings.secret_key = _secrets.token_urlsafe(48)
        secret_key_source = "temporary"
