# Changelog

## 2.0.0 (October 2026)

The first 2.x release. It brings together the work developed as 0.7, 0.8 and 0.9 (details below).

**Added**
- Automatic backup for all devices: every device is backed up daily from 01:00 server time, new devices are scheduled automatically, and start times are spread over 15 minutes.
- Device health checks: CPU, memory, hardware, alarms, interfaces and logs by syslog level from read-only commands, with OK / Warning / Critical status, history, a schedule and an optional AI summary in English or Malayalam.
- OneDrive as a second off-site backup target next to Google Drive.
- Security page with a self-check of the server's configuration.

**Security**
- A weak or placeholder `SECRET_KEY` is no longer used; NCM generates a random key in `backend/.secret_key`.
- Password policy, forced password change for weak, new and reset passwords, sign-in lockout, session revocation, *Sign out everywhere*, idle sign-out.
- Security headers, API docs off by default, private file permissions.

**Changed**
- Navigation labels *Config changes* and *File browser* are now *Changes* and *Files*.
- README rewritten with screenshots; release notes moved to this file.

**Upgrade notes**
- Keep your existing `backend/.env` (especially `DEVICE_ENCRYPTION_KEY`) and database. Set `DATABASE_URL` to a full path.
- On first start, devices without a schedule get a daily one; devices with their own schedule keep it.
- Users with weak passwords must set a new one at their next sign-in.
- Back up `backend/.secret_key` together with `.env`.

### 0.9: Automatic backup for all devices, and security hardening

#### Automatic backup for all devices
- **On by default after upgrading:** every device gets a backup schedule **every 24 hours, starting 01:00 server time**. Devices added later, by hand or by CSV import, are scheduled automatically.
- Devices start up to 15 minutes apart, so many devices do not connect at the same second.
- Devices that already had their own schedule keep it. Paused schedules are switched on. **Apply to all devices** on the Schedules page sets every device to the same frequency.
- Change the frequency, start time and how many backups to keep on the Schedules page. Turning it off keeps existing schedules but stops scheduling new devices.

#### Security
- **Signing key:** if `SECRET_KEY` in `.env` is a placeholder or shorter than 32 characters, NCM generates a random key in `backend/.secret_key` (mode 600) and uses that. Before this, a known key let anyone forge an admin login.
- **Password policy:** at least 10 characters, three of lower/upper/digits/symbols, not a common password, not containing the username.
- **Forced password change:** anyone signing in with a weak password, the default admin password, a new account, or a password reset by an admin must set a new password before doing anything else.
- **Sign-in lockout:** 5 failed sign-ins lock that username for 15 minutes (also for usernames that do not exist, so the lock does not reveal valid names); 20 from one address lock that address. A wrong username takes as long as a wrong password.
- **Sessions end** when the password changes, the role changes or the account is disabled. **Sign out everywhere** is in My profile. Browsers sign out after **30 minutes idle**.
- **Security headers:** Content-Security-Policy, X-Frame-Options DENY, nosniff, no-referrer, HSTS over HTTPS, no-store on API responses.
- **API docs** (`/docs`, `/redoc`, `/openapi.json`) are off unless `ENABLE_API_DOCS=true`.
- **File permissions:** `.env`, the database, `.secret_key` and the backup folder are made readable by the NCM user only; new files are created private.
- **Security page** (user menu, Admin and Super Admin): checks the items above, HTTPS, number of Super Admins, users who must change their password, and last sign-in time and address per account.
- All of this is in the audit log (`LOGIN` FAILED/BLOCKED, `PASSWORD_CHANGE`, `LOGOUT_ALL`, `BACKUP_POLICY`).

After upgrading, existing users with weak passwords are asked to change them at their next sign-in. Keep `backend/.secret_key` with your server backups; if it is lost, everyone just signs in again.

#### Recommended: HTTPS
NCM itself serves plain HTTP. On the server, put it behind nginx or Caddy with a certificate, and allow port 8000 only from localhost. Example Caddyfile:

```
ncm.example.local {
    reverse_proxy 127.0.0.1:8000
}
```
Then start NCM with `uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1`.

### 0.8: Device health checks

New **Health** page. NCM logs in over SSH and runs only read-only commands (`display` on Huawei, `show` on Juniper and Arista), then shows:

- **CPU and memory** with warning (75% / 80%) and critical (90%) levels, plus model, software version and uptime.
- **Hardware and alarms:** failed fans, power supplies, temperature problems and active Major/Minor alarms.
- **Interfaces:** up / down / admin-down counts, err-disabled ports and ports with error counters.
- **Logs by level:** every log line is sorted by syslog level (0 emergency ... 7 debug). Filter by level, search text, or show only the last N hours. Junos logs have no level by default, so NCM guesses it from the text and marks it `~` (add `explicit-priority` to the syslog file for exact levels).
- **Status per device:** OK, Warning, Critical, or Failed (SSH did not work). Critical and failed devices show as a count on the Health tab.
- **AI summary** (optional, uses the AI provider from the AI review page): explains what needs attention and which commands to run next. Choose English or Malayalam. Run on click, or automatically for Warning/Critical or for every check. Secrets are masked first.
- **Check now** from the Health page or the new pulse button on the Devices page, **Check all devices** (4 at a time), and an optional **schedule** (every 15 minutes to 7 days).
- **Command lists per vendor** can be changed in *Schedule & settings*. Only read-only commands are accepted (`reboot`, `reset`, `configure`, `write`, `| save` and similar are refused). Commands a device does not know are skipped and listed in the result.
- History: the last 50 checks per device are kept (configurable). Every check is in the audit log.

Default commands:

| Huawei | Juniper | Arista |
|---|---|---|
| display version, cpu-usage, memory-usage, device, fan, power, temperature all, alarm active, interface brief, logbuffer | show version, chassis routing-engine, chassis alarms, system alarms, chassis environment, interfaces terse, log messages \| last 500 | show version, processes top once, system environment all, interfaces status, interfaces counters errors, logging 500 |

Permissions: `health.view` (all roles), `health.run` (operator and above), `health.manage` settings and schedule (admin and super admin). The AI summary also needs `ai.run`.

Navigation labels "Config changes" and "File browser" are now "Changes" and "Files" to make room.

### 0.7: OneDrive backup

- **Microsoft OneDrive as a second off-site target.** The Google Drive page is now **Cloud**, with a Google Drive / OneDrive switch. Each provider has its own connection, automatic upload switch, folder name and pending count, so you can use one, the other, or both.
- **Device-code sign-in.** Click **Connect OneDrive**, open `microsoft.com/devicelogin` on any device and enter the code shown. No redirect URI or HTTPS is needed, so it works when NCM is opened by IP address. The refresh token is stored encrypted and rotated, so uploads keep working after restarts.
- **Folder layout.** `NCM-Backups/<hostname>/` (default) or `NCM-Backups/YYYY/MM/DD/`. Folders are created automatically. Files larger than 4 MB use an upload session.
- **Test upload** writes `_ncm-test.txt` to the OneDrive folder to check the connection.
- The Backups table now has a **Cloud** column showing Google Drive and OneDrive copies, with an upload button for each connected provider.

: choose your AI provider from the web

- **Multiple AI providers.** Add Google Gemini, OpenRouter, OpenAI, Groq, Mistral, Anthropic Claude, a local Ollama server, or any OpenAI-compatible endpoint, with API keys entered in the browser (stored encrypted).
- **Model picker.** "Load models" lists the models your key can use. Free models (for example OpenRouter `:free` models) are shown first and marked, and models without tool support are flagged, because the chat assistant needs tool calling. "Test" sends a tiny request before you save.
- **Shared and personal keys.** Admins add shared providers on the AI review page and choose the default for everyone. Every user can add personal API keys and choose their own provider and model in **My profile** (avatar menu).
- **Fallback.** If a provider hits its free limit, has no quota for the model, or fails, NCM tries the next enabled provider automatically (can be switched off).
- **My profile** also lets users change their display name, email and password.
- Upgrading: a `GEMINI_API_KEY` in `.env` is imported once as a shared "Google Gemini" provider. After that, manage keys in the web UI.

Free options to try: OpenRouter models ending in `:free`, Groq, Mistral's free plan, Gemini's free tier, or Ollama on your own server (no key, nothing leaves your network). Free tiers change often and may use submitted data for training; check each provider's terms.

## 0.5: NCM assistant (chat)

Click **Ask NCM** in the bottom-right corner and ask questions in English or Malayalam, for example:

- "Which VLANs were added in the last 7 days?"
- "What changed on TSR-CORE-R01 last time, which commands?"
- "Which devices still use SNMP community strings?"
- "Which backups failed this week and why?"
- "Which port went down last on CLT-ACC-SW07?" (needs live commands, see below)

The assistant uses Gemini function calling with NCM's own data: device list, backup status, recent changes with diffs, full diffs between backups, text search across the latest config of every device, and config sections. Every answer shows which lookups it made. Secrets are masked, and the IP-hiding option from the AI review page also applies.

**Live read-only commands (off by default).** Backups cannot tell you the current state of a port or the latest log messages. An admin can turn on *Let the assistant run live read-only commands* on the AI review page. The assistant may then run one `display` (Huawei) or `show` (Juniper, Arista) command at a time over SSH. Commands that change configuration, reboot, copy, delete, debug, ping and similar are refused, output filters are limited to match/include/exclude/count/last style pipes, and every command is written to the audit log. Only users who can test SSH (operator and above) can trigger live commands.

The conversation is kept in the browser tab only (cleared on sign-out). Each question uses one or a few Gemini requests depending on how many lookups are needed.

## 0.4: AI review with Google Gemini

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

## 0.3

- **New clean interface.** Light, enterprise-style layout with top navigation, global device search (press `/`), account menu, and an improved dark mode.
- **New overview.** Stats strip with 14-day sparklines, device table sorted by what needs attention, backup health donut, activity chart, and recent config changes with inline diff snippets. Range selector (7, 14, 30 days) and a "Back up all devices" button.
- **Vendor badges.** Each device shows a vendor-coloured badge with a router, switch or firewall icon (detected from model and hostname). To show your own logo images instead, put them in `backend/app/web/vendors/` as `<vendor>.svg|png|webp` (see the README in that folder).
- **App icon.** NCM now has its own icon, favicon and web app manifest, so it can be installed from the browser.
- **Better change detection.** The config fingerprint ignores timestamp and header lines (for example Huawei `!Last configuration was updated at`), so a backup is only marked "changed" when the configuration really changed. Older backups are re-fingerprinted automatically in the background at startup.

## 0.2

- **New interface.** Rack-faceplate overview where every device is a port with a status LED, 14-day backup activity chart, a "needs attention" list, mobile layout and an improved dark mode.
- **Config change tracking.** Per-device change timeline (`+added / −removed` for every backup versus the one before), side-by-side or unified diff with inline highlighting, folding of unchanged lines, "hide timestamp and header noise", ignore whitespace, compare across two different devices, and `.diff` download.
- **Web file browser.** Browse `BACKUP_ROOT` from the browser with breadcrumbs, search across all files, preview with find-in-file, download a file or a whole folder as ZIP, and tick any two files to compare them. Sandboxed to the backup directory.
- **Google Drive backup.** Connect a Google account with OAuth (`drive.file` scope: NCM only sees files it creates). Upload pending backups in one click, or turn on automatic upload after every successful backup. Files land in `NCM-Backups/<hostname>/<hostname>_YYYYmmdd_HHMMSS.conf`. Browse and download Drive copies from NCM.
- Backups now store size and SHA-256, so NCM knows whether the config changed. Single backups can be deleted (`backups.delete`).
- The UI now uses real role permissions from the backend (`/api/auth/me` returns `permissions`).
