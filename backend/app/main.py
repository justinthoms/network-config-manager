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
from app.services.scheduler import start_scheduler, stop_scheduler

app = FastAPI(title="Network Config Manager", version="0.1.0")

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

@app.on_event("startup")
def startup():
    init_db()
    start_scheduler()

@app.on_event("shutdown")
def shutdown():
    stop_scheduler()

app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(devices_router, prefix="/api/devices", tags=["Devices"])
app.include_router(backups_router, prefix="/api/backups", tags=["Backups"])
app.include_router(schedules_router, prefix="/api/schedules", tags=["Schedules"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(audit_router, prefix="/api/audit", tags=["Audit"])

@app.get("/", response_class=HTMLResponse)
def home():
    return (WEB_DIR / "index.html").read_text()

app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
