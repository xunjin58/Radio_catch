"""Regression tests for status-neutral material-library metadata edits."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_session
from app.models import Clip, ClipAnalysis
from app.workflow_routes import router


class ClipMetadataRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = Session(create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        ))
        Base.metadata.create_all(self.session.bind)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.session.close()

    def add_clip(self, status: str = "approved") -> tuple[Clip, ClipAnalysis]:
        clip = Clip(
            original_filename="source.mp4", file_path="/tmp/source.mp4", sha256="a" * 64,
            file_size_bytes=1, review_status=status,
        )
        analysis = ClipAnalysis(
            clip=clip, mode="adaptive_frames", review_status=status, summary="旧摘要",
            segment_role="middle", climax_time=0.5, usable_range={"start": 0.0, "end": 1.0},
            tags={"dish": "旧菜", "nested": {"before": True}, "thumbnail_path": "/tmp/thumb.jpg"},
        )
        self.session.add_all([clip, analysis])
        self.session.commit()
        return clip, analysis

    def test_metadata_replaces_tags_and_preserves_review_status(self) -> None:
        clip, analysis = self.add_clip()

        response = self.client.patch(f"/api/clips/{clip.id}/metadata", json={
            "summary": "人工修订的摘要", "dish": "新菜", "segment_role": "head",
            "climax_time": 0.8,
            "usable_range": {"start": 0.2, "end": 2.5},
            "tags": {"nested": {"after": ["ok"]}, "actions": ["翻动"]},
        })

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"], "人工修订的摘要")
        self.assertEqual(body["dish"], "新菜")
        self.assertEqual(body["segment_role"], "head")
        self.assertEqual(body["climax_time"], 0.8)
        self.assertEqual(body["usable_range"], {"start": 0.2, "end": 2.5})
        self.assertEqual(body["tags"]["nested"], {"after": ["ok"]})
        self.assertNotIn("before", body["tags"]["nested"])
        self.assertEqual(body["tags"]["thumbnail_path"], "/tmp/thumb.jpg")
        self.assertEqual(self.session.get(Clip, clip.id).review_status, "approved")
        self.assertEqual(self.session.get(ClipAnalysis, analysis.id).review_status, "approved")

    def test_metadata_omitted_fields_and_review_status_remain_unchanged(self) -> None:
        clip, analysis = self.add_clip(status="needs_review")

        response = self.client.patch(f"/api/clips/{clip.id}/metadata", json={"summary": "只改摘要"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tags"]["dish"], "旧菜")
        self.assertEqual(response.json()["usable_range"], {"start": 0.0, "end": 1.0})
        self.assertEqual(self.session.get(Clip, clip.id).review_status, "needs_review")
        self.assertEqual(self.session.get(ClipAnalysis, analysis.id).review_status, "needs_review")

    def test_metadata_rejects_invalid_role_range_and_tags(self) -> None:
        clip, _ = self.add_clip()

        self.assertEqual(self.client.patch(f"/api/clips/{clip.id}/metadata", json={"segment_role": "intro"}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/clips/{clip.id}/metadata", json={"usable_range": {"start": 2, "end": 1}}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/clips/{clip.id}/metadata", json={"tags": ["not", "an", "object"]}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/clips/{clip.id}/metadata", json={"climax_time": -0.1}).status_code, 422)

    def test_metadata_can_clear_best_appearance_time(self) -> None:
        clip, _ = self.add_clip()

        response = self.client.patch(f"/api/clips/{clip.id}/metadata", json={"climax_time": None})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["climax_time"])

    def test_clip_detail_uses_latest_analysis_only(self) -> None:
        clip, _ = self.add_clip()
        latest = ClipAnalysis(
            clip_id=clip.id, mode="native_video", review_status="approved", summary="最新摘要",
            segment_role="tail", tags={"dish": "新菜"}, usable_range={"start": 1.0, "end": 3.0},
            created_at=datetime.utcnow() + timedelta(seconds=1), updated_at=datetime.utcnow() + timedelta(seconds=1),
        )
        self.session.add(latest)
        self.session.commit()

        response = self.client.get(f"/api/clips/{clip.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], "最新摘要")
        self.assertEqual(response.json()["dish"], "新菜")
        self.assertEqual(response.json()["segment_role"], "tail")

    def test_clip_detail_tolerates_mixed_timezone_analysis_times(self) -> None:
        clip, _ = self.add_clip()
        latest = ClipAnalysis(
            clip_id=clip.id, mode="native_video", review_status="approved", summary="最新摘要",
            segment_role="head", tags={"dish": "新菜"}, usable_range={"start": 0.0, "end": 2.0},
            created_at=datetime.utcnow() + timedelta(seconds=1), updated_at=datetime.utcnow() + timedelta(seconds=1),
        )
        self.session.add(latest)
        self.session.commit()
        latest.updated_at = datetime.now(timezone.utc)

        response = self.client.get(f"/api/clips/{clip.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], "最新摘要")


if __name__ == "__main__":
    unittest.main()
