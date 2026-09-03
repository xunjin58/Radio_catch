"""SQLite database setup shared by the API routers and worker processes."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .project_paths import database_url, project_paths


DEFAULT_DATABASE_PATH = project_paths().database_path
DATABASE_URL = database_url()

if DATABASE_URL.startswith("sqlite:///"):
    # Do not require a manual bootstrap step for the local-first default.
    DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

engine_kwargs: dict[str, object] = {"future": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    # A single connection is required for a process-local sqlite:// test database.
    if DATABASE_URL == "sqlite://":
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a transaction-scoped session."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create the local schema. Call once during app startup."""
    # Importing here keeps model registration explicit and avoids circular imports.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_schema()
    _rebase_project_runtime_paths()


def _migrate_sqlite_schema() -> None:
    """Apply small, additive SQLite migrations for existing local databases."""
    if engine.dialect.name != "sqlite":
        return
    tables = set(inspect(engine).get_table_names())
    if "model_configs" in tables:
        columns = {column["name"] for column in inspect(engine).get_columns("model_configs")}
        if "max_native_media_bytes" not in columns:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE model_configs ADD COLUMN max_native_media_bytes "
                    "INTEGER NOT NULL DEFAULT 104857600"
                )
    if "renders" in tables:
        columns = {column["name"] for column in inspect(engine).get_columns("renders")}
        with engine.begin() as connection:
            if "delivery_output_path" not in columns:
                connection.exec_driver_sql("ALTER TABLE renders ADD COLUMN delivery_output_path TEXT")
            if "delivery_manifest" not in columns:
                connection.exec_driver_sql("ALTER TABLE renders ADD COLUMN delivery_manifest JSON")
            if "delivered_at" not in columns:
                connection.exec_driver_sql("ALTER TABLE renders ADD COLUMN delivered_at DATETIME")


def _rebased_file_path(value: str, *, media_root: Path, export_root: Path) -> str:
    """Rebase a copied project's old machine-specific absolute path when safe.

    Path strings in historical SQLite records are absolute.  When a complete
    project folder is copied to another OS, look only for recognised runtime
    directory boundaries and update a value only if the matching local file
    really exists.  This never turns the database into an arbitrary file-path
    resolver.
    """
    normalized = value.replace("\\", "/")
    for marker, target_root in (("/media/", media_root), ("/storage/", media_root), ("/exports/", export_root)):
        if marker not in normalized:
            continue
        relative_parts = [part for part in normalized.rsplit(marker, 1)[1].split("/") if part]
        candidate = target_root.joinpath(*relative_parts)
        if candidate.is_file():
            return str(candidate)
    return value


def _rebase_json_paths(value: object, *, media_root: Path, export_root: Path) -> object:
    if isinstance(value, str):
        return _rebased_file_path(value, media_root=media_root, export_root=export_root)
    if isinstance(value, list):
        return [_rebase_json_paths(item, media_root=media_root, export_root=export_root) for item in value]
    if isinstance(value, dict):
        return {
            key: _rebase_json_paths(item, media_root=media_root, export_root=export_root)
            for key, item in value.items()
        }
    return value


def _rebase_project_runtime_paths() -> None:
    """Make a copied ``RADIO_CATCH_PROJECT_DIR`` usable on the current machine."""
    paths = project_paths()
    if not paths.uses_project_root:
        return

    from .models import Clip, ClipAnalysis, Render

    changed = False
    with SessionLocal() as session:
        for clip in session.scalars(select(Clip)).all():
            rebased = _rebased_file_path(clip.file_path, media_root=paths.media_root, export_root=paths.export_root)
            if rebased != clip.file_path:
                clip.file_path = rebased
                changed = True
        for analysis in session.scalars(select(ClipAnalysis)).all():
            tags = _rebase_json_paths(analysis.tags, media_root=paths.media_root, export_root=paths.export_root)
            frames = _rebase_json_paths(analysis.evidence_frames, media_root=paths.media_root, export_root=paths.export_root)
            if tags != analysis.tags:
                analysis.tags = tags
                changed = True
            if frames != analysis.evidence_frames:
                analysis.evidence_frames = frames
                changed = True
        for render in session.scalars(select(Render)).all():
            for attribute in ("output_path", "delivery_output_path"):
                value = getattr(render, attribute)
                if not value:
                    continue
                rebased = _rebased_file_path(value, media_root=paths.media_root, export_root=paths.export_root)
                if rebased != value:
                    setattr(render, attribute, rebased)
                    changed = True
            edl = _rebase_json_paths(render.edit_decision_list, media_root=paths.media_root, export_root=paths.export_root)
            manifest = _rebase_json_paths(render.delivery_manifest, media_root=paths.media_root, export_root=paths.export_root)
            if edl != render.edit_decision_list:
                render.edit_decision_list = edl
                changed = True
            if manifest != render.delivery_manifest:
                render.delivery_manifest = manifest
                changed = True
        if changed:
            session.commit()
