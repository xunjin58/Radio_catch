"""Multimodal, metadata-first planning for short-video remix variants."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .intelligence import _gemini_response_text
from .models import Clip, ModelConfig, ModelTaskAssignment, ModelUsage
from .project_routes import get_business_context
from .security import decrypt_api_key
from .workflow import WorkflowError, serialize_clip, validate_manifest


MAX_PLANNER_CANDIDATES = 24
MAX_IMAGES_PER_CANDIDATE = 4  # one thumbnail plus up to three evidence frames
PLANNER_IMAGE_MAX_EDGE = 512
PLANNER_IMAGE_MAX_BYTES = 256 * 1024

PLANNER_SYSTEM_PROMPT = """你是短视频混剪规划师。基于候选素材的结构化标签、摘要和静态画面，规划可实际执行的短视频 EDL。
标签用于硬约束和筛选，摘要与画面用于判断视觉差异。不得编造素材 ID、画面内容、时间区间或卖点。
先识别少量自然成立的叙事策略，再为每种策略产出不同的具体变体。相同策略可以替换同一叙事槽位的不同素材；素材不足时可使用同菜品、叙事功能相近但展示方式不同的镜头补位。不要为了凑数量输出完全相同的 EDL。
每条变体总时长必须在 9.5 到 15.5 秒之间，所有片段均须使用候选素材列出的 usable_range。只输出 JSON。
输出对象必须包含 `strategies` 和 `variants`；每个 `variants` 项必须直接包含 `strategy_id`、`reason` 和 `clips`。`clips` 是按播放顺序排列的对象数组，每项只用候选中原样给出的 `clip_id`，并包含数值 `start`、`end`、`speed`。不得改用 `structure`、`title`、`target_duration` 或 `total_duration` 代替 `clips`，也不得只描述镜头而不列出片段。"""

PLANNER_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "strategies": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"}, "name": {"type": "STRING"},
                    "reason": {"type": "STRING"}, "allocation": {"type": "INTEGER"},
                },
                "required": ["id", "name", "reason", "allocation"],
            },
        },
        "variants": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "strategy_id": {"type": "STRING"}, "reason": {"type": "STRING"},
                    "substitution_note": {"type": "STRING"},
                    "clips": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "clip_id": {"type": "STRING"}, "start": {"type": "NUMBER"},
                                "end": {"type": "NUMBER"}, "speed": {"type": "NUMBER"},
                            },
                            "required": ["clip_id", "start", "end"],
                        },
                    },
                },
                "required": ["strategy_id", "reason", "clips"],
            },
        },
        "shortfall_reason": {"type": "STRING"},
    },
    "required": ["strategies", "variants"],
}


class RemixPlanningError(RuntimeError):
    pass


def _planner_model(session: Session) -> ModelConfig:
    config = session.scalar(
        select(ModelConfig)
        .join(ModelTaskAssignment)
        .where(ModelTaskAssignment.task_type == "remix_planning", ModelConfig.is_active.is_(True))
    )
    if config is None:
        config = session.scalar(select(ModelConfig).where(ModelConfig.is_default.is_(True), ModelConfig.is_active.is_(True)))
    if config is None:
        raise RemixPlanningError("尚未配置 AI 混剪规划模型")
    if not config.supports_images:
        raise RemixPlanningError("AI 混剪规划模型必须启用图片输入能力")
    return config


def _storage_root() -> Path:
    return Path(os.getenv("RADIO_CATCH_STORAGE_DIR", Path(__file__).resolve().parents[1] / "storage")).resolve()


def _safe_local_image(path: Any) -> Path | None:
    if not path:
        return None
    try:
        resolved = Path(str(path)).resolve()
        root = _storage_root()
    except OSError:
        return None
    if root not in resolved.parents or not resolved.is_file():
        return None
    return resolved


def _thumbnail_path(clip: Clip) -> Path | None:
    direct = _safe_local_image(getattr(clip, "thumbnail_path", None))
    if direct:
        return direct
    for analysis in reversed(list(clip.analyses or [])):
        tags = analysis.tags if isinstance(analysis.tags, dict) else {}
        if analysis.mode == "adaptive_frames":
            found = _safe_local_image(tags.get("thumbnail_path"))
            if found:
                return found
    return None


def _evidence_paths(clip: Clip) -> list[Path]:
    for analysis in reversed(list(clip.analyses or [])):
        if analysis.mode != "adaptive_frames" or not isinstance(analysis.evidence_frames, list):
            continue
        paths = [_safe_local_image(item.get("path")) for item in analysis.evidence_frames if isinstance(item, dict)]
        return [path for path in paths if path is not None][: MAX_IMAGES_PER_CANDIDATE - 1]
    return []


def _image_data(path: Path) -> tuple[str, str]:
    """Return a bounded, transient JPEG for the planning-model request.

    Derived frames are deliberately kept at their review resolution on disk,
    which can make a multi-clip Base64 request too large for a model gateway.
    FFmpeg performs the conversion in memory; neither the JPEG nor its Base64
    representation is persisted.
    """
    source = path.read_bytes()
    decoder = "png" if path.suffix.lower() == ".png" else "mjpeg"
    try:
        completed = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "image2pipe", "-vcodec", decoder,
                "-i", "pipe:0", "-vf", f"scale={PLANNER_IMAGE_MAX_EDGE}:{PLANNER_IMAGE_MAX_EDGE}:force_original_aspect_ratio=decrease",
                "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "pipe:1",
            ],
            input=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        if completed.stdout and len(completed.stdout) <= PLANNER_IMAGE_MAX_BYTES:
            return "image/jpeg", base64.b64encode(completed.stdout).decode("ascii")
    except (OSError, subprocess.SubprocessError):
        pass
    if len(source) <= PLANNER_IMAGE_MAX_BYTES:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return mime, base64.b64encode(source).decode("ascii")
    raise RemixPlanningError("规划图片无法压缩到安全请求大小，请确认 FFmpeg 可用")


def _score(item: dict[str, Any]) -> float:
    quality = item.get("quality_score")
    confidence = item.get("confidence")
    try:
        return float(quality if quality is not None else 0) + float(confidence if confidence is not None else 0)
    except (TypeError, ValueError):
        return 0.0


def _diversity_key(item: dict[str, Any]) -> str:
    tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
    values = [item.get("segment_role", "middle")]
    for key in ("actions", "visual_hooks", "commerce_roles", "shot_type"):
        value = tags.get(key, "")
        values.append(json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else str(value))
    return "|".join(values)


def _candidate_pool(session: Session, dish: str) -> tuple[int, list[dict[str, Any]]]:
    rows: list[tuple[Clip, dict[str, Any]]] = []
    for clip in session.scalars(select(Clip).order_by(Clip.created_at.desc())).all():
        item = serialize_clip(clip)
        if item.get("review_status") != "approved" or item.get("dish") != dish or not item.get("usable_range"):
            continue
        images = [path for path in [_thumbnail_path(clip), *_evidence_paths(clip)] if path is not None]
        item["_clip"] = clip
        item["_image_paths"] = images[:MAX_IMAGES_PER_CANDIDATE]
        rows.append((clip, item))
    ranked = sorted((item for _, item in rows), key=_score, reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        key = _diversity_key(item)
        if key not in seen:
            selected.append(item); seen.add(key)
    selected_ids = {item["id"] for item in selected}
    selected.extend(item for item in ranked if item["id"] not in selected_ids)
    return len(rows), selected[:MAX_PLANNER_CANDIDATES]


def _candidate_text(item: dict[str, Any]) -> str:
    tags = dict(item.get("tags") or {})
    tags.pop("thumbnail_path", None)
    payload = {
        "clip_id": item["id"], "filename": item.get("filename"), "summary": item.get("summary"),
        "segment_role": item.get("segment_role"), "tags": tags,
        "usable_range": item.get("usable_range"), "quality_score": item.get("quality_score"),
        "confidence": item.get("confidence"), "image_count": len(item.get("_image_paths", [])),
    }
    return json.dumps(payload, ensure_ascii=False)


def _clean_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        raise RemixPlanningError("规划模型没有返回 JSON 文本")
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RemixPlanningError("规划模型没有返回有效 JSON") from exc
    if not isinstance(result, dict):
        raise RemixPlanningError("规划模型响应必须是 JSON 对象")
    return result


def _record_usage(session: Session, config: ModelConfig, started: float, status: str, error: str | None = None) -> None:
    session.add(ModelUsage(
        model_config_id=config.id, operation="remix_planning",
        latency_ms=round((time.perf_counter() - started) * 1000), status=status, error_message=error,
    ))


async def _call_model(session: Session, config: ModelConfig, dish: str, requested_count: int, target_duration_seconds: float, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    instruction = (
        f"目标菜品：{dish}。请求生成 {requested_count} 条实际变体，目标时长约 {target_duration_seconds:.1f} 秒。候选素材的画面紧跟在各自素材说明之后。"
        f"项目业务背景（只作标签用途说明，不可视为视频事实）：{get_business_context(session)}"
    )
    started = time.perf_counter()
    try:
        if config.protocol.lower() == "gemini":
            parts: list[dict[str, Any]] = [{"text": instruction}]
            for item in candidates:
                parts.append({"text": _candidate_text(item)})
                for path in item["_image_paths"]:
                    mime, encoded = _image_data(path)
                    parts.append({"inline_data": {"mime_type": mime, "data": encoded}})
            request = {
                "system_instruction": {"parts": [{"text": PLANNER_SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": PLANNER_RESPONSE_SCHEMA},
            }
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                response = await client.post(
                    f"{config.base_url.rstrip('/')}/v1beta/models/{config.model_name}:generateContent",
                    headers={"Authorization": f"Bearer {decrypt_api_key(config.api_key_encrypted)}", "Content-Type": "application/json"}, json=request,
                )
            response.raise_for_status()
            raw = _gemini_response_text(response.json())
        else:
            content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
            for item in candidates:
                content.append({"type": "text", "text": _candidate_text(item)})
                for path in item["_image_paths"]:
                    mime, encoded = _image_data(path)
                    content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}", "detail": "low"}})
            request: dict[str, Any] = {
                "model": config.model_name,
                "messages": [{"role": "system", "content": PLANNER_SYSTEM_PROMPT}, {"role": "user", "content": content}],
                "temperature": 0.2,
            }
            if config.supports_structured_json:
                request["response_format"] = {"type": "json_object"}
            async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                response = await client.post(
                    f"{config.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {decrypt_api_key(config.api_key_encrypted)}", "Content-Type": "application/json"}, json=request,
                )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
        result = _clean_json(raw)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, OSError, RemixPlanningError) as exc:
        error = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        _record_usage(session, config, started, "failed", error); session.commit()
        raise RemixPlanningError(f"AI 混剪规划失败：{error}") from exc
    _record_usage(session, config, started, "success"); session.commit()
    return result


def _normalize_plan(session: Session, dish: str, requested_count: int, raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    raw_strategies = raw.get("strategies") if isinstance(raw.get("strategies"), list) else []
    strategy_map: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_strategies, start=1):
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or f"strategy_{index}")[:80]
        if identifier not in strategy_map:
            strategy_map[identifier] = {"id": identifier, "name": str(item.get("name") or identifier)[:160], "reason": str(item.get("reason") or "")[:1000], "allocation": 0}
    variants: list[dict[str, Any]] = []
    signatures: set[tuple[tuple[str, float, float, float], ...]] = set()
    for index, item in enumerate(raw.get("variants") if isinstance(raw.get("variants"), list) else [], start=1):
        if len(variants) >= requested_count or not isinstance(item, dict):
            continue
        strategy_id = str(item.get("strategy_id") or "strategy_1")[:80]
        if strategy_id not in strategy_map:
            strategy_map[strategy_id] = {"id": strategy_id, "name": f"策略 {len(strategy_map) + 1}", "reason": "", "allocation": 0}
        try:
            manifest = validate_manifest(session, dish, item.get("clips"))
        except (WorkflowError, TypeError, ValueError):
            continue
        signature = tuple((str(row["clip_id"]), round(float(row["start"]), 3), round(float(row["end"]), 3), round(float(row["speed"]), 3)) for row in manifest)
        if signature in signatures:
            continue
        signatures.add(signature)
        clips = [{"clip_id": row["clip_id"], "start": row["start"], "end": row["end"], "speed": row["speed"]} for row in manifest]
        variants.append({
            "id": f"variant_{len(variants) + 1}", "strategy_id": strategy_id,
            "name": f"{strategy_map[strategy_id]['name']}-{len(variants) + 1:02d}",
            "reason": str(item.get("reason") or "")[:1000], "substitution_note": str(item.get("substitution_note") or "")[:1000],
            "clips": clips,
        })
        strategy_map[strategy_id]["allocation"] += 1
    if not variants:
        raise RemixPlanningError("AI 未能规划出符合审核、菜品和时长约束的成片")
    strategies = [item for item in strategy_map.values() if item["allocation"]]
    shortfall = raw.get("shortfall_reason") if isinstance(raw.get("shortfall_reason"), str) else None
    if len(variants) < requested_count and not shortfall:
        shortfall = "现有素材无法形成更多不重复且符合时长约束的变体。"
    return strategies, variants, shortfall[:1000] if shortfall else None


async def plan_remix(session: Session, *, dish: str, requested_count: int, target_duration_seconds: float) -> dict[str, Any]:
    config = _planner_model(session)
    candidate_count, candidates = _candidate_pool(session, dish)
    if not candidates:
        raise RemixPlanningError("该菜品没有已审核且具备可用区间的素材")
    raw = await _call_model(session, config, dish, requested_count, target_duration_seconds, candidates)
    strategies, variants, shortfall = _normalize_plan(session, dish, requested_count, raw)
    return {
        "candidate_count": candidate_count, "included_candidate_count": len(candidates),
        "excluded_candidate_count": max(0, candidate_count - len(candidates)),
        "candidate_selection_note": f"按角色、标签和质量预筛，最多向模型发送 {MAX_PLANNER_CANDIDATES} 条代表性素材。",
        "requested_count": requested_count, "planned_count": len(variants), "target_duration_seconds": target_duration_seconds,
        "strategies": strategies, "variants": variants, "shortfall_reason": shortfall,
        "planner_model_config_id": config.id,
    }
