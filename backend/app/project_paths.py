"""Resolve the local filesystem layout for one Radio Catch project.

``RADIO_CATCH_PROJECT_DIR`` is the portable, single-folder layout.  The older
per-directory variables remain supported for existing installations and take
precedence when explicitly set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ProjectPaths:
    """All runtime assets that belong to one locally managed project."""

    root: Path | None
    database_path: Path
    media_root: Path
    export_root: Path

    @property
    def uses_project_root(self) -> bool:
        return self.root is not None


def _configured_path(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else None


def project_paths() -> ProjectPaths:
    """Return paths without creating files or directories.

    In a unified project folder, labels live in ``radio_catch.db`` beside
    ``media/`` and ``exports/``.  Explicit legacy overrides are intentionally
    honoured so an existing deployment is never silently redirected.
    """
    root = _configured_path("RADIO_CATCH_PROJECT_DIR")
    database_path = root / "radio_catch.db" if root else BACKEND_DIR / "data" / "radio_catch.db"
    media_root = root / "media" if root else BACKEND_DIR / "storage"
    export_root = root / "exports" if root else BACKEND_DIR / "data" / "exports"

    database_override = os.getenv("RADIO_CATCH_DATABASE_URL")
    if database_override and database_override.startswith("sqlite:///"):
        database_path = Path(database_override.removeprefix("sqlite:///"))
    media_root = _configured_path("RADIO_CATCH_STORAGE_DIR") or media_root
    export_root = _configured_path("RADIO_CATCH_EXPORT_DIR") or export_root

    return ProjectPaths(
        root=root,
        database_path=database_path.resolve(),
        media_root=media_root.resolve(),
        export_root=export_root.resolve(),
    )


def database_url() -> str:
    """Return the configured SQLAlchemy SQLite URL when no external URL is set."""
    configured = os.getenv("RADIO_CATCH_DATABASE_URL")
    return configured or f"sqlite:///{project_paths().database_path}"
