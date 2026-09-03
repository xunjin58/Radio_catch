"""Regression tests for the portable single-folder project layout."""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import _rebased_file_path
from app.project_paths import project_paths


def _migration_module():
    script = Path(__file__).resolve().parent / "scripts" / "migrate_to_project_folder.py"
    spec = importlib.util.spec_from_file_location("project_folder_migration", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectPathsTests(unittest.TestCase):
    def test_project_root_contains_database_media_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"RADIO_CATCH_PROJECT_DIR": directory},
            clear=False,
        ):
            for name in ("RADIO_CATCH_DATABASE_URL", "RADIO_CATCH_STORAGE_DIR", "RADIO_CATCH_EXPORT_DIR"):
                os.environ.pop(name, None)
            paths = project_paths()
            root = Path(directory).resolve()
            self.assertEqual(paths.root, root)
            self.assertEqual(paths.database_path, root / "radio_catch.db")
            self.assertEqual(paths.media_root, root / "media")
            self.assertEqual(paths.export_root, root / "exports")

    def test_copied_machine_path_rebases_only_when_project_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            exports = root / "exports"
            target = media / "clips" / "ab" / "clip.mp4"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"test")
            self.assertEqual(
                _rebased_file_path(
                    r"C:\\OldComputer\\lemon-project\\media\\clips\\ab\\clip.mp4",
                    media_root=media,
                    export_root=exports,
                ),
                str(target),
            )
            missing = "/Users/old/lemon-project/media/clips/ab/missing.mp4"
            self.assertEqual(_rebased_file_path(missing, media_root=media, export_root=exports), missing)


class ProjectFolderMigrationTests(unittest.TestCase):
    def test_migration_rewrites_media_tag_and_render_paths(self) -> None:
        migration = _migration_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_media, old_exports = root / "old-media", root / "old-exports"
            new_media, new_exports = root / "project" / "media", root / "project" / "exports"
            old_clip = old_media / "clips" / "ab" / "clip.mp4"
            old_frame = old_media / "derived" / "clip" / "frame.png"
            old_render = old_exports / "RC-test.mp4"
            for path in (old_clip, old_frame, old_render):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")
            new_media.mkdir(parents=True)
            new_exports.mkdir(parents=True)
            database = root / "project" / "radio_catch.db"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE clips (id TEXT PRIMARY KEY, file_path TEXT NOT NULL);
                    CREATE TABLE clip_analyses (id TEXT PRIMARY KEY, tags TEXT, evidence_frames TEXT);
                    CREATE TABLE renders (id TEXT PRIMARY KEY, output_path TEXT, delivery_output_path TEXT,
                                          edit_decision_list TEXT, delivery_manifest TEXT);
                    """
                )
                connection.execute("INSERT INTO clips VALUES (?, ?)", ("clip", str(old_clip)))
                connection.execute(
                    "INSERT INTO clip_analyses VALUES (?, ?, ?)",
                    ("analysis", json.dumps({"thumbnail_path": str(old_frame)}), json.dumps([{"path": str(old_frame)}])),
                )
                connection.execute(
                    "INSERT INTO renders VALUES (?, ?, ?, ?, ?)",
                    ("render", str(old_render), str(old_render), json.dumps([{"source_path": str(old_clip)}]), json.dumps({"output": str(old_render)})),
                )

            migration.migrate_database(database, old_media, new_media, old_exports, new_exports)

            with sqlite3.connect(database) as connection:
                clip_path = connection.execute("SELECT file_path FROM clips").fetchone()[0]
                tags, evidence = connection.execute("SELECT tags, evidence_frames FROM clip_analyses").fetchone()
                output, delivery, edl, manifest = connection.execute(
                    "SELECT output_path, delivery_output_path, edit_decision_list, delivery_manifest FROM renders"
                ).fetchone()
            self.assertEqual(clip_path, str(new_media / "clips" / "ab" / "clip.mp4"))
            self.assertEqual(json.loads(tags)["thumbnail_path"], str(new_media / "derived" / "clip" / "frame.png"))
            self.assertEqual(json.loads(evidence)[0]["path"], str(new_media / "derived" / "clip" / "frame.png"))
            self.assertEqual(output, str(new_exports / "RC-test.mp4"))
            self.assertEqual(delivery, output)
            self.assertEqual(json.loads(edl)[0]["source_path"], clip_path)
            self.assertEqual(json.loads(manifest)["output"], output)


if __name__ == "__main__":
    unittest.main()
