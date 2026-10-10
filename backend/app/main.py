from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.database import init_db
from app.api.auth import router as auth_router
from app.api.devices import router as devices_router
from app.api.backups import router as backups_router
from app.api.schedules import router as schedules_router
from app.api.users import router as users_router
from app.api.audit import router as audit_router
from app.api.files import router as files_router
from app.api.cloud import router as cloud_router
from app.api.dashboard import router as dashboard_router
from app.api.ai import router as ai_router
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.maintenance import start_background_tasks

app = FastAPI(title="Network Config Manager", version="0.6.0")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

@app.on_event("startup")
def startup():
    init_db()
    start_scheduler()
    start_background_tasks()
    from app.services.ai_providers import ensure_env_provider
    ensure_env_provider()

@app.on_event("shutdown")
def shutdown():
    stop_scheduler()

app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(devices_router, prefix="/api/devices", tags=["Devices"])
app.include_router(backups_router, prefix="/api/backups", tags=["Backups"])
app.include_router(schedules_router, prefix="/api/schedules", tags=["Schedules"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(audit_router, prefix="/api/audit", tags=["Audit"])
app.include_router(files_router, prefix="/api/files", tags=["File Browser"])
app.include_router(cloud_router, prefix="/api/cloud", tags=["Cloud"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(ai_router, prefix="/api/ai", tags=["AI review"])

LOGO_DIR = WEB_DIR / "vendors"
LOGO_EXT = (".svg", ".png", ".webp", ".jpg", ".jpeg")


@app.get("/api/meta/vendor-logos", tags=["Meta"])
def vendor_logos():
    """Optional custom vendor logos placed in app/web/vendors/<vendor>.svg|png|webp."""
    out = {}
    if LOGO_DIR.is_dir():
        for p in sorted(LOGO_DIR.iterdir()):
            if p.suffix.lower() in LOGO_EXT and p.stem.lower() not in out:
                out[p.stem.lower()] = f"/static/vendors/{p.name}"
    return out


@app.get("/", response_class=HTMLResponse)
def home():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
