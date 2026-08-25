"""Regression tests for the Gemini native-video adapter."""

from __future__ import annotations

import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config_routes import test_connection
from app.database import Base
from app.intelligence import GEMINI_RESPONSE_SCHEMA, IntelligenceError, _gemini_response_text, understand_clip
from app.models import Clip, ClipAnalysis, ModelConfig, ModelTaskAssignment, ModelUsage
from app.project_routes import DEFAULT_BUSINESS_CONTEXT, get_project_settings, update_project_settings
from app.schemas import ProjectSettingsUpdate, TestConnectionRequest
from app.security import encrypt_api_key


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.is_success = status_code < 400

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise RuntimeError("provider failed")

    def json(self) -> dict:
        return self.payload


class FakeAsyncClient:
    response = FakeResponse({})
    calls: list[dict] = []

    def __init__(self, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": "post", "url": url, **kwargs})
        return self.response

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"method": "get", "url": url, **kwargs})
        return self.response


def gemini_payload(summary: str = "原生视频") -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": """{
            "summary": "%s", "segment_role": "head", "dish": ["烤鱼"],
            "actions": ["翻动"], "visual_hooks": ["火焰"], "audio_hooks": ["滋滋声"],
            "commerce_roles": ["hook", "product_proof", "not-a-role", "hook"],
            "shot_type": "特写", "climax_time": 1, "usable_range": {"start": 0, "end": 2},
            "quality_score": 0.8, "confidence": 0.9
        }""" % summary}]}}]
    }


class GeminiIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.session = Session(create_engine("sqlite://"))
        Base.metadata.create_all(self.session.bind)

    def tearDown(self) -> None:
        self.session.close()
        self.tempdir.cleanup()

    def add_config(self, protocol: str, **overrides: object) -> ModelConfig:
        config = ModelConfig(
            name=f"{protocol}-model", provider="test", protocol=protocol,
            base_url="https://api.example.test" if protocol == "gemini" else "https://api.example.test/v1",
            api_key_encrypted=encrypt_api_key("test-key"),
            model_name="gemini-3-flash-preview" if protocol == "gemini" else "mimo-v2.5" if protocol == "mimo" else "vision-model",
            supports_images=True, supports_native_video=protocol in {"gemini", "mimo"}, supports_structured_json=True,
            **overrides,
        )
        self.session.add(config)
        self.session.flush()
        self.session.add(ModelTaskAssignment(task_type="clip_understanding", model_config_id=config.id))
        self.session.commit()
        return config

    def add_clip(self, suffix: str = ".mp4", content: bytes = b"video-with-audio") -> Clip:
        source = Path(self.tempdir.name) / f"clip{suffix}"
        source.write_bytes(content)
        clip = Clip(
            original_filename=source.name, file_path=str(source), sha256="a" * 64,
            file_size_bytes=len(content), duration_seconds=2.0, has_audio=True,
        )
        self.session.add(clip)
        self.session.commit()
        return clip

    def test_gemini_sends_original_video_with_structured_output(self) -> None:
        self.add_config("gemini")
        clip = self.add_clip()
        FakeAsyncClient.calls = []
        FakeAsyncClient.response = FakeResponse(gemini_payload())
        with patch("app.intelligence.httpx.AsyncClient", FakeAsyncClient):
            row = asyncio.run(understand_clip(self.session, clip.id))

        call = FakeAsyncClient.calls[-1]
        parts = call["json"]["contents"][0]["parts"]
        inline = parts[1]["inline_data"]
        self.assertEqual(call["url"], "https://api.example.test/v1beta/models/gemini-3-flash-preview:generateContent")
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(inline["mime_type"], "video/mp4")
        self.assertEqual(inline["data"], base64.b64encode(b"video-with-audio").decode("ascii"))
        self.assertEqual(call["json"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("销售新鲜柠檬", call["json"]["system_instruction"]["parts"][0]["text"])
        self.assertIn("commerce_roles", call["json"]["generationConfig"]["responseSchema"]["required"])
        self.assertEqual(row.mode, "native_video")
        self.assertEqual(row.tags["commerce_roles"], ["hook", "product_proof"])
        self.assertEqual(row.evidence_frames[0]["has_audio"], True)
        self.assertNotIn("data", row.evidence_frames[0])

    def test_gemini_wrapped_response_is_supported(self) -> None:
        self.assertIn("原生视频", _gemini_response_text({"data": gemini_payload()}))

    def test_project_settings_returns_default_and_persists_context(self) -> None:
        self.assertEqual(get_project_settings(self.session).business_context, DEFAULT_BUSINESS_CONTEXT)
        updated = update_project_settings(ProjectSettingsUpdate(business_context="只标注可见的柠檬切片。"), self.session)
        self.assertEqual(updated.business_context, "只标注可见的柠檬切片。")
        self.assertEqual(get_project_settings(self.session).business_context, "只标注可见的柠檬切片。")
        self.assertIn("commerce_roles", GEMINI_RESPONSE_SCHEMA["properties"])

    def test_gemini_over_limit_fails_without_frame_fallback(self) -> None:
        self.add_config("gemini", max_native_media_bytes=3)
        clip = self.add_clip(content=b"four")
        with self.assertRaisesRegex(IntelligenceError, "模型理解失败"):
            asyncio.run(understand_clip(self.session, clip.id))
        self.assertEqual(self.session.scalars(select(ClipAnalysis)).all(), [])
        usage = self.session.scalar(select(ModelUsage))
        self.assertEqual(usage.status, "failed")

    def test_gemini_rejects_frame_modes_without_calling_provider(self) -> None:
        self.add_config("gemini")
        clip = self.add_clip()
        FakeAsyncClient.calls = []
        with self.assertRaisesRegex(IntelligenceError, "仅支持 auto 或 native"):
            asyncio.run(understand_clip(self.session, clip.id, mode="adaptive"))
        self.assertEqual(FakeAsyncClient.calls, [])

    def test_gemini_invalid_provider_output_is_recorded_as_failure(self) -> None:
        self.add_config("gemini")
        clip = self.add_clip()
        FakeAsyncClient.calls = []
        FakeAsyncClient.response = FakeResponse({"candidates": []})
        with patch("app.intelligence.httpx.AsyncClient", FakeAsyncClient):
            with self.assertRaisesRegex(IntelligenceError, "模型理解失败"):
                asyncio.run(understand_clip(self.session, clip.id))
        usage = self.session.scalar(select(ModelUsage))
        self.assertEqual(usage.status, "failed")

    def test_openai_compatible_frames_still_use_chat_completions(self) -> None:
        self.add_config("openai")
        clip = self.add_clip()
        frame = Path(self.tempdir.name) / "frame.png"
        frame.write_bytes(b"frame")
        self.session.add(ClipAnalysis(clip_id=clip.id, mode="adaptive_frames", evidence_frames=[{"path": str(frame), "time": 0}]))
        self.session.commit()
        FakeAsyncClient.calls = []
        FakeAsyncClient.response = FakeResponse({"choices": [{"message": {"content": _gemini_response_text(gemini_payload())}}]})
        with patch("app.intelligence.httpx.AsyncClient", FakeAsyncClient):
            row = asyncio.run(understand_clip(self.session, clip.id))
        self.assertEqual(FakeAsyncClient.calls[-1]["url"], "https://api.example.test/v1/chat/completions")
        self.assertIn("销售新鲜柠檬", FakeAsyncClient.calls[-1]["json"]["messages"][0]["content"])
        self.assertEqual(row.mode, "adaptive_frames")

    def test_gemini_connection_test_uses_model_list_only(self) -> None:
        config = self.add_config("gemini")
        FakeAsyncClient.calls = []
        FakeAsyncClient.response = FakeResponse({"data": []})
        with patch("app.config_routes.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(test_connection(config.id, TestConnectionRequest(), self.session))
        self.assertTrue(result.ok)
        self.assertEqual(FakeAsyncClient.calls[-1]["url"], "https://api.example.test/v1/models")
        self.assertIn("not tested", result.detail)

    def test_mimo_sends_original_video_to_openai_compatible_endpoint(self) -> None:
        self.add_config("mimo")
        clip = self.add_clip()
        FakeAsyncClient.calls = []
        FakeAsyncClient.response = FakeResponse({"choices": [{"message": {"content": _gemini_response_text(gemini_payload("MiMo 原生视频"))}}]})
        with patch("app.intelligence.httpx.AsyncClient", FakeAsyncClient):
            row = asyncio.run(understand_clip(self.session, clip.id))

        call = FakeAsyncClient.calls[-1]
        video = call["json"]["messages"][1]["content"][1]
        self.assertEqual(call["url"], "https://api.example.test/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(video["type"], "video_url")
        self.assertEqual(video["video_url"]["url"], f"data:video/mp4;base64,{base64.b64encode(b'video-with-audio').decode('ascii')}")
        self.assertEqual(video["fps"], 2)
        self.assertEqual(call["json"]["response_format"], {"type": "json_object"})
        self.assertIn("销售新鲜柠檬", call["json"]["messages"][0]["content"])
        self.assertEqual(row.mode, "native_video")
        self.assertEqual(row.evidence_frames[0]["source"], "mimo_native_video")
        self.assertNotIn("data", row.evidence_frames[0])

    def test_mimo_rejects_frame_modes_without_calling_provider(self) -> None:
        self.add_config("mimo")
        clip = self.add_clip()
        FakeAsyncClient.calls = []
        with self.assertRaisesRegex(IntelligenceError, "仅支持 auto 或 native"):
            asyncio.run(understand_clip(self.session, clip.id, mode="dense"))
        self.assertEqual(FakeAsyncClient.calls, [])

    def test_mimo_rejects_oversized_base64_request_before_calling_provider(self) -> None:
        self.add_config("mimo")
        clip = self.add_clip()
        FakeAsyncClient.calls = []
        with patch("app.intelligence.MIMO_MAX_BASE64_VIDEO_BYTES", 10):
            with self.assertRaisesRegex(IntelligenceError, "模型理解失败"):
                asyncio.run(understand_clip(self.session, clip.id))
        self.assertEqual(FakeAsyncClient.calls, [])
        usage = self.session.scalar(select(ModelUsage))
        self.assertEqual(usage.status, "failed")

    def test_mimo_connection_test_uses_model_list_only(self) -> None:
        config = self.add_config("mimo")
        FakeAsyncClient.calls = []
        FakeAsyncClient.response = FakeResponse({"data": []})
        with patch("app.config_routes.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(test_connection(config.id, TestConnectionRequest(), self.session))
        self.assertTrue(result.ok)
        self.assertEqual(FakeAsyncClient.calls[-1]["url"], "https://api.example.test/v1/models")
        self.assertIn("not tested", result.detail)


if __name__ == "__main__":
    unittest.main()
