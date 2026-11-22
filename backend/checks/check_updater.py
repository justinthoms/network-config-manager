# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""In-app upgrades: version order, archive safety, a full upgrade and rollback on a throw-away install."""
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from app.services import updater
from app.version import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]


# ───────────── versions ─────────────

@pytest.mark.parametrize("a,b", [("2.1.0", "2.0.9"), ("v2.10.0", "2.9.0"), ("3.0.0", "2.99.99"),
                                 ("2.1.0", "2.1.0-beta1"), ("2.1.0-rc2", "2.1.0-rc1")])
def test_version_order(a, b):
    assert updater.is_newer(a, b) and not updater.is_newer(b, a)


def test_not_a_version():
    assert updater.parse_version("latest") is None
    assert not updater.is_newer("nightly", "1.0.0")


# ───────────── archive checks ─────────────

def _tar(entries: dict, links: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tf.addfile(info)
    return buf.getvalue()


GOOD = {"ncm-abc/backend/app/main.py": b"", "ncm-abc/backend/requirements.txt": b"",
        "ncm-abc/backend/migrations/env.py": b""}


def _check(data: bytes):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        return updater._safe_members(tf)


def test_good_archive_passes():
    top, _ = _check(_tar(GOOD))
    assert top == "ncm-abc"


@pytest.mark.parametrize("bad", [
    {**GOOD, "ncm-abc/../evil.py": b"x"},
    {**GOOD, "/etc/evil": b"x"},
    {**GOOD, "other-top/x": b"x"},
    {"ncm-abc/backend/app/main.py": b""},              # not NCM
])
def test_bad_archives_are_refused(bad):
    with pytest.raises(updater.UpdateError):
        _check(_tar(bad))


def test_links_are_refused():
    with pytest.raises(updater.UpdateError):
        _check(_tar(GOOD, links={"ncm-abc/backend/app/x": "/etc/passwd"}))


def test_download_only_from_github(monkeypatch):
    assert updater._allowed_host("https://codeload.github.com/a/b/tar.gz/v1")
    assert updater._allowed_host("https://api.github.com/repos/a/b/tarball/v1")
    assert not updater._allowed_host("https://evil.example.com/x.tar.gz")


# ───────────── full upgrade and rollback on a copy ─────────────

@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """A copy of this NCM in tmp_path/install, and a release archive of version 99.0.0."""
    install = tmp_path / "install"
    ignore = shutil.ignore_patterns("__pycache__", ".upgrade", "*.db", ".secret_key", "backups", ".pytest_cache")
    for rel in ["backend/app", "backend/migrations", "backend/checks"]:
        shutil.copytree(REPO_ROOT / rel, install / rel, ignore=ignore)
    for rel in ["backend/requirements.txt", "backend/alembic.ini", "backend/pyproject.toml", "README.md",
                "CHANGELOG.md", "LICENSE", "run-local.sh"]:
        shutil.copy2(REPO_ROOT / rel, install / rel)
    (install / "backend/.env").write_text("SECRET_KEY=keep-me\n")
    (install / "backend/app/web/vendors/acme.svg").write_text("<svg/>")

    # the "new release": same code, version 99.0.0, one new file, README removed
    rel_root = tmp_path / "release" / "justinthoms-network-config-manager-abc1234"
    shutil.copytree(install, rel_root, ignore=shutil.ignore_patterns(".env", "acme.svg", "README.md"))
    (rel_root / "backend/app/version.py").write_text('__version__ = "99.0.0"\n')
    (rel_root / "backend/app/NEW_FEATURE.txt").write_text("new")
    archive = tmp_path / "release.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(rel_root, arcname=rel_root.name)

    work = install / "backend/.upgrade"
    monkeypatch.setattr(updater, "ROOT", install)
    monkeypatch.setattr(updater, "BASE_DIR", install / "backend")
    monkeypatch.setattr(updater, "WORK", work)
    monkeypatch.setattr(updater, "STATE_FILE", work / "state.json")
    monkeypatch.setattr(updater, "LOCK_FILE", work / "upgrade.lock")
    monkeypatch.setattr(updater, "ROLLBACK_DIR", work / "rollback")
    monkeypatch.setattr(updater, "restart_mode", lambda: "manual")
    monkeypatch.setattr(updater, "_download", lambda url, dest, run: shutil.copy(archive, dest))
    return install


def _wait(threads_before):
    import threading
    import time
    for _ in range(600):
        if not [t for t in threading.enumerate() if t.name in ("ncm-upgrade", "ncm-rollback")]:
            return
        time.sleep(0.1)
    raise AssertionError("upgrade thread did not finish")


def test_upgrade_and_rollback(fake_install):
    release = {"version": "99.0.0", "tarball_url": "https://api.github.com/x"}
    updater.start_upgrade(release, "admin")
    _wait(None)
    st = updater.read_state()
    assert st["status"] == "restart_needed", st
    assert [s["status"] for s in st["steps"]] == ["done", "done", "done", "skipped", "done", "done", "done", "done"]
    assert '"99.0.0"' in (fake_install / "backend/app/version.py").read_text()
    assert (fake_install / "backend/app/NEW_FEATURE.txt").exists()
    assert not (fake_install / "README.md").exists()                     # removed in the release
    assert (fake_install / "backend/.env").read_text() == "SECRET_KEY=keep-me\n"   # kept
    assert (fake_install / "backend/app/web/vendors/acme.svg").exists()            # kept
    snaps = updater.snapshots()
    assert len(snaps) == 1 and snaps[0]["version"] == __version__
    assert not (fake_install / "backend/.upgrade/staging").exists()

    updater.start_rollback(snaps[0]["id"], False, "admin")
    _wait(None)
    assert updater.read_state()["status"] == "restart_needed"
    assert f'"{__version__}"' in (fake_install / "backend/app/version.py").read_text()
    assert not (fake_install / "backend/app/NEW_FEATURE.txt").exists()
    assert (fake_install / "README.md").exists()
    assert (fake_install / "backend/app/web/vendors/acme.svg").exists()
    snaps = updater.snapshots()
    assert len(snaps) == 2 and snaps[0]["version"] == "99.0.0"         # the rollback saved 99.0.0 first


def test_broken_release_changes_nothing(fake_install, tmp_path, monkeypatch):
    broken = tmp_path / "broken" / "ncm-x"
    shutil.copytree(fake_install, broken, ignore=shutil.ignore_patterns(".env", ".upgrade"))
    (broken / "backend/app/version.py").write_text('__version__ = "99.0.0"\n')
    (broken / "backend/app/main.py").write_text("raise RuntimeError('broken release')\n")
    archive = tmp_path / "broken.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(broken, arcname="ncm-x")
    monkeypatch.setattr(updater, "_download", lambda url, dest, run: shutil.copy(archive, dest))
    before = (fake_install / "backend/app/main.py").read_text()
    updater.start_upgrade({"version": "99.0.0", "tarball_url": "https://api.github.com/x"}, "admin")
    _wait(None)
    st = updater.read_state()
    assert st["status"] == "failed" and "did not start" in st["error"]
    assert (fake_install / "backend/app/main.py").read_text() == before
    assert updater.snapshots() == []


def test_older_version_is_refused(fake_install):
    with pytest.raises(updater.UpdateError):
        updater.start_upgrade({"version": "1.0.0", "tarball_url": "https://api.github.com/x"}, "admin")


# ───────────── API ─────────────

def test_who_may_see_and_upgrade(client, super_admin, make_user, monkeypatch):
    _, viewer = make_user("viewer")
    _, admin = make_user("admin")
    assert client.get("/api/system/version", headers=viewer).json() == {"version": __version__}
    assert client.get("/api/system/update", headers=viewer).status_code == 403
    r = client.get("/api/system/update", headers=admin)
    assert r.status_code == 200 and r.json()["can_upgrade"] is False
    assert client.post("/api/system/update/start", headers=admin,
                       json={"version": "99.0.0", "password": "x"}).status_code == 403
    assert client.put("/api/system/update/settings", headers=admin, json={"auto_check": False}).status_code == 403
    # Super Admin: wrong password is refused before anything else
    r = client.post("/api/system/update/start", headers=super_admin, json={"version": "99.0.0", "password": "wrong"})
    assert r.status_code == 400 and "password" in r.json()["detail"]


def test_release_must_exist_on_github(client, super_admin, monkeypatch):
    monkeypatch.setattr(updater, "fetch_releases", lambda: [])
    from ncm_fixtures import ADMIN_PASSWORD as pw
    r = client.post("/api/system/update/start", headers=super_admin, json={"version": "99.0.0", "password": pw})
    assert r.status_code == 400 and "not found" in r.json()["detail"]


def test_check_reports_update(client, super_admin, monkeypatch):
    monkeypatch.setattr(updater, "fetch_releases", lambda: [
        {"version": "99.0.0", "tag": "v99.0.0", "name": "NCM 99", "notes": "- big things", "published_at": None,
         "url": "https://github.com/x", "prerelease": False, "tarball_url": "https://api.github.com/x"}])
    r = client.post("/api/system/update/check", headers=super_admin).json()
    assert r["last_check"]["update_available"] is True and r["can_upgrade"] is True
    v = client.get("/api/system/version", headers=super_admin).json()
    assert v["latest"] == "99.0.0" and v["update_available"] is True
    json.dumps(r)
