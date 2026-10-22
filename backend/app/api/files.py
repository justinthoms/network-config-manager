# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

"""Web file browser for the backup directory (read-only, sandboxed to BACKUP_ROOT)."""
import io
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse

from app.api.deps import require_permission
from app.core.config import settings
from app.core.database import get_db
from app.services.audit import record_audit
from app.services.diff import compare

router = APIRouter()
MAX_VIEW_BYTES = 5 * 1024 * 1024
MAX_ZIP_BYTES = 500 * 1024 * 1024


def _root() -> Path:
    root = Path(settings.backup_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve(rel: str | None) -> Path:
    root = _root()
    rel = (rel or "").strip().lstrip("/\\")
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(400, "Path is outside the backup directory")
    if not path.exists():
        raise HTTPException(404, "Path not found")
    return path


def _rel(path: Path) -> str:
    rel = path.relative_to(_root()).as_posix()
    return "" if rel == "." else rel


def _entry(p: Path) -> dict:
    st = p.stat()
    item = {
        "name": p.name,
        "path": _rel(p),
        "type": "dir" if p.is_dir() else "file",
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
    }
    if p.is_dir():
        try:
            item["items"] = sum(1 for _ in p.iterdir())
        except OSError:
            item["items"] = None
    else:
        item["size"] = st.st_size
    return item


@router.get("/browse")
def browse(path: str = "", user=Depends(require_permission("files.view"))):
    target = _resolve(path)
    if not target.is_dir():
        raise HTTPException(400, "Not a directory")
    entries = [_entry(p) for p in target.iterdir() if not p.name.startswith(".")]
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    crumbs, acc = [{"name": "backups", "path": ""}], []
    for part in Path(_rel(target)).parts if _rel(target) else []:
        acc.append(part)
        crumbs.append({"name": part, "path": "/".join(acc)})
    return {"path": _rel(target), "breadcrumbs": crumbs, "entries": entries}


@router.get("/search")
def search(q: str, limit: int = 200, user=Depends(require_permission("files.view"))):
    q = q.strip().lower()
    if len(q) < 2:
        raise HTTPException(400, "Type at least 2 characters")
    out = []
    for p in _root().rglob("*"):
        if p.is_file() and q in _rel(p).lower():
            out.append(_entry(p))
            if len(out) >= limit:
                break
    out.sort(key=lambda e: e["modified"], reverse=True)
    return out


@router.get("/stats")
def stats(user=Depends(require_permission("files.view"))):
    files = size = 0
    for p in _root().rglob("*"):
        if p.is_file():
            files += 1
            size += p.stat().st_size
    return {"files": files, "bytes": size, "root": str(_root())}


@router.get("/view", response_class=PlainTextResponse)
def view(path: str, user=Depends(require_permission("files.view"))):
    target = _resolve(path)
    if not target.is_file():
        raise HTTPException(400, "Not a file")
    if target.stat().st_size > MAX_VIEW_BYTES:
        raise HTTPException(413, "File is too large to preview. Download it instead.")
    return target.read_text(encoding="utf-8", errors="replace")


@router.get("/download")
def download(path: str, db=Depends(get_db), user=Depends(require_permission("backups.download"))):
    target = _resolve(path)
    if target.is_file():
        return FileResponse(str(target), filename=target.name, media_type="application/octet-stream")

    # Folder → zip
    files = [p for p in target.rglob("*") if p.is_file()]
    if sum(p.stat().st_size for p in files) > MAX_ZIP_BYTES:
        raise HTTPException(413, "Folder is larger than 500 MB. Download a smaller folder.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(target).as_posix())
    buf.seek(0)
    name = (target.name if _rel(target) else "backups") + ".zip"
    record_audit(db, user, "FILES_DOWNLOAD_ZIP", "files", _rel(target) or "/", details=f"{len(files)} files")
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/diff")
def diff_files(a: str, b: str, ignore_whitespace: bool = False, ignore_volatile: bool = True,
               user=Depends(require_permission("backups.diff"))):
    pa, pb = _resolve(a), _resolve(b)
    if not (pa.is_file() and pb.is_file()):
        raise HTTPException(400, "Select two files")
    for p in (pa, pb):
        if p.stat().st_size > MAX_VIEW_BYTES:
            raise HTTPException(413, f"{p.name} is too large to compare")
    result = compare(pa.read_text(encoding="utf-8", errors="replace"),
                     pb.read_text(encoding="utf-8", errors="replace"),
                     ignore_whitespace=ignore_whitespace, ignore_volatile=ignore_volatile,
                     label_a=_rel(pa), label_b=_rel(pb))
    result.update({"file_a": _rel(pa), "file_b": _rel(pb)})
    return result
