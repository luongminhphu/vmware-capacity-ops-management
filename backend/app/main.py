import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import hashlib

from .collectors.scheduler import scheduler_loop
from .config import get_settings
from .database import SessionLocal, ensure_schema
from .routers import auth, compliance, dashboard, data, export, imports, reports, settings as settings_router, vcenters
from .security import ensure_bootstrap_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vco.main")

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

_scheduler_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    ensure_schema()

    db = SessionLocal()
    try:
        ensure_bootstrap_admin(db)
    finally:
        db.close()

    global _scheduler_task
    if settings.enable_scheduler:
        _scheduler_task = asyncio.create_task(scheduler_loop(settings))
        logger.info("Background vCenter poll scheduler started (interval=%s min)", settings.poll_interval_minutes)
    else:
        logger.info("Background scheduler disabled (ENABLE_SCHEDULER=false)")

    yield

    if _scheduler_task:
        _scheduler_task.cancel()


app = FastAPI(title="VMware Capacity Ops Management API", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(vcenters.router)
app.include_router(dashboard.router)
app.include_router(data.router)
app.include_router(compliance.router)
app.include_router(imports.router)
app.include_router(export.router)
app.include_router(reports.router)
app.include_router(settings_router.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "VMware Capacity Ops Management"}


def _asset_version() -> str:
    """Short fingerprint of the frontend JS/CSS. index.html references assets
    as `js/core.js?v=<fingerprint>` so browsers fetch new files after every
    deploy instead of reusing a heuristically cached copy."""
    h = hashlib.sha1()
    for f in sorted(list(FRONTEND_DIR.glob("js/**/*.js")) + list(FRONTEND_DIR.glob("css/*.css"))):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


@app.middleware("http")
async def _static_no_cache(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        # Always revalidate static files (ETag keeps it cheap); versioned
        # query strings make the actual cache-busting deterministic.
        response.headers["Cache-Control"] = "no-cache"
    return response


if FRONTEND_DIR.exists():
    _INDEX_FILE = FRONTEND_DIR / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index_html():
        html = _INDEX_FILE.read_text(encoding="utf-8").replace("__ASSET_V__", _asset_version())
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
