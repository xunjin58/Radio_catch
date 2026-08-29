"""Regression tests for multimodal remix planning."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Clip, ClipAnalysis, ModelConfig, ModelTaskAssignment, ModelUsage
from app.remix_planning import PLANNER_IMAGE_MAX_BYTES, RemixPlanningError, _candidate_pool, _image_data, plan_remix
from app.security import encrypt_api_key
from app.workflow_routes import router as workflow_router


class FakeResponse:
    def __init__(self, payload: dict): self.payload = payload
    def raise_for_status(self) -> None: return None
    def json(self) -> dict: return self.payload


class FakeAsyncClient:
    response = FakeResponse({})
    calls: list[dict] = []
    def __init__(self, **_kwargs: object): pass
    async def __aenter__(self) -> "FakeAsyncClient": return self
    async def __aexit__(self, *_args: object) -> None: return None
    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs}); return self.response


class RemixPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage = Path(self.tempdir.name) / "storage"; self.storage.mkdir()
        self.session = Session(create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool))
        Base.metadata.create_all(self.session.bind)

    def tearDown(self) -> None:
        self.session.close(); self.tempdir.cleanup()

    def add_config(self, supports_images: bool = True) -> None:
        config = ModelConfig(
            name="planner", provider="test", protocol="openai", base_url="https://api.example.test/v1",
            api_key_encrypted=encrypt_api_key("test-key"), model_name="vision-model", supports_images=supports_images,
            supports_structured_json=True, is_default=True,
        )
        self.session.add(config); self.session.flush()
        self.session.add(ModelTaskAssignment(task_type="remix_planning", model_config_id=config.id)); self.session.commit()

    def add_clip(self, index: int, hook: str = "俯拍") -> Clip:
        source = self.storage / "clips" / f"clip-{index}.mp4"; source.parent.mkdir(exist_ok=True); source.write_bytes(b"video")
        derived = self.storage / "derived" / str(index); derived.mkdir(parents=True)
        thumbnail = derived / "thumbnail.png"; thumbnail.write_bytes(b"thumbnail")
        frames = []
        for frame_index in range(4):
            frame = derived / f"frame-{frame_index}.png"; frame.write_bytes(f"frame-{frame_index}".encode())
            frames.append({"time": frame_index * 3, "path": str(frame)})
        clip = Clip(original_filename=source.name, file_path=str(source), sha256=f"{index:064x}", file_size_bytes=5, duration_seconds=22, review_status="approved")
        analysis = ClipAnalysis(
            clip=clip, mode="adaptive_frames", summary=f"{hook}切柠檬", segment_role="middle",
            tags={"dish": ["柠檬"], "actions": ["切片"], "visual_hooks": [hook], "thumbnail_path": str(thumbnail)},
            usable_range={"start": 0, "end": 22}, quality_score=0.8, confidence=0.9, evidence_frames=frames, review_status="approved",
        )
        self.session.add_all([clip, analysis]); self.session.commit(); return clip

    def test_planning_uses_bounded_local_images_and_removes_duplicate_edls(self) -> None:
        first, second = self.add_clip(1, "俯拍"), self.add_clip(2, "侧拍")
        self.add_config(); FakeAsyncClient.calls = []
        FakeAsyncClient.response = FakeResponse({"choices": [{"message": {"content": json.dumps({
            "strategies": [{"id": "slice", "name": "切片展示", "reason": "两种镜头角度", "allocation": 3}],
            "variants": [
                {"strategy_id": "slice", "reason": "俯拍切片", "clips": [{"clip_id": first.id, "start": 0, "end": 22}]},
                {"strategy_id": "slice", "reason": "侧拍切片", "substitution_note": "替换为侧拍", "clips": [{"clip_id": second.id, "start": 0, "end": 22}]},
                {"strategy_id": "slice", "reason": "重复", "clips": [{"clip_id": first.id, "start": 0, "end": 22}]},
            ], "shortfall_reason": "只有两种有效展示方式",
        }, ensure_ascii=False)}}]})

        with patch.dict(os.environ, {"RADIO_CATCH_STORAGE_DIR": str(self.storage)}), patch("app.remix_planning.httpx.AsyncClient", FakeAsyncClient):
            plan = asyncio.run(plan_remix(self.session, dish="柠檬", requested_count=3, target_duration_seconds=22))

        self.assertEqual(plan["candidate_count"], 2); self.assertEqual(plan["planned_count"], 2)
        self.assertEqual(plan["shortfall_reason"], "只有两种有效展示方式")
        self.assertNotIn("data:image", json.dumps(plan))
        content = FakeAsyncClient.calls[-1]["json"]["messages"][1]["content"]
        image_blocks = [block for block in content if block["type"] == "image_url"]
        self.assertEqual(len(image_blocks), 8)  # thumbnail + three evidence frames for each clip
        self.assertTrue(all("data:image" in block["image_url"]["url"] for block in image_blocks))
        usage = self.session.scalar(select(ModelUsage)); self.assertEqual(usage.operation, "remix_planning")

    def test_candidate_pool_caps_at_twenty_four(self) -> None:
        for index in range(25): self.add_clip(index + 1, f"镜头-{index}")
        with patch.dict(os.environ, {"RADIO_CATCH_STORAGE_DIR": str(self.storage)}):
            total, candidates = _candidate_pool(self.session, "柠檬")
        self.assertEqual(total, 25); self.assertEqual(len(candidates), 24)

    def test_planner_image_is_jpeg_and_capped(self) -> None:
        image = self.storage / "derived" / "image.png"; image.parent.mkdir()
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=2x2", "-frames:v", "1", str(image)],
            check=True,
        )

        mime, encoded = _image_data(image)

        self.assertEqual(mime, "image/jpeg")
        self.assertLessEqual(len(base64.b64decode(encoded)), PLANNER_IMAGE_MAX_BYTES)

    def test_planning_requires_an_image_capable_model(self) -> None:
        self.add_clip(1); self.add_config(supports_images=False)
        with patch.dict(os.environ, {"RADIO_CATCH_STORAGE_DIR": str(self.storage)}), self.assertRaisesRegex(RemixPlanningError, "图片输入"):
            asyncio.run(plan_remix(self.session, dish="柠檬", requested_count=1, target_duration_seconds=22))

    def test_remix_plan_endpoint_exposes_planning_result(self) -> None:
        from app.database import get_session
        app = FastAPI(); app.include_router(workflow_router); app.dependency_overrides[get_session] = lambda: self.session
        expected = {"candidate_count": 2, "included_candidate_count": 2, "excluded_candidate_count": 0, "candidate_selection_note": "ok", "requested_count": 2, "planned_count": 1, "target_duration_seconds": 22, "strategies": [], "variants": [{"id": "variant_1"}], "shortfall_reason": "素材不足", "planner_model_config_id": "model"}
        with TestClient(app) as client, patch("app.workflow_routes.plan_remix", AsyncMock(return_value=expected)) as planner:
            response = client.post("/api/remix-plans", json={"name": "测试", "dish": "柠檬", "requested_count": 2, "target_duration_seconds": 22})
        self.assertEqual(response.status_code, 200); self.assertEqual(response.json()["planned_count"], 1)
        planner.assert_awaited_once_with(self.session, dish="柠檬", requested_count=2, target_duration_seconds=22.0)


if __name__ == "__main__":
    unittest.main()
