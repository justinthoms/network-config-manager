# Copyright (c) 2026 Justin Thomas. Licensed under the MIT License (see LICENSE).
# Network Config Manager™ and NCM™ are trademarks of Justin Thomas.

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.database import init_db, startup_lock
from app.api.auth import router as auth_router
from app.api.devices import router as devices_router
from app.api.backups import router as backups_router
from app.api.schedules import router as schedules_router
from app.api.users import router as users_router
from app.api.audit import router as audit_router
from app.api.files import router as files_router
from app.api.cloud import router as cloud_router
from app.api.onedrive import router as onedrive_router
from app.api.dashboard import router as dashboard_router
from app.api.ai import router as ai_router
from app.api.health import router as health_router
from app.api.security import router as security_router
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.maintenance import start_background_tasks

from app.core.config import settings as _settings

@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.services.hardening import restrict_permissions, startup_warnings
    from app.services.ai_providers import ensure_env_provider
    restrict_permissions()
    startup_warnings()
    with startup_lock():         # with several worker processes, one sets up at a time
        init_db()
        start_scheduler()
        ensure_env_provider()
    start_background_tasks()
    yield
    stop_scheduler()


app = FastAPI(
    title="Network Config Manager", version="2.0.0", lifespan=lifespan,
    docs_url="/docs" if _settings.enable_api_docs else None,
    redoc_url="/redoc" if _settings.enable_api_docs else None,
    openapi_url="/openapi.json" if _settings.enable_api_docs else None,
)

# Content Security Policy: only this server, plus Google Fonts for the UI typefaces.
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'")


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "no-referrer")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        h.setdefault("Cache-Control", "no-store")
    else:
        h.setdefault("Content-Security-Policy", CSP)
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip()
    if proto == "https":
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"

app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(devices_router, prefix="/api/devices", tags=["Devices"])
app.include_router(backups_router, prefix="/api/backups", tags=["Backups"])
app.include_router(schedules_router, prefix="/api/schedules", tags=["Schedules"])
app.include_router(users_router, prefix="/api/users", tags=["Users"])
app.include_router(audit_router, prefix="/api/audit", tags=["Audit"])
app.include_router(files_router, prefix="/api/files", tags=["File Browser"])
app.include_router(cloud_router, prefix="/api/cloud", tags=["Cloud"])
app.include_router(onedrive_router, prefix="/api/cloud/onedrive", tags=["Cloud"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(ai_router, prefix="/api/ai", tags=["AI review"])
app.include_router(health_router, prefix="/api/health", tags=["Health"])
app.include_router(security_router, prefix="/api/security", tags=["Security"])

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
