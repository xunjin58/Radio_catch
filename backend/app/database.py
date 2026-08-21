"""SQLite database setup shared by the API routers and worker processes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = BACKEND_DIR / "data" / "radio_catch.db"
DATABASE_URL = os.getenv("RADIO_CATCH_DATABASE_URL", f"sqlite:///{DEFAULT_DATABASE_PATH}")

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


def _migrate_sqlite_schema() -> None:
    """Apply small, additive SQLite migrations for existing local databases."""
    if engine.dialect.name != "sqlite" or "model_configs" not in inspect(engine).get_table_names():
        return
    columns = {column["name"] for column in inspect(engine).get_columns("model_configs")}
    if "max_native_media_bytes" not in columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE model_configs ADD COLUMN max_native_media_bytes "
                "INTEGER NOT NULL DEFAULT 104857600"
            )
