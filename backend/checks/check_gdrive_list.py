# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""The Google Drive list shows only the backups under the folder configured here,
not files another NCM install may hold in the same Google account."""
from app.services import gdrive
from app.services import settings_store as store


class _Resp:
    def __init__(self, data):
        self.status_code = 200
        self._data = data

    def json(self):
        return self._data


# A shared Google account with TWO NCM installs:
#   NCM-TSR-DIS  (this install)  -> device folder "sw1" -> file g2
#   NCM-WEONE    (other install) -> device folder "KODAKARA" -> file g1
FOLDERS = {
    "root_tsr": {"name": "NCM-TSR-DIS", "children": ["sub_sw1"]},
    "sub_sw1": {"name": "sw1", "parent": "root_tsr"},
    "root_weone": {"name": "NCM-WEONE", "children": ["sub_kodakara"]},
    "sub_kodakara": {"name": "KODAKARA", "parent": "root_weone"},
}
FILES = {
    "sub_sw1": [{"id": "g2", "name": "sw1_20261124.conf", "size": "27000",
                 "createdTime": "2026-11-24T05:00:00Z", "webViewLink": "https://drive/g2", "parents": ["sub_sw1"]}],
    "sub_kodakara": [{"id": "g1", "name": "KODAKARA_20261124.conf", "size": "8900",
                      "createdTime": "2026-11-24T04:35:00Z", "webViewLink": "https://drive/g1", "parents": ["sub_kodakara"]}],
    "root_tsr": [], "root_weone": [],
}


def _fake_request(db, method, url, **kw):
    q = (kw.get("params") or {}).get("q", "")
    is_folder = "mimeType='application/vnd.google-apps.folder'" in q
    # folder-by-name lookup (used by _find_folder) — q has name='X' and 'root' in parents
    if is_folder and "name='" in q:
        name = q.split("name='", 1)[1].split("'", 1)[0]
        hit = [fid for fid, meta in FOLDERS.items() if meta["name"] == name and "parent" not in meta]
        return _Resp({"files": [{"id": hit[0]}] if hit else []})
    # sub-folders of a root — q has '<root>' in parents, no name=
    if is_folder and "in parents" in q:
        import re
        root = re.search(r"'([^']+)' in parents", q).group(1)
        return _Resp({"files": [{"id": c, "name": FOLDERS[c]["name"]} for c in FOLDERS[root]["children"]]})
    # files under the given parents
    if "mimeType!=" in q:
        import re
        parents = re.findall(r"'([^']+)' in parents", q)
        out = []
        for pid in parents:
            out += FILES.get(pid, [])
        return _Resp({"files": out})
    return _Resp({"files": []})


def test_list_shows_only_the_configured_folder(db, monkeypatch):
    store.set(db, gdrive.K_ROOT_NAME, "NCM-TSR-DIS")
    monkeypatch.setattr(gdrive, "_request", _fake_request)
    files = gdrive.list_files(db)
    ids = {f["id"] for f in files}
    assert ids == {"g2"}                                    # only this install's file
    assert "g1" not in ids                                  # the other install's file is NOT shown
    assert files[0]["folder"] == "NCM-TSR-DIS/sw1"


def test_other_install_sees_only_its_own(db, monkeypatch):
    store.set(db, gdrive.K_ROOT_NAME, "NCM-WEONE")
    monkeypatch.setattr(gdrive, "_request", _fake_request)
    files = gdrive.list_files(db)
    assert {f["id"] for f in files} == {"g1"}
    assert files[0]["folder"] == "NCM-WEONE/KODAKARA"


def test_missing_folder_lists_nothing(db, monkeypatch):
    store.set(db, gdrive.K_ROOT_NAME, "NCM-DOES-NOT-EXIST")
    monkeypatch.setattr(gdrive, "_request", _fake_request)
    assert gdrive.list_files(db) == []


def test_no_cross_install_move_function():
    # the unsafe "move everything into my folder" helper must be gone
    assert not hasattr(gdrive, "move_all_to_current")
