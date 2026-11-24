# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""The Google Drive file list shows each file's real folder, and flags files not in the current folder."""
from app.services import gdrive
from app.services import settings_store as store


class _Resp:
    def __init__(self, data):
        self.status_code = 200
        self._data = data

    def json(self):
        return self._data


# A tiny fake Drive: two root folders, each with one per-device sub-folder holding one file.
FOLDERS = {
    "root_old": {"name": "NCM-Backups", "parents": []},
    "sub_kodakara": {"name": "KODAKARA", "parents": ["root_old"]},
    "root_new": {"name": "NCM-TSR-DIS", "parents": []},
    "sub_sw1": {"name": "sw1", "parents": ["root_new"]},
}
FILES = [
    {"id": "f1", "name": "KODAKARA_20261124.conf", "size": "8900", "createdTime": "2026-11-24T04:35:00Z",
     "webViewLink": "https://drive/f1", "parents": ["sub_kodakara"]},
    {"id": "f2", "name": "sw1_20261124.conf", "size": "27000", "createdTime": "2026-11-24T05:00:00Z",
     "webViewLink": "https://drive/f2", "parents": ["sub_sw1"]},
]


def _fake_request(db, method, url, **kw):
    if url.endswith("/files"):                       # the list query
        return _Resp({"files": FILES})
    fid = url.rsplit("/", 1)[-1]                      # a files/<id> lookup
    meta = FOLDERS.get(fid)
    return _Resp({"id": fid, "name": meta["name"], "parents": meta["parents"]} if meta else {})


def test_list_resolves_folder_path_and_current_flag(db, monkeypatch):
    store.set(db, gdrive.K_ROOT_NAME, "NCM-TSR-DIS")
    monkeypatch.setattr(gdrive, "_request", _fake_request)
    files = gdrive.list_files(db)
    by_id = {f["id"]: f for f in files}
    # real folder path is shown, never "—"
    assert by_id["f1"]["folder"] == "NCM-Backups/KODAKARA"
    assert by_id["f2"]["folder"] == "NCM-TSR-DIS/sw1"
    # the old-folder file is flagged as not in the current folder; the new one is
    assert by_id["f1"]["in_current_folder"] is False
    assert by_id["f2"]["in_current_folder"] is True


def test_unreadable_parent_falls_back_to_dash(db, monkeypatch):
    store.set(db, gdrive.K_ROOT_NAME, "NCM-TSR-DIS")

    def req(db, method, url, **kw):
        if url.endswith("/files"):
            return _Resp({"files": [{"id": "x", "name": "a.conf", "parents": ["gone"]}]})
        r = _Resp({})
        r.status_code = 404
        return r
    monkeypatch.setattr(gdrive, "_request", req)
    files = gdrive.list_files(db)
    assert files[0]["folder"] == "—" and files[0]["in_current_folder"] is False
