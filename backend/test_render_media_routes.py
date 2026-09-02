"""Regression tests for locally scoped finished-render media."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import workflow_routes
from app.database import Base, get_session
from app.models import Render
from app.workflow import create_render_thumbnail, render_thumbnail_path


class RenderMediaRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.export_root = Path(self.tempdir.name) / "exports"
        self.export_root.mkdir()
        self.session = Session(create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        ))
        Base.metadata.create_all(self.session.bind)
        self.original_export_dir = workflow_routes.EXPORT_DIR
        workflow_routes.EXPORT_DIR = self.export_root
        app = FastAPI()
        app.include_router(workflow_routes.router)
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        workflow_routes.EXPORT_DIR = self.original_export_dir
        self.session.close()
        self.tempdir.cleanup()

    def add_render(self, path: Path | None, status: str = "completed") -> Render:
        render = Render(
            video_id=f"RC-{len(self.session.query(Render).all()) + 1}",
            dish="柠檬",
            title="封面测试",
            status=status,
            output_path=str(path) if path else None,
            duration_seconds=12,
            edit_decision_list=[],
        )
        self.session.add(render)
        self.session.commit()
        return render

    def test_completed_render_is_playable_and_supports_ranges(self) -> None:
        output = self.export_root / "RC-1.mp4"
        output.write_bytes(b"local-render")
        render = self.add_render(output)

        response = self.client.get(f"/api/renders/{render.id}/video")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"local-render")
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))

        partial = self.client.get(f"/api/renders/{render.id}/video", headers={"Range": "bytes=0-4"})
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"local")

    def test_thumbnail_is_served_and_legacy_render_is_backfilled(self) -> None:
        output = self.export_root / "RC-1.mp4"
        output.write_bytes(b"local-render")
        render = self.add_render(output)
        thumbnail = render_thumbnail_path(output)
        thumbnail.write_bytes(b"jpeg-cover")

        response = self.client.get(f"/api/renders/{render.id}/thumbnail")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"jpeg-cover")
        self.assertTrue(response.headers["content-type"].startswith("image/jpeg"))

        thumbnail.unlink()

        def generate_cover(_output: Path, _duration: float | None) -> Path:
            thumbnail.write_bytes(b"rebuilt-cover")
            return thumbnail

        with patch("app.workflow_routes.create_render_thumbnail", side_effect=generate_cover) as mocked:
            backfilled = self.client.get(f"/api/renders/{render.id}/thumbnail")
        self.assertEqual(backfilled.status_code, 200)
        self.assertEqual(backfilled.content, b"rebuilt-cover")
        mocked.assert_called_once_with(output.resolve(), 12)

    def test_unavailable_or_outside_render_media_is_rejected(self) -> None:
        queued = self.add_render(None, status="queued")
        outside = Path(self.tempdir.name) / "outside.mp4"
        outside.write_bytes(b"not-public")
        escaped = self.add_render(outside)

        self.assertEqual(self.client.get(f"/api/renders/{queued.id}/video").status_code, 409)
        self.assertEqual(self.client.get(f"/api/renders/{queued.id}/thumbnail").status_code, 409)
        self.assertEqual(self.client.get(f"/api/renders/{escaped.id}/video").status_code, 404)
        self.assertEqual(self.client.get(f"/api/renders/missing/thumbnail").status_code, 404)

    def test_agent_delivery_is_preferred_by_metadata_and_served_separately(self) -> None:
        base = self.export_root / "RC-1.mp4"
        final = self.export_root / "batch" / "RC-1.mimo-final.mp4"
        final.parent.mkdir()
        base.write_bytes(b"base-render")
        final.write_bytes(b"final-delivery")
        render = self.add_render(base)
        render.delivery_output_path = str(final)
        render.delivery_manifest = {
            "script": "这是一条完成的口播。",
            "cues": [{"start": 0.25, "end": 1.2, "text": "完成口播"}],
            "media_probe": {"streams": []},
            "video_duration_seconds": 12,
        }
        self.session.commit()

        metadata = self.client.get(f"/api/renders/{render.id}")
        self.assertEqual(metadata.status_code, 200)
        self.assertEqual(metadata.json()["final_delivery"]["status"], "available")
        self.assertEqual(metadata.json()["final_delivery"]["script"], "这是一条完成的口播。")

        video = self.client.get(f"/api/renders/{render.id}/delivery-video")
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.content, b"final-delivery")
        download = self.client.get(f"/api/renders/{render.id}/delivery-download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b"final-delivery")
        manifest = self.client.get(f"/api/renders/{render.id}/delivery-manifest")
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest.json()["script"], "这是一条完成的口播。")


class RenderThumbnailGenerationTests(unittest.TestCase):
    def test_generation_overwrites_an_existing_cover_at_fifteen_percent(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            output = Path(tempdir) / "render.mp4"
            output.write_bytes(b"video")
            thumbnail = render_thumbnail_path(output)
            thumbnail.write_bytes(b"stale")

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                Path(command[-1]).write_bytes(b"fresh")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("app.workflow.shutil.which", return_value="ffmpeg"), patch(
                "app.workflow.subprocess.run", side_effect=fake_run
            ) as mocked:
                result = create_render_thumbnail(output, 12)

            self.assertEqual(result, thumbnail.resolve())
            self.assertEqual(thumbnail.read_bytes(), b"fresh")
            command = mocked.call_args.args[0]
            self.assertEqual(command[command.index("-ss") + 1], "1.800")


if __name__ == "__main__":
    unittest.main()
