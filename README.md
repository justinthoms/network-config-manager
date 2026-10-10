# Network Config Manager

Open-source multi-vendor Network Configuration Manager for NOC and network operations teams.

## What's new in v0.6: choose your AI provider from the web

- **Multiple AI providers.** Add Google Gemini, OpenRouter, OpenAI, Groq, Mistral, Anthropic Claude, a local Ollama server, or any OpenAI-compatible endpoint, with API keys entered in the browser (stored encrypted).
- **Model picker.** "Load models" lists the models your key can use. Free models (for example OpenRouter `:free` models) are shown first and marked, and models without tool support are flagged, because the chat assistant needs tool calling. "Test" sends a tiny request before you save.
- **Shared and personal keys.** Admins add shared providers on the AI review page and choose the default for everyone. Every user can add personal API keys and choose their own provider and model in **My profile** (avatar menu).
- **Fallback.** If a provider hits its free limit, has no quota for the model, or fails, NCM tries the next enabled provider automatically (can be switched off).
- **My profile** also lets users change their display name, email and password.
- Upgrading: a `GEMINI_API_KEY` in `.env` is imported once as a shared "Google Gemini" provider. After that, manage keys in the web UI.

Free options to try: OpenRouter models ending in `:free`, Groq, Mistral's free plan, Gemini's free tier, or Ollama on your own server (no key, nothing leaves your network). Free tiers change often and may use submitted data for training; check each provider's terms.

## What's new in v0.5: NCM assistant (chat)

Click **Ask NCM** in the bottom-right corner and ask questions in English or Malayalam, for example:

- "Which VLANs were added in the last 7 days?"
- "What changed on TSR-CORE-R01 last time, which commands?"
- "Which devices still use SNMP community strings?"
- "Which backups failed this week and why?"
- "Which port went down last on CLT-ACC-SW07?" (needs live commands, see below)

The assistant uses Gemini function calling with NCM's own data: device list, backup status, recent changes with diffs, full diffs between backups, text search across the latest config of every device, and config sections. Every answer shows which lookups it made. Secrets are masked, and the IP-hiding option from the AI review page also applies.

**Live read-only commands (off by default).** Backups cannot tell you the current state of a port or the latest log messages. An admin can turn on *Let the assistant run live read-only commands* on the AI review page. The assistant may then run one `display` (Huawei) or `show` (Juniper, Arista) command at a time over SSH. Commands that change configuration, reboot, copy, delete, debug, ping and similar are refused, output filters are limited to match/include/exclude/count/last style pipes, and every command is written to the audit log. Only users who can test SSH (operator and above) can trigger live commands.

The conversation is kept in the browser tab only (cleared on sign-out). Each question uses one or a few Gemini requests depending on how many lookups are needed.

## What's new in v0.4: AI review with Google Gemini

- **AI config check.** On any successful backup (Backups page, or the backup viewer) click the AI button. Gemini audits the configuration and returns a score out of 100, what is done well, and findings by severity with the config line it saw, a recommendation and suggested vendor commands.
- **AI security review of changes.** On the Config changes page, compare two backups of a device and click **AI security review**. Gemini rates the change low, medium, high or critical risk, explains each change and its impact, lists findings and suggests rollback commands.
- **Automatic change review** (optional). Turn it on in the AI review tab and every backup whose configuration changed is reviewed in the background. Risk badges appear on the Overview and Backups pages.
- **Privacy.** Passwords, secrets, SNMP communities, keys, hashes and certificates are always masked before anything is sent. Well-known weak values (for example `public`) are still hidden but marked as weak so they can be reported. IP addresses can optionally be replaced by placeholders and are restored in the result.
- Results are saved, so opening a review again does not use another Gemini request. "Run again" forces a fresh check.

### Setup (v0.4/v0.5 only, see v0.6 for the web setup)

1. Create an API key at [Google AI Studio](https://aistudio.google.com) (Get API key).
2. Add it to `backend/.env` and restart NCM:
   ```
   GEMINI_API_KEY=your-key
   GEMINI_MODEL=gemini-3-flash-preview
   ```
   If the configured model is not available to your key, NCM automatically picks the newest Flash model it can use and shows it on the AI review page. You can still set `GEMINI_MODEL` to any model name shown in AI Studio.

Notes: the free tier has per-minute and per-day request limits; NCM shows a clear message when they are reached. On the free tier Google may use submitted content to improve its products. NCM masks secrets, and you can also hide IP addresses, but check your organisation's policy. AI results can be wrong; always verify suggested commands before running them.

Permissions: `ai.view` (all roles), `ai.run` (operator and above), `ai.manage` settings (admin and super admin).

## What's new in v0.3

- **New clean interface.** Light, enterprise-style layout with top navigation, global device search (press `/`), account menu, and an improved dark mode.
- **New overview.** Stats strip with 14-day sparklines, device table sorted by what needs attention, backup health donut, activity chart, and recent config changes with inline diff snippets. Range selector (7, 14, 30 days) and a "Back up all devices" button.
- **Vendor badges.** Each device shows a vendor-coloured badge with a router, switch or firewall icon (detected from model and hostname). To show your own logo images instead, put them in `backend/app/web/vendors/` as `<vendor>.svg|png|webp` (see the README in that folder).
- **App icon.** NCM now has its own icon, favicon and web app manifest, so it can be installed from the browser.
- **Better change detection.** The config fingerprint ignores timestamp and header lines (for example Huawei `!Last configuration was updated at`), so a backup is only marked "changed" when the configuration really changed. Older backups are re-fingerprinted automatically in the background at startup.

## What's new in v0.2

- **New interface.** Rack-faceplate overview where every device is a port with a status LED, 14-day backup activity chart, a "needs attention" list, mobile layout and an improved dark mode.
- **Config change tracking.** Per-device change timeline (`+added / −removed` for every backup versus the one before), side-by-side or unified diff with inline highlighting, folding of unchanged lines, "hide timestamp and header noise", ignore whitespace, compare across two different devices, and `.diff` download.
- **Web file browser.** Browse `BACKUP_ROOT` from the browser with breadcrumbs, search across all files, preview with find-in-file, download a file or a whole folder as ZIP, and tick any two files to compare them. Sandboxed to the backup directory.
- **Google Drive backup.** Connect a Google account with OAuth (`drive.file` scope: NCM only sees files it creates). Upload pending backups in one click, or turn on automatic upload after every successful backup. Files land in `NCM-Backups/<hostname>/<hostname>_YYYYmmdd_HHMMSS.conf`. Browse and download Drive copies from NCM.
- Backups now store size and SHA-256, so NCM knows whether the config changed. Single backups can be deleted (`backups.delete`).
- The UI now uses real role permissions from the backend (`/api/auth/me` returns `permissions`).

## Features

- 🔐 Secure authentication and role-based access control
- 🖥️ Multi-vendor device management (Huawei, Juniper, Arista)
- 💾 Manual and scheduled configuration backups with retries and retention
- 🔄 Config diff and change timeline
- 📁 Web file browser for the backup directory
- ☁️ Google Drive off-site copies
- 🧾 Audit logs
- 📥 CSV import and export
- 🌙 Dark and light mode
- 🔒 Encrypted device credentials

## Run locally

```bash
./run-local.sh
```

Then edit `backend/.env` (created from `.env.example`): set `SECRET_KEY`, `DEVICE_ENCRYPTION_KEY` and `DEFAULT_ADMIN_PASSWORD`. Generate a Fernet key with:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Upgrading from v0.1: just restart. New tables and columns are added automatically; existing backups keep working (old backups have no checksum, so their "changed" flag is calculated from the file contents instead).

## Google Drive setup

1. In [Google Cloud Console](https://console.cloud.google.com/apis/library/drive.googleapis.com), select or create a project and enable the **Google Drive API**.
2. Under **Google Auth Platform**, configure the consent screen and add the scope `https://www.googleapis.com/auth/drive.file`. Set publishing status to **In production**. In *Testing* mode Google revokes the refresh token after 7 days and uploads stop.
3. Create an **OAuth client ID** of type **Web application**. Add the authorised redirect URI shown on NCM's Google Drive page, which is `{PUBLIC_BASE_URL}/api/cloud/gdrive/callback`, for example `http://localhost:8000/api/cloud/gdrive/callback`.
4. Put the client ID and secret in `backend/.env` as `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, or paste them on the Google Drive page as Super Admin (the secret is stored encrypted).
5. Set `PUBLIC_BASE_URL` to the address your browser uses to open NCM, restart, and click **Connect Google Drive**.

Notes: Google only accepts `http://` redirect URIs for `localhost`. For access by IP or hostname, serve NCM over HTTPS (for example behind nginx or Caddy). Disk retention never deletes Drive copies; deleting a backup in NCM does not delete it from Drive.

## Permissions

| Permission | super_admin | admin | operator | viewer |
|---|:-:|:-:|:-:|:-:|
| files.view (file browser) | ✓ | ✓ | ✓ | ✓ |
| cloud.view | ✓ | ✓ | ✓ | ✓ |
| cloud.sync (upload to Drive) | ✓ | ✓ | ✓ | |
| cloud.manage (connect, settings) | ✓ | ✓ | | |
| backups.delete | ✓ | ✓ | | |

OAuth client credentials can only be changed by a Super Admin.

## API additions

- `GET /api/dashboard/summary`
- `GET /api/backups/changes?device_id=` and `GET /api/backups/diff?backup_a=&backup_b=&ignore_volatile=&ignore_whitespace=&cross_device=`
- `DELETE /api/backups/{id}`
- `GET /api/files/browse|search|view|download|diff|stats`
- `GET /api/cloud/gdrive/status|auth-url|files`, `PUT /api/cloud/gdrive/credentials|settings`, `POST /api/cloud/gdrive/sync|upload/{id}|disconnect`

## License

MIT License
