# Network Config Manager (NCM)

[![Version](https://img.shields.io/badge/version-2.0.0-1f4fd8)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)](#quick-start)
[![Vendors](https://img.shields.io/badge/vendors-Huawei%20%7C%20Juniper%20%7C%20Arista-555)](#features)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](#license)
![Gemini](https://img.shields.io/badge/Google%20Gemini-supported-8E75B2?logo=googlegemini&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-supported-412991?logo=openai&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-local-000000?logo=ollama&logoColor=white)

Open-source, self-hosted configuration manager for NOC and network operations teams. NCM backs up Huawei, Juniper and Arista devices over SSH, shows what changed, checks device health, keeps off-site copies in Google Drive and OneDrive, and uses AI to review risky changes.

<img src="docs/screenshots/02-overview.png" alt="NCM overview: device status, backup health and recent config changes">

> All screenshots use fictional demo data.

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Running in production](#running-in-production)
- [Setup guides](#setup-guides)
- [Security](#security)
- [Roles and permissions](#roles-and-permissions)
- [Upgrading](#upgrading)
- [Troubleshooting](#troubleshooting)
- [API](#api)

## Features

- 💾 **Backups over SSH** for Huawei (VRP), Juniper (Junos) and Arista (EOS), on demand or on a schedule, with retries and retention. **Every device is backed up daily** by default, including devices added later.
- 🔄 **Change tracking:** per-device timeline, side-by-side or unified diff, timestamp and header noise hidden, compare across devices, `.diff` download.
- 🩺 **Device health checks:** CPU, memory, fans, power, temperature, alarms, interfaces and logs sorted by syslog level, from read-only `display` / `show` commands. OK / Warning / Critical per device, on demand or on a schedule.
- 🤖 **AI review** with your own provider (Gemini, OpenAI, Anthropic, OpenRouter, Groq, Mistral, or a local Ollama): config audits with a score, risk rating for every change, health summaries, and an assistant that answers questions about your network in English or Malayalam. Secrets are always masked first.
- ☁️ **Off-site copies** in Google Drive and OneDrive, uploaded automatically after each backup.
- 📁 **File browser** for the backup folder: search, preview, download as ZIP, compare any two files.
- 🔐 **Security:** role-based access, encrypted device credentials, strong password policy, sign-in lockout, session revocation, idle sign-out, security headers, and a built-in security self-check.
- 🧾 **Audit log** of every sign-in, change and command.
- 📥 CSV import and export, 🌙 dark mode, installable as a web app.

## Screenshots

### Device health

Read-only commands, parsed into one status per device. Open a device to see what needs attention, every log line by level, interfaces, raw output and an AI summary.

<img src="docs/screenshots/04-health.png" alt="Device health page with OK, Warning, Critical and Failed devices">

<table>
<tr>
<td width="50%"><img src="docs/screenshots/05-health-detail.png" alt="Health check detail: CPU, memory, uptime and issues"><br><sub>Health check of one device</sub></td>
<td width="50%"><img src="docs/screenshots/06-health-logs.png" alt="Logs filtered by syslog level"><br><sub>Logs by level, with search and a time filter</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/07-health-ai.png" alt="AI summary of a health check"><br><sub>AI summary with the commands to run next</sub></td>
<td width="50%"><img src="docs/screenshots/03-devices.png" alt="Device inventory"><br><sub>Device inventory with one-click backup, SSH test and health check</sub></td>
</tr>
</table>

### Config changes and AI review

<table>
<tr>
<td width="50%"><img src="docs/screenshots/09-changes.png" alt="Side-by-side diff of two backups"><br><sub>Change timeline and side-by-side diff</sub></td>
<td width="50%"><img src="docs/screenshots/15-ai-change-review.png" alt="AI security review of a config change rated critical"><br><sub>AI security review of a change, with rollback commands</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/14-ai-review.png" alt="AI review page with providers and results"><br><sub>AI providers, settings and all results</sub></td>
<td width="50%"><img src="docs/screenshots/16-assistant.png" alt="NCM assistant answering in English and Malayalam"><br><sub>Ask NCM, in English or Malayalam</sub></td>
</tr>
</table>

### Backups, files and schedules

<table>
<tr>
<td width="50%"><img src="docs/screenshots/08-backups.png" alt="Backup list with status and cloud copies"><br><sub>Every backup, with Google Drive and OneDrive copies</sub></td>
<td width="50%"><img src="docs/screenshots/11-schedules.png" alt="Automatic backup for all devices"><br><sub>Daily backup for all devices, staggered start</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/10-files.png" alt="File browser for the backup folder"><br><sub>File browser</sub></td>
<td width="50%"><img src="docs/screenshots/12-cloud-google-drive.png" alt="Google Drive backup settings"><br><sub>Google Drive</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/13-cloud-onedrive.png" alt="OneDrive backup settings"><br><sub>OneDrive</sub></td>
<td width="50%"><img src="docs/screenshots/19-security.png" alt="Security self-check page"><br><sub>Security self-check</sub></td>
</tr>
</table>

### Administration

<table>
<tr>
<td width="50%"><img src="docs/screenshots/17-users.png" alt="User management"><br><sub>Users and roles</sub></td>
<td width="50%"><img src="docs/screenshots/18-audit-log.png" alt="Audit log"><br><sub>Audit log</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/20-profile.png" alt="My profile with password, sessions and personal AI keys"><br><sub>My profile: password, sessions, personal AI keys</sub></td>
<td width="50%"><img src="docs/screenshots/01-login.png" alt="Sign-in page"><br><sub>Sign in</sub></td>
</tr>
</table>

<img src="docs/screenshots/21-overview-dark.png" alt="Overview in dark mode">

## Quick start

Requirements: Linux (or macOS / WSL), Python 3.10 or newer, and SSH access from the NCM server to your devices.

```bash
git clone https://github.com/justinthoms/network-config-manager.git
cd network-config-manager
./run-local.sh
```

`run-local.sh` creates a virtual environment, installs the requirements, copies `backend/.env.example` to `backend/.env` and starts NCM on port 8000. Stop it (Ctrl+C), then edit `backend/.env`:

```bash
# token signing key
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# device password encryption key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the first value in `SECRET_KEY` and the second in `DEVICE_ENCRYPTION_KEY`, set `DEFAULT_ADMIN_PASSWORD`, and set `DATABASE_URL` to a **full path** (see below). Start `./run-local.sh` again, open `http://localhost:8000`, and sign in as `admin`. NCM asks you to choose a strong password at the first sign-in.

> **Keep `DEVICE_ENCRYPTION_KEY` safe and never change it.** Device passwords and cloud tokens are encrypted with it; with a different key they cannot be read.

Then add devices on the **Devices** page (or import a CSV), click **Test SSH**, and **Back up**. Every device is backed up daily from 01:00 server time automatically.

## Configuration

All settings live in `backend/.env`. The important ones:

| Setting | Default | What it does |
|---|---|---|
| `SECRET_KEY` | (placeholder) | Signs login tokens. If weak or missing, NCM generates one in `backend/.secret_key`. |
| `DEVICE_ENCRYPTION_KEY` | (required) | Fernet key that encrypts device passwords and cloud tokens. |
| `DATABASE_URL` | `sqlite:///./ncm.db` | Use a full path, e.g. `sqlite:////opt/ncm/backend/ncm.db`. A relative path depends on the folder NCM is started from. PostgreSQL also works: `postgresql+psycopg://user:pass@host/ncm`. |
| `BACKUP_ROOT` | `./backups` | Where configuration files are written. Use a full path. |
| `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` | `admin` | Only used to create the first account. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `480` | Maximum session length. |
| `SESSION_IDLE_MINUTES` | `30` | Browser signs out after this much inactivity (0 = never). |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_MINUTES` | `5` / `15` | Sign-in lockout. |
| `ENABLE_API_DOCS` | `false` | Turns on `/docs` and `/redoc`. |
| `RESTRICT_FILE_PERMISSIONS` | `true` | Keeps `.env`, the database, keys and backups readable by the NCM user only. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Address browsers use to open NCM (Google Drive sign-in). |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | | Optional, can also be set on the Cloud page. |
| `ONEDRIVE_CLIENT_ID` / `ONEDRIVE_TENANT_ID` | | Optional, can also be set on the Cloud page. |

Schedules, health-check commands, AI providers and cloud settings are managed in the web UI.

## Running in production

NCM holds the credentials of every device, so treat the server like a jump host: restrict who can reach it, keep it patched, and back up `backend/.env`, `backend/.secret_key`, the database and `BACKUP_ROOT`.

**1. Run it as a service** (example `/etc/systemd/system/ncm.service`, with NCM in `/opt/ncm` and a dedicated `ncm` user):

```ini
[Unit]
Description=Network Config Manager
After=network-online.target

[Service]
User=ncm
WorkingDirectory=/opt/ncm/backend
ExecStart=/opt/ncm/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now ncm
```

**2. Put HTTPS in front of it.** NCM serves plain HTTP; without HTTPS, passwords cross the network unencrypted. With [Caddy](https://caddyserver.com) (`/etc/caddy/Caddyfile`):

```
ncm.example.local {
    reverse_proxy 127.0.0.1:8000
}
```

For an internal name without a public certificate, add `tls internal` inside the block and trust Caddy's root certificate on the NOC machines. Nginx works the same way with `proxy_pass http://127.0.0.1:8000;` and `proxy_set_header X-Forwarded-Proto $scheme;`.

**3. Check the Security page** (user menu → Security) and fix anything red or orange.

**Several worker processes.** `uvicorn ... --workers N` is supported. Every process serves the web UI and API, but only one runs scheduled backups and health checks (it holds `backend/.scheduler.lock`); if it stops, another takes over within a minute. All workers must run on the same server and share the same `backend` folder.

## Setup guides

### AI providers

Open **AI review → Add provider** (shared for everyone) or **My profile → Add API key** (personal). Supported: Google Gemini, OpenAI, Anthropic, OpenRouter, Groq, Mistral, any OpenAI-compatible endpoint, and Ollama on your own server (nothing leaves your network).

- Passwords, secrets, SNMP communities, keys and hashes are always masked before anything is sent. Turn on **Hide IP addresses** to replace addresses with placeholders too.
- **Automatic change review** reviews every backup whose configuration changed.
- **Live read-only commands** (off by default) let the assistant run one `display` / `show` command at a time. Configuration, reboot, copy, delete and similar commands are refused, and every command is audited.
- Free tiers change often and may use submitted data for training; check your provider's terms and your organisation's policy. AI can be wrong; check suggested commands before running them.

### Device health checks

**Health → Schedule & settings** sets how often all devices are checked, how long logs count as recent, when the AI summary runs (on click, for Warning/Critical, or always) and its language (English or Malayalam). The command list per vendor can be edited; only read-only commands are accepted, and commands a device does not know are skipped and listed in the result.

| Huawei | Juniper | Arista |
|---|---|---|
| `display version`, `cpu-usage`, `memory-usage`, `device`, `fan`, `power`, `temperature all`, `alarm active`, `interface brief`, `logbuffer` | `show version`, `chassis routing-engine`, `chassis alarms`, `system alarms`, `chassis environment`, `interfaces terse`, `log messages \| last 500` | `show version`, `processes top once`, `system environment all`, `interfaces status`, `interfaces counters errors`, `logging 500` |

Junos logs carry no level by default; NCM infers it from the message and marks it `~`. Add `explicit-priority` to the syslog file for exact levels.

### Google Drive

1. In [Google Cloud Console](https://console.cloud.google.com/apis/library/drive.googleapis.com), enable the **Google Drive API**.
2. Under **Google Auth Platform**, configure the consent screen with the scope `https://www.googleapis.com/auth/drive.file` and set it to **In production** (in *Testing*, Google revokes access after 7 days).
3. Create an **OAuth client ID** of type **Web application** with the redirect URI `{PUBLIC_BASE_URL}/api/cloud/gdrive/callback`.
4. Enter the client ID and secret on **Cloud → Google Drive** (Super Admin) or in `.env`, then click **Connect Google Drive**.

Google accepts `http://` redirects only for `localhost`; for access by IP or hostname, serve NCM over HTTPS. NCM only sees files it created.

### OneDrive

1. In the [Microsoft Entra admin center](https://entra.microsoft.com), create an **App registration**. For a personal OneDrive, include personal Microsoft accounts.
2. Under **Authentication**, set **Allow public client flows** to **Yes**. No redirect URI or secret is needed.
3. Under **API permissions**, add Microsoft Graph delegated `Files.ReadWrite` and `offline_access`.
4. Enter the application (client) ID and tenant ID on **Cloud → OneDrive** (Super Admin) or in `.env`.
5. Click **Connect OneDrive**, open `microsoft.com/devicelogin` and enter the code shown.

Files go to `NCM-Backups/<hostname>/` or `NCM-Backups/YYYY/MM/DD/`. Retention on disk never deletes cloud copies.

## Security

- Device passwords and cloud tokens are encrypted at rest with `DEVICE_ENCRYPTION_KEY`.
- Passwords: at least 10 characters with three character types, not common, not containing the username. Weak passwords, new accounts and admin resets must be changed at the next sign-in.
- 5 failed sign-ins lock a username for 15 minutes (unknown usernames behave the same); 20 from one address lock the address.
- Changing a password or role, or disabling an account, ends that user's sessions. **My profile → Sign out everywhere** ends all of your own.
- Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, `no-referrer`, HSTS over HTTPS, `no-store` on API responses. API docs are off by default.
- Health checks and the assistant can only run read-only `display` / `show` commands.
- Everything is recorded in the audit log.

Please report security problems privately to the maintainer instead of opening a public issue.

## Roles and permissions

| Permission | Super Admin | Admin | Operator | Viewer |
|---|:-:|:-:|:-:|:-:|
| `dashboard.view` | ✓ | ✓ | ✓ | ✓ |
| `devices.create` | ✓ | ✓ | ✓ |  |
| `devices.delete` | ✓ | ✓ |  |  |
| `devices.edit` | ✓ | ✓ | ✓ |  |
| `devices.test` | ✓ | ✓ | ✓ |  |
| `devices.view` | ✓ | ✓ | ✓ | ✓ |
| `backups.delete` | ✓ | ✓ |  |  |
| `backups.diff` | ✓ | ✓ | ✓ | ✓ |
| `backups.download` | ✓ | ✓ | ✓ |  |
| `backups.run` | ✓ | ✓ | ✓ |  |
| `backups.view` | ✓ | ✓ | ✓ | ✓ |
| `schedules.create` | ✓ | ✓ | ✓ |  |
| `schedules.delete` | ✓ | ✓ |  |  |
| `schedules.edit` | ✓ | ✓ | ✓ |  |
| `schedules.view` | ✓ | ✓ | ✓ | ✓ |
| `files.view` | ✓ | ✓ | ✓ | ✓ |
| `health.manage` | ✓ | ✓ |  |  |
| `health.run` | ✓ | ✓ | ✓ |  |
| `health.view` | ✓ | ✓ | ✓ | ✓ |
| `cloud.manage` | ✓ | ✓ |  |  |
| `cloud.sync` | ✓ | ✓ | ✓ |  |
| `cloud.view` | ✓ | ✓ | ✓ | ✓ |
| `ai.manage` | ✓ | ✓ |  |  |
| `ai.run` | ✓ | ✓ | ✓ |  |
| `ai.view` | ✓ | ✓ | ✓ | ✓ |
| `users.create` | ✓ | ✓ |  |  |
| `users.delete` | ✓ |  |  |  |
| `users.edit` | ✓ | ✓ |  |  |
| `users.view` | ✓ | ✓ |  |  |
| `audit.view` | ✓ | ✓ |  |  |
| `security.view` | ✓ | ✓ |  |  |

Only a Super Admin can create Super Admins or change OAuth client credentials.

## Upgrading

1. Back up `backend/.env`, `backend/.secret_key` (if present), the database and `BACKUP_ROOT`.
2. Replace the code (`git pull`, or copy the new files) and **keep** your `.env` and database.
3. Update the Python packages: `.venv/bin/pip install -r backend/requirements.txt`.
4. Restart NCM. The database is upgraded automatically at start ([Alembic](https://alembic.sqlalchemy.org) migrations in `backend/migrations`).

When upgrading to 2.0: every device gets a daily backup schedule (devices with their own schedule keep it), and users with weak passwords must change them at their next sign-in. See [CHANGELOG.md](CHANGELOG.md).

## Troubleshooting

**"Invalid username or password" after an upgrade or restart.** NCM is probably using a different (new, empty) database. With the default `DATABASE_URL=sqlite:///./ncm.db`, the database is in the folder NCM was *started from*: `run-local.sh` starts it from the project root, a service may start it from `backend/`. Find your `ncm.db` and set its full path in `DATABASE_URL`.

**"Too many failed sign-ins".** Wait 15 minutes or restart NCM.

**Forgot the admin password.** With `DATABASE_URL` set to a full path, run this inside `backend/` with the virtual environment active. It sets a temporary password that must be changed at the next sign-in.

```bash
python3 -c "
import getpass
from app.core.database import init_db, SessionLocal; init_db()
from app.models.user import User; from app.core.security import hash_password
db = SessionLocal(); u = db.query(User).filter_by(username='admin').one()
u.password_hash = hash_password(getpass.getpass('Temporary password: '))
u.must_change_password = True; u.is_active = True; u.token_version += 1; db.commit(); print('Done')"
```

**"Fernet key must be 32 url-safe base64-encoded bytes".** `DEVICE_ENCRYPTION_KEY` is still the placeholder; generate one as shown in [Quick start](#quick-start).

**A health command shows "Not supported".** Your software version uses different syntax; edit the command list in **Health → Schedule & settings**.

## Development

Run the automated checks (in `backend/checks`) from the `backend` folder. They use a throw-away database, not yours:

```bash
pip install pytest
pytest
```

To change the database schema, edit the model in `app/models`, then add a migration and fill in its `upgrade()`:

```bash
alembic revision -m "add column x to devices"
```

## API

The web UI uses a JSON API under `/api` with bearer tokens from `POST /api/auth/login`. Set `ENABLE_API_DOCS=true` to browse it at `/docs`. Main groups: `auth`, `devices`, `backups`, `schedules` (including `/schedules/policy`), `health`, `cloud/gdrive`, `cloud/onedrive`, `ai`, `files`, `users`, `audit`, `security`, `dashboard`.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

Copyright (c) 2026 Justin Thomas. Released under the [MIT License](LICENSE).

**Trademarks:** Network Config Manager™, NCM™ and the NCM logo are trademarks of Justin Thomas. The MIT License covers the code only; it does not grant use of these names or the logo.

---

© 2026 Justin Thomas. Network Config Manager™ and NCM™ are trademarks of Justin Thomas.
