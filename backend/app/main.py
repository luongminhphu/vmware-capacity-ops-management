import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .collectors.scheduler import scheduler_loop
from .config import get_settings
from .database import Base, SessionLocal, engine
from .routers import auth, compliance, dashboard, data, export, imports, settings as settings_router, vcenters
from .security import ensure_bootstrap_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vco.main")

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"

_scheduler_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Base.metadata.create_all(bind=engine)

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
app.include_router(settings_router.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "VMware Capacity Ops Management"}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
