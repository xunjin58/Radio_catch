"""Local-first API server for the Cutline short-video experiment workflow."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config_routes import router as model_config_router
from .database import SessionLocal, init_db
from .media import LocalMediaService, MediaJobService, SQLAlchemyClipRepository
from .media_routes import create_media_router
from .models import Clip
from .intelligence_routes import router as intelligence_router
from .project_routes import router as project_settings_router
from .project_paths import project_paths
from .workflow_routes import router as workflow_router


STORAGE_ROOT = project_paths().media_root


def repository() -> SQLAlchemyClipRepository:
    """Return a short-lived repository for an API operation or local worker."""
    return SQLAlchemyClipRepository(SessionLocal(), Clip)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    yield
    _app.state.media_jobs.shutdown()


app = FastAPI(
    title="Radio Catch API",
    version="0.1.0",
    description="Local-first media understanding, controlled remixing and performance learning.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# V1 is intentionally a small in-process worker pool. Job records are still
# durable in SQLite through the workflow APIs; a later queue may replace this.
_bootstrap_repository = repository()
_media_service = LocalMediaService(STORAGE_ROOT, _bootstrap_repository)
_media_jobs = MediaJobService(max_workers=2)
app.state.media_jobs = _media_jobs
app.include_router(create_media_router(_media_service, _media_jobs))
app.include_router(model_config_router)
app.include_router(project_settings_router)
app.include_router(workflow_router)
app.include_router(intelligence_router)

# Generated media remains local and is served only through this local API.
app.mount("/storage", StaticFiles(directory=STORAGE_ROOT), name="storage")


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "local", "storage": str(STORAGE_ROOT)}
