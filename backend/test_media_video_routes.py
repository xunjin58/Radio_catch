"""Regression tests for locally scoped source-video playback."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.media import LocalMediaService, MediaJobService, SQLAlchemyClipRepository
from app.media_routes import create_media_router
from app.models import Clip


class MediaVideoRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.tempdir.name) / "storage"
        self.storage_root.mkdir()
        self.session = Session(create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        ))
        Base.metadata.create_all(self.session.bind)
        repository = SQLAlchemyClipRepository(self.session, Clip)
        self.jobs = MediaJobService(max_workers=1)
        self.service = LocalMediaService(self.storage_root, repository)
        app = FastAPI()
        app.include_router(create_media_router(self.service, self.jobs))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.jobs.shutdown()
        self.session.close()
        self.tempdir.cleanup()

    def add_clip(self, path: Path) -> Clip:
        clip = Clip(
            original_filename=path.name,
            file_path=str(path),
            sha256=uuid4().hex + uuid4().hex,
            file_size_bytes=path.stat().st_size if path.exists() else 1,
        )
        self.session.add(clip)
        self.session.commit()
        return clip

    def test_registered_video_inside_storage_is_playable(self) -> None:
        path = self.storage_root / "clips" / "clip.mp4"
        path.parent.mkdir()
        path.write_bytes(b"local-video")
        clip = self.add_clip(path)

        response = self.client.get(f"/api/media/clips/{clip.id}/video")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"local-video")
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))

        partial = self.client.get(f"/api/media/clips/{clip.id}/video", headers={"Range": "bytes=0-4"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"local")
        self.assertEqual(partial.headers["content-range"], "bytes 0-4/11")

    def test_missing_clip_returns_404(self) -> None:
        response = self.client.get("/api/media/clips/not-a-clip/video")

        self.assertEqual(response.status_code, 404)

    def test_missing_video_file_returns_404(self) -> None:
        clip = self.add_clip(self.storage_root / "clips" / "missing.mp4")

        response = self.client.get(f"/api/media/clips/{clip.id}/video")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Video file is unavailable.")

    def test_path_outside_storage_is_rejected(self) -> None:
        outside_path = Path(self.tempdir.name) / "outside.mp4"
        outside_path.write_bytes(b"not-public")
        clip = self.add_clip(outside_path)

        response = self.client.get(f"/api/media/clips/{clip.id}/video")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Video file is unavailable.")


if __name__ == "__main__":
    unittest.main()
