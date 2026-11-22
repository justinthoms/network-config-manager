# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Check GitHub releases for a new NCM version and upgrade in place from the web UI.

What an upgrade does, in order. Nothing in the running install changes before
step 6, so a failure before then leaves NCM exactly as it was.

 1. Download the release archive from GitHub (only from the configured API
    host, codeload.github.com or github.com).
 2. Check the archive: one top folder, no absolute paths, no "..", no links,
    and it must contain backend/app/main.py.
 3. Unpack it into backend/.upgrade/staging.
 4. If requirements.txt changed, install the new Python packages.
 5. Test the new code in a separate process: import the app and run its
    database migrations on a COPY of the database (SQLite) or on an empty
    test database (PostgreSQL).
 6. Snapshot the current code and the SQLite database into
    backend/.upgrade/rollback/<old version>-<time>/ (the last 3 are kept).
 7. Replace the code: backend/app, backend/migrations, backend/checks, the
    files next to them and the docs. Never touched: backend/.env,
    backend/.secret_key, the database, BACKUP_ROOT, custom vendor logos.
 8. Restart NCM, which upgrades the real database at start.

Restart: a single process re-executes itself (same PID, so systemd and Docker
are fine); with --workers N the uvicorn supervisor gets SIGHUP and restarts
every worker; with --reload the file change restarts it.

If the web UI does not come back, roll back from a shell:
    cd backend && ../.venv/bin/python -m app.services.updater rollback
"""
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

try:                                   # Linux / macOS
    import fcntl
except ImportError:                    # Windows
    fcntl = None
    import msvcrt

from app.core.config import BASE_DIR, settings
from app.version import __version__

logger = logging.getLogger("ncm.updater")

ROOT = BASE_DIR.parent                        # folder that holds backend/
WORK = BASE_DIR / ".upgrade"
STATE_FILE = WORK / "state.json"
LOCK_FILE = WORK / "upgrade.lock"
ROLLBACK_DIR = WORK / "rollback"
KEEP_SNAPSHOTS = 3
MAX_DOWNLOAD = 150 * 1024 * 1024
# Paths (relative to ROOT) that an upgrade replaces. Everything else is left alone.
MANAGED = ["backend/app", "backend/migrations", "backend/checks", "backend/requirements.txt",
           "backend/alembic.ini", "backend/pyproject.toml", "backend/.env.example",
           "README.md", "CHANGELOG.md", "LICENSE", "run-local.sh", "docs"]
VENDOR_LOGOS = "backend/app/web/vendors"       # user files inside a managed folder: kept
K_LATEST = "update.latest"                     # settings_store key: last check result (JSON)
K_AUTO = "update.auto_check"                   # "1" / "0": overrides UPDATE_CHECK_ENABLED

_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


class UpdateError(RuntimeError):
    pass


# ───────────────────────── versions ─────────────────────────

def parse_version(v: str):
    """(major, minor, patch, is_release, prerelease) so that 2.1.0-beta < 2.1.0; None if not a version."""
    m = _SEMVER.match((v or "").strip())
    if not m:
        return None
    pre = m.group(4) or ""
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    return bool(a and b and a > b)


# ───────────────────────── GitHub ─────────────────────────

def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "NCM-updater",
         "X-GitHub-Api-Version": "2022-11-28"}
    if settings.update_github_token:
        h["Authorization"] = f"Bearer {settings.update_github_token}"
    return h


def _api(path: str) -> str:
    repo = settings.update_repo.strip().strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise UpdateError(f"UPDATE_REPO '{repo}' is not in the form owner/name")
    return f"{settings.update_api_url.rstrip('/')}/repos/{repo}{path}"


def _allowed_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return url.startswith(("https://", "http://")) and host in {
        (urlparse(settings.update_api_url).hostname or "").lower(), "codeload.github.com", "github.com",
        "api.github.com"}


def fetch_releases() -> list[dict]:
    """Published releases, newest version first (drafts skipped, pre-releases only if enabled)."""
    try:
        r = httpx.get(_api("/releases?per_page=30"), headers=_headers(), timeout=20, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise UpdateError(f"Cannot reach GitHub: {exc}") from exc
    if r.status_code == 404:
        raise UpdateError(f"Repository {settings.update_repo} was not found (private? set UPDATE_GITHUB_TOKEN)")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise UpdateError("GitHub rate limit reached. Try again later, or set UPDATE_GITHUB_TOKEN.")
    if r.status_code != 200:
        raise UpdateError(f"GitHub answered {r.status_code}")
    out = []
    for rel in r.json():
        tag = rel.get("tag_name") or ""
        if rel.get("draft") or not parse_version(tag):
            continue
        if rel.get("prerelease") and not settings.update_include_prereleases:
            continue
        out.append({"version": tag.lstrip("v"), "tag": tag, "name": rel.get("name") or tag,
                    "notes": (rel.get("body") or "")[:20000], "published_at": rel.get("published_at"),
                    "url": rel.get("html_url"), "prerelease": bool(rel.get("prerelease")),
                    "tarball_url": rel.get("tarball_url")})
    out.sort(key=lambda x: parse_version(x["version"]), reverse=True)
    return out


def check(db) -> dict:
    """Ask GitHub now and store the result."""
    from app.services import settings_store as store
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "current": __version__, "error": None,
              "latest": None, "update_available": False, "releases": []}
    try:
        rels = fetch_releases()
        newer = [r for r in rels if is_newer(r["version"])]
        result.update(latest=rels[0] if rels else None, update_available=bool(newer),
                      releases=newer[:10] or rels[:1])
    except UpdateError as exc:
        result["error"] = str(exc)
    store.set_json(db, K_LATEST, result)
    return result


def auto_check_enabled(db) -> bool:
    from app.services import settings_store as store
    v = store.get(db, K_AUTO)
    return settings.update_check_enabled if v is None else v == "1"


def status(db) -> dict:
    from app.services import settings_store as store
    last = store.get_json(db, K_LATEST) or {}
    if last.get("current") != __version__:       # checked before an upgrade: recompute against this version
        newer = [r for r in last.get("releases", []) if is_newer(r["version"])]
        last.update(current=__version__, update_available=bool(newer), releases=newer or last.get("releases", [])[:1])
    return {"current": __version__, "repo": settings.update_repo, "auto_check": auto_check_enabled(db),
            "check_hours": max(1, settings.update_check_hours), "last_check": last or None,
            "upgrade": read_state(), "environment": environment()}


def start_background_checker():
    """Check now and then every UPDATE_CHECK_HOURS, in one daemon thread per process."""
    def loop():
        from app.core.database import SessionLocal
        time.sleep(30)
        while True:
            db = SessionLocal()
            try:
                if auto_check_enabled(db):
                    from app.services import settings_store as store
                    last = store.get_json(db, K_LATEST) or {}
                    age = _age_hours(last.get("checked_at"))
                    if age is None or age >= max(1, settings.update_check_hours):
                        res = check(db)
                        if res.get("update_available"):
                            logger.info("NCM %s is available (running %s)", res["latest"]["version"], __version__)
            except Exception:
                logger.exception("Update check failed")
            finally:
                db.close()
            time.sleep(1800)
    threading.Thread(target=loop, name="ncm-update-check", daemon=True).start()


def _age_hours(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


# ───────────────────────── environment checks ─────────────────────────

def _db_file() -> Path | None:
    """The SQLite database file, or None for another database."""
    from app.core.database import engine
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database or engine.url.database == ":memory:":
        return None
    return Path(engine.url.database).resolve()


def _parent_cmdline() -> str:
    try:
        return Path(f"/proc/{os.getppid()}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def restart_mode() -> str:
    own = " ".join(sys.argv)
    parent = _parent_cmdline()
    if "--reload" in own or "--reload" in parent:
        return "reload"
    if "uvicorn" in parent and "--workers" in parent and hasattr(signal, "SIGHUP"):
        return "workers"
    if "uvicorn" in own or Path(sys.argv[0]).name.startswith("uvicorn") or "uvicorn" in sys.argv[0]:
        return "exec"
    return "manual"


@functools.lru_cache(maxsize=1)
def _has_pip() -> bool:
    try:
        return subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def environment() -> dict:
    """What the UI shows before an upgrade: problems block it, warnings do not."""
    problems, warnings = [], []
    for rel in ["backend/app", "backend"]:
        if not os.access(ROOT / rel, os.W_OK):
            problems.append(f"NCM cannot write to {ROOT / rel}. Run NCM as the user that owns its files.")
            break
    free = shutil.disk_usage(BASE_DIR).free
    if free < 300 * 1024 * 1024:
        problems.append(f"Only {free // (1024 * 1024)} MB free disk space; at least 300 MB is needed.")
    mode = restart_mode()
    if mode == "manual":
        warnings.append("NCM could not tell how it was started, so it will not restart itself. "
                        "Restart it yourself after the upgrade (for example: sudo systemctl restart ncm).")
    if Path("/.dockerenv").exists():
        warnings.append("NCM runs in a container. An in-app upgrade is lost when the container is re-created "
                        "from its image; rebuild the image with the new version as well.")
    if (ROOT / ".git").exists():
        warnings.append("This install is a git checkout. After the upgrade, git will show the new files as "
                        "changes; run 'git fetch --tags && git checkout <new tag>' to line git up again.")
    db = _db_file()
    if db is None:
        warnings.append("The database is not SQLite, so the upgrade does not snapshot it. Back up the "
                        "database yourself first (for example with pg_dump).")
    if not _has_pip():
        warnings.append("pip is not available; upgrades that need new Python packages will stop safely.")
    return {"restart_mode": mode, "problems": problems, "warnings": warnings, "root": str(ROOT),
            "python": sys.version.split()[0], "database_snapshot": db is not None}


# ───────────────────────── state shared by all workers ─────────────────────────

def read_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return None


def _write_state(state: dict):
    WORK.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE_FILE)


class _Run:
    """One upgrade or rollback: a step list the UI polls."""

    STEPS = {
        "upgrade": ["download", "verify", "unpack", "packages", "test", "snapshot", "install", "restart"],
        "rollback": ["snapshot", "install", "restart"],
    }

    def __init__(self, kind: str, target: str, user: str):
        self.state = {"kind": kind, "from": __version__, "to": target, "by": user, "status": "running",
                      "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
                      "error": None, "log": [], "snapshot": None,
                      "steps": [{"id": s, "status": "pending"} for s in self.STEPS[kind]]}
        _write_state(self.state)

    def step(self, sid: str, status: str = "running", note: str | None = None):
        for s in self.state["steps"]:
            if s["id"] == sid:
                s["status"] = status
                if note:
                    s["note"] = note
        if note:
            self.log(note)
        _write_state(self.state)

    def log(self, text: str):
        logger.info("upgrade: %s", text)
        self.state["log"].append(f"{datetime.now().strftime('%H:%M:%S')}  {text}")
        self.state["log"] = self.state["log"][-200:]
        _write_state(self.state)

    def finish(self, status: str, error: str | None = None):
        self.state.update(status=status, error=error, finished_at=datetime.now(timezone.utc).isoformat())
        for s in self.state["steps"]:
            if s["status"] == "running":
                s["status"] = "failed" if error else "done"
        if error:
            self.log("Stopped: " + error)
        _write_state(self.state)


_lock_fh = None


def _take_lock():
    """One upgrade at a time, across worker processes."""
    global _lock_fh
    WORK.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_FILE, "a")
    try:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        raise UpdateError("An upgrade or rollback is already running")
    _lock_fh = fh


def _release_lock():
    global _lock_fh
    if _lock_fh:
        try:
            if fcntl:
                fcntl.flock(_lock_fh, fcntl.LOCK_UN)
            else:
                _lock_fh.seek(0)
                msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            _lock_fh.close()
            _lock_fh = None


# ───────────────────────── upgrade steps ─────────────────────────

def _download(url: str, dest: Path, run: _Run):
    if not _allowed_host(url):
        raise UpdateError(f"Refusing to download from {urlparse(url).hostname}")
    size, h = 0, hashlib.sha256()
    with httpx.Client(headers=_headers(), timeout=60, follow_redirects=True) as c:
        with c.stream("GET", url) as r:
            if not _allowed_host(str(r.url)):
                raise UpdateError(f"Download was redirected to {r.url.host}, which is not allowed")
            if r.status_code != 200:
                raise UpdateError(f"Download failed: GitHub answered {r.status_code}")
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes(1 << 16):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise UpdateError("The release archive is larger than 150 MB")
                    h.update(chunk)
                    fh.write(chunk)
    run.log(f"Downloaded {size / 1024:.0f} KB, sha256 {h.hexdigest()[:16]}…")


def _safe_members(tf: tarfile.TarFile) -> tuple[str, list[tarfile.TarInfo]]:
    members = [m for m in tf.getmembers() if m.name not in ("pax_global_header",) and m.type != tarfile.XGLTYPE]
    tops = {m.name.split("/", 1)[0] for m in members}
    if len(tops) != 1:
        raise UpdateError("The archive does not have exactly one top folder")
    top = tops.pop()
    for m in members:
        p = Path(m.name)
        if p.is_absolute() or ".." in p.parts:
            raise UpdateError(f"Unsafe path in archive: {m.name}")
        if not (m.isfile() or m.isdir()):
            raise UpdateError(f"Archive contains a link or special file: {m.name}")
    names = {m.name for m in members}
    for need in ("backend/app/main.py", "backend/requirements.txt", "backend/migrations/env.py"):
        if f"{top}/{need}" not in names:
            raise UpdateError(f"This release does not look like NCM: {need} is missing")
    return top, members


def _unpack(archive: Path, staging: Path) -> Path:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tf:
        top, members = _safe_members(tf)
        for m in members:
            target = staging / m.name
            if m.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(m)
            with open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)
    return staging / top


def _staged_version(new_root: Path, fallback: str) -> str:
    try:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (new_root / "backend/app/version.py").read_text())
        return m.group(1) if m else fallback
    except OSError:
        return fallback


def _pip(requirements: Path, run: _Run):
    run.log("Installing Python packages from requirements.txt (this can take a few minutes)")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                        str(requirements)], capture_output=True, text=True, timeout=1200)
    if r.returncode != 0:
        raise UpdateError("Installing Python packages failed: " + (r.stderr or r.stdout)[-800:])


def _smoke_test(new_root: Path, run: _Run):
    """Start the new code in a separate process and run its migrations on a throw-away database."""
    tmp = Path(tempfile.mkdtemp(prefix="ncm-upgrade-test-"))
    try:
        db = _db_file()
        test_db = tmp / "test.db"
        if db is not None and db.exists():
            _sqlite_copy(db, test_db)
            run.log("Testing the new version's database migrations on a copy of your database")
        else:
            run.log("Testing the new version's database migrations on an empty test database")
        env = dict(os.environ, DATABASE_URL=f"sqlite:///{test_db}", BACKUP_ROOT=str(tmp / "backups"),
                   SECRET_KEY="upgrade-test-" + "x" * 40, RESTRICT_FILE_PERMISSIONS="false",
                   PYTHONDONTWRITEBYTECODE="1")
        code = ("import app.main\nfrom app.core.database import migrate\nmigrate()\n"
                "from app.version import __version__\nprint('NCM-OK', __version__)")
        r = subprocess.run([sys.executable, "-c", code], cwd=new_root / "backend", env=env,
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or "NCM-OK" not in r.stdout:
            raise UpdateError("The new version did not start in the test: " + (r.stderr or r.stdout)[-1200:])
        run.log("The new version started and migrated the test database")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _sqlite_copy(src: Path, dst: Path):
    """Consistent copy of a live SQLite database."""
    s, d = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _snapshot(run: _Run) -> Path:
    ROLLBACK_DIR.mkdir(parents=True, exist_ok=True)
    on_disk = _staged_version(ROOT, __version__)       # may differ from the running one before a restart
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    snap = ROLLBACK_DIR / f"{on_disk}-{stamp}"
    n = 1
    while snap.exists():
        n += 1
        snap = ROLLBACK_DIR / f"{on_disk}-{stamp}-{n}"
    snap.mkdir()
    for rel in MANAGED:
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, snap / "code" / rel, ignore=shutil.ignore_patterns("__pycache__"))
        elif src.is_file():
            (snap / "code" / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, snap / "code" / rel)
    db = _db_file()
    meta = {"version": on_disk, "created_at": datetime.now(timezone.utc).isoformat(), "database": None}
    if db is not None and db.exists():
        _sqlite_copy(db, snap / "database.sqlite")
        meta["database"] = str(db)
    (snap / "snapshot.json").write_text(json.dumps(meta, indent=1))
    run.log(f"Saved the current version to {snap.relative_to(BASE_DIR)}"
            + (" with a copy of the database" if meta["database"] else ""))
    # keep the newest few
    for old in sorted(snapshots_paths(), key=lambda p: p.stat().st_mtime)[:-KEEP_SNAPSHOTS]:
        shutil.rmtree(old, ignore_errors=True)
    return snap


def _install_tree(src_root: Path, run: _Run):
    """Replace every managed path with the one under src_root (missing there = removed)."""
    logos = ROOT / VENDOR_LOGOS
    keep_logos = {p.name: p.read_bytes() for p in logos.iterdir()
                  if p.is_file() and p.name != "README.txt"} if logos.is_dir() else {}
    for rel in MANAGED:
        new, cur = src_root / rel, ROOT / rel
        if new.is_dir():
            staged = cur.with_name(cur.name + ".ncm-new")
            old = cur.with_name(cur.name + ".ncm-old")
            for p in (staged, old):
                if p.exists():
                    shutil.rmtree(p)
            shutil.copytree(new, staged, ignore=shutil.ignore_patterns("__pycache__"))
            if cur.exists():
                cur.rename(old)
            staged.rename(cur)
            shutil.rmtree(old, ignore_errors=True)
        elif new.is_file():
            cur.parent.mkdir(parents=True, exist_ok=True)
            tmp = cur.with_name(cur.name + ".ncm-new")
            shutil.copy2(new, tmp)
            tmp.replace(cur)
        elif cur.exists():
            shutil.rmtree(cur) if cur.is_dir() else cur.unlink()
    if keep_logos:
        logos.mkdir(parents=True, exist_ok=True)
        for name, data in keep_logos.items():
            (logos / name).write_bytes(data)
        run.log(f"Kept {len(keep_logos)} custom vendor logo(s)")


def _restart(run: _Run):
    mode = restart_mode()
    if mode == "manual":
        run.log("Restart NCM now to finish (for example: sudo systemctl restart ncm)")
        return False

    def later():
        time.sleep(1.5)            # let the browser read the last status
        logger.warning("Restarting NCM for the upgrade (%s)", mode)
        if mode == "workers":
            os.kill(os.getppid(), signal.SIGHUP)
        elif mode == "exec":
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os.execv(sys.executable, [sys.executable] + sys.argv)
        # reload: uvicorn's reloader has seen the new files already
    run.log({"workers": "Restarting all worker processes", "exec": "Restarting NCM",
             "reload": "Development mode: uvicorn reloads the new code by itself"}[mode])
    threading.Thread(target=later, name="ncm-restart", daemon=True).start()
    return True


def start_upgrade(release: dict, username: str) -> dict:
    """Start an upgrade in a background thread; returns the initial state."""
    if not is_newer(release["version"]):
        raise UpdateError(f"{release['version']} is not newer than the running {__version__}")
    env = environment()
    if env["problems"]:
        raise UpdateError(" ".join(env["problems"]))
    _take_lock()
    try:
        run = _Run("upgrade", release["version"], username)
    except Exception:
        _release_lock()
        raise
    threading.Thread(target=_do_upgrade, args=(release, run), name="ncm-upgrade", daemon=True).start()
    return run.state


def _do_upgrade(release: dict, run: _Run):
    staging = WORK / "staging"
    req_changed = False
    try:
        run.step("download")
        archive = WORK / "download.tar.gz"
        _download(release["tarball_url"], archive, run)
        run.step("download", "done")

        run.step("verify")
        with tarfile.open(archive, "r:gz") as tf:
            _safe_members(tf)
        run.step("verify", "done", "Archive checked: one NCM folder, no links, no paths outside it")

        run.step("unpack")
        new_root = _unpack(archive, staging)
        staged = _staged_version(new_root, release["version"])
        if parse_version(staged) and not is_newer(staged):
            raise UpdateError(f"The archive contains version {staged}, which is not newer than {__version__}")
        run.step("unpack", "done", f"Unpacked version {staged}")

        run.step("packages")
        cur_req = (BASE_DIR / "requirements.txt").read_text()
        new_req = (new_root / "backend/requirements.txt").read_text()
        if cur_req.strip() != new_req.strip():
            req_changed = True
            _pip(new_root / "backend/requirements.txt", run)
            run.step("packages", "done", "Python packages updated")
        else:
            run.step("packages", "skipped", "Python packages are unchanged")

        run.step("test")
        _smoke_test(new_root, run)
        run.step("test", "done")

        run.step("snapshot")
        snap = _snapshot(run)
        run.state["snapshot"] = snap.name
        run.step("snapshot", "done")

        run.step("install")
        try:
            _install_tree(new_root, run)
        except Exception as exc:
            run.log(f"Installing failed ({exc}); putting the previous version back")
            _install_tree(snap / "code", run)
            raise UpdateError(f"Installing the new files failed, the previous version was restored: {exc}")
        run.step("install", "done", f"Version {staged} installed")

        run.state["to"] = staged
        run.step("restart")
        restarting = _restart(run)
        run.finish("restarting" if restarting else "restart_needed")
    except Exception as exc:
        if req_changed:
            try:
                _pip(BASE_DIR / "requirements.txt", run)
                run.log("Python packages put back to the running version's list")
            except Exception as exc2:      # noqa: BLE001
                run.log(f"Could not put the Python packages back: {exc2}")
        run.finish("failed", str(exc) if isinstance(exc, UpdateError) else f"{type(exc).__name__}: {exc}")
        if not isinstance(exc, UpdateError):
            logger.exception("Upgrade failed")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            (WORK / "download.tar.gz").unlink()
        except OSError:
            pass
        _release_lock()


# ───────────────────────── rollback ─────────────────────────

def snapshots_paths() -> list[Path]:
    return [p for p in ROLLBACK_DIR.iterdir() if (p / "snapshot.json").exists()] if ROLLBACK_DIR.is_dir() else []


def snapshots() -> list[dict]:
    out = []
    for p in snapshots_paths():
        try:
            meta = json.loads((p / "snapshot.json").read_text())
        except (OSError, ValueError):
            continue
        out.append({"id": p.name, "version": meta.get("version"), "created_at": meta.get("created_at"),
                    "has_database": (p / "database.sqlite").exists()})
    return sorted(out, key=lambda x: x["created_at"] or "", reverse=True)


def _snapshot_path(snap_id: str) -> Path:
    if not re.fullmatch(r"[\w.+-]+", snap_id or ""):
        raise UpdateError("Unknown snapshot")
    p = ROLLBACK_DIR / snap_id
    if not (p / "snapshot.json").exists():
        raise UpdateError("Unknown snapshot")
    return p


def start_rollback(snap_id: str, restore_database: bool, username: str) -> dict:
    snap = _snapshot_path(snap_id)
    meta = json.loads((snap / "snapshot.json").read_text())
    _take_lock()
    try:
        run = _Run("rollback", meta.get("version") or "?", username)
    except Exception:
        _release_lock()
        raise
    threading.Thread(target=_do_rollback, args=(snap, restore_database, run), name="ncm-rollback",
                     daemon=True).start()
    return run.state


def _do_rollback(snap: Path, restore_database: bool, run: _Run):
    try:
        run.step("snapshot")
        cur = _snapshot(run)            # so the rollback itself can be undone
        run.state["snapshot"] = cur.name
        run.step("snapshot", "done")
        run.step("install")
        _restore(snap, restore_database, run)
        run.step("install", "done")
        run.step("restart")
        run.finish("restarting" if _restart(run) else "restart_needed")
    except Exception as exc:
        run.finish("failed", f"{type(exc).__name__}: {exc}")
        logger.exception("Rollback failed")
    finally:
        _release_lock()


def _restore(snap: Path, restore_database: bool, run: _Run | None = None):
    say = run.log if run else print
    _install_tree(snap / "code", run or _Quiet())
    say(f"Code put back from {snap.name}")
    req = snap / "code/backend/requirements.txt"
    if req.exists():
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r",
                            str(req)], capture_output=True, text=True, timeout=1200)
        say("Python packages put back" if r.returncode == 0 else "pip reported a problem: " + r.stderr[-400:])
    if restore_database:
        meta = json.loads((snap / "snapshot.json").read_text())
        if meta.get("database") and (snap / "database.sqlite").exists():
            _sqlite_copy(snap / "database.sqlite", Path(meta["database"]))
            say("Database put back to the copy taken before the upgrade")
        else:
            say("This snapshot has no database copy; the database was left as it is")


class _Quiet:
    def log(self, text):
        print(text)


# ───────────────────────── startup / command line ─────────────────────────

def on_startup():
    """After a restart: mark the upgrade done (or failed) and write it to the audit log."""
    st = read_state()
    if not st or st.get("status") not in ("restarting", "restart_needed"):
        return
    ok = st.get("kind") == "rollback" or not parse_version(st.get("to", "")) or st.get("to") == __version__
    st["status"] = "done" if ok else "failed"
    st["finished_at"] = datetime.now(timezone.utc).isoformat()
    st["running"] = __version__
    if not ok:
        st["error"] = f"After the restart NCM still runs {__version__}, not {st.get('to')}"
    for s in st.get("steps", []):
        if s["status"] in ("running", "pending"):
            s["status"] = "done" if ok else "failed"
    st.setdefault("log", []).append(f"{datetime.now().strftime('%H:%M:%S')}  NCM {__version__} started")
    try:
        _write_state(st)
        from app.core.database import SessionLocal
        from app.services.audit import record_audit
        db = SessionLocal()
        try:
            record_audit(db, None, "SYSTEM_UPGRADE" if st.get("kind") == "upgrade" else "SYSTEM_ROLLBACK",
                         "system", __version__, "SUCCESS" if ok else "FAILED",
                         f"{st.get('from')} -> {__version__} by {st.get('by')}")
        finally:
            db.close()
    except Exception:
        logger.exception("Could not record the finished upgrade")


def _cli(argv: list[str]) -> int:
    """python -m app.services.updater [status|rollback [snapshot] [--with-database]]"""
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(f"Running code: NCM {__version__}")
        st = read_state()
        if st:
            print(f"Last {st['kind']}: {st['from']} -> {st['to']}: {st['status']}" + (f" ({st['error']})" if st.get("error") else ""))
        for s in snapshots():
            print(f"  snapshot {s['id']}  version {s['version']}  database copy: {'yes' if s['has_database'] else 'no'}")
        return 0
    if cmd == "rollback":
        snaps = snapshots()
        if not snaps:
            print("No snapshots to roll back to.")
            return 1
        sid = next((a for a in argv[1:] if not a.startswith("--")), snaps[0]["id"])
        print(f"Rolling back to snapshot {sid} …")
        _restore(_snapshot_path(sid), "--with-database" in argv)
        print("Done. Restart NCM now (for example: sudo systemctl restart ncm).")
        return 0
    print(_cli.__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
