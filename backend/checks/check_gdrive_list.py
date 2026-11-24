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
    "sub_bhcore": {"name": "BH-CORE", "parents": ["root_old"]},
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


# ───────────── move into current folder ─────────────

def test_move_all_to_current_folder(db, monkeypatch):
    store.set(db, gdrive.K_ROOT_NAME, "NCM-TSR-DIS")
    store.set(db, gdrive.K_FOLDERS, None)
    created = {"root_new": "NEW-ROOT"}         # pretend the new root already resolves
    moves = []

    def req(db, method, url, **kw):
        params = kw.get("params", {})
        if method == "GET" and url.endswith("/files"):
            # two files in the old NCM-Backups/<host> folders
            return _Resp({"files": [
                {"id": "f1", "name": "KODAKARA_1.conf", "parents": ["sub_kodakara"]},
                {"id": "f2", "name": "BH-CORE_1.conf", "parents": ["sub_bhcore"]},
            ]})
        if method == "GET":                     # folder meta lookups
            fid = url.rsplit("/", 1)[-1]
            return _Resp({"id": fid, "name": FOLDERS[fid]["name"], "parents": FOLDERS[fid]["parents"]})
        if method == "POST":                    # _find_or_create_folder: pretend none exist, create
            name = (kw.get("json") or {}).get("name", "x")
            return _Resp({"id": "new_" + name})
        if method == "PATCH":                   # the move
            moves.append((url.rsplit("/", 1)[-1], params.get("addParents"), params.get("removeParents")))
            return _Resp({"id": url.rsplit("/", 1)[-1]})
        return _Resp({})

    # folder lookup should find nothing first (so it creates) — make GET for folders-by-query return empty
    def req2(db, method, url, **kw):
        params = kw.get("params", {})
        if method == "GET" and url.endswith("/files") and "q" in params and "mimeType='application/vnd.google-apps.folder'" in params["q"]:
            return _Resp({"files": []})         # _find_or_create_folder search → create
        return req(db, method, url, **kw)

    monkeypatch.setattr(gdrive, "_request", req2)
    result = gdrive.move_all_to_current(db)
    assert result["moved"] == 2 and result["failed"] == 0
    # both files were moved off their old parent
    assert {m[0] for m in moves} == {"f1", "f2"}
    assert all(m[2] in ("sub_kodakara", "sub_bhcore") for m in moves)   # removed from old parent
