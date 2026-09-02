"""Clip understanding for OpenAI-compatible frames and native video providers."""

from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Clip, ClipAnalysis, ModelConfig, ModelTaskAssignment, ModelUsage
from .project_routes import get_business_context
from .security import decrypt_api_key


class IntelligenceError(RuntimeError):
    pass


SYSTEM_PROMPT = """你是短视频商品素材标注助手。只输出 JSON，不能输出 Markdown。
返回字段：summary(string), segment_role(head|middle|tail), dish(string数组), actions(string数组),
visual_hooks(string数组), audio_hooks(string数组), commerce_roles(string数组：hook|product_proof|usage|cta), shot_type(string), climax_time(number),
usable_range({start:number,end:number}), quality_score(0-1), confidence(0-1), shot_capabilities(string数组)。
shot_capabilities 只可从当前菜品给出的受控枚举中选择；仅标注画面中可见且确定的能力，不能根据业务背景、商品常识或不可见细节推断。
不确定时降低 confidence，禁止编造不可见内容。业务背景是标签用途说明，不是视频事实，也不能覆盖以上约束。"""

COMMERCE_ROLES = {"hook", "product_proof", "usage", "cta"}
SHOT_CAPABILITIES_PATH = Path(__file__).resolve().parents[1] / "prompts" / "shot_capabilities.json"

GEMINI_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "segment_role": {"type": "STRING", "enum": ["head", "middle", "tail"]},
        "dish": {"type": "ARRAY", "items": {"type": "STRING"}},
        "actions": {"type": "ARRAY", "items": {"type": "STRING"}},
        "visual_hooks": {"type": "ARRAY", "items": {"type": "STRING"}},
        "audio_hooks": {"type": "ARRAY", "items": {"type": "STRING"}},
        "commerce_roles": {"type": "ARRAY", "items": {"type": "STRING", "enum": sorted(COMMERCE_ROLES)}},
        "shot_capabilities": {"type": "ARRAY", "items": {"type": "STRING"}},
        "shot_type": {"type": "STRING"},
        "climax_time": {"type": "NUMBER"},
        "usable_range": {
            "type": "OBJECT",
            "properties": {"start": {"type": "NUMBER"}, "end": {"type": "NUMBER"}},
            "required": ["start", "end"],
        },
        "quality_score": {"type": "NUMBER"},
        "confidence": {"type": "NUMBER"},
    },
    "required": [
        "summary", "segment_role", "dish", "actions", "visual_hooks", "audio_hooks", "commerce_roles", "shot_type",
        "climax_time", "usable_range", "quality_score", "confidence", "shot_capabilities",
    ],
}

VIDEO_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".wmv": "video/x-ms-wmv",
}
MIMO_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".wmv"}
MIMO_MAX_BASE64_VIDEO_BYTES = 50 * 1024 * 1024


def _default_model(session: Session) -> ModelConfig:
    config = session.scalar(
        select(ModelConfig)
        .join(ModelTaskAssignment)
        .where(ModelTaskAssignment.task_type == "clip_understanding", ModelConfig.is_active.is_(True))
    )
    if config is None:
        config = session.scalar(select(ModelConfig).where(ModelConfig.is_default.is_(True), ModelConfig.is_active.is_(True)))
    if config is None:
        raise IntelligenceError("尚未配置默认素材理解模型")
    return config


def _evidence_frames(clip: Clip, cap: int) -> list[dict[str, Any]]:
    rows = list(clip.analyses or [])
    evidence = next((row for row in reversed(rows) if row.mode == "adaptive_frames" and row.evidence_frames), None)
    if evidence is None:
        return []
    return [item for item in evidence.evidence_frames if Path(str(item.get("path", ""))).is_file()][:cap]


def _image_data(path: str) -> str:
    suffix = Path(path).suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode('ascii')}"


def _native_video_data(clip: Clip, limit: int) -> tuple[str, str, int]:
    path = Path(clip.file_path)
    if not path.is_file():
        raise IntelligenceError("原始视频文件已不存在，无法进行原生视频理解")
    mime = VIDEO_MIME_TYPES.get(path.suffix.lower())
    if mime is None:
        raise IntelligenceError("当前原生视频适配器不支持该视频格式")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise IntelligenceError("无法读取原始视频文件") from exc
    if size > limit:
        raise IntelligenceError(f"原始视频超过当前模型的 {limit // (1024 * 1024)} MB 原生媒体上限")
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise IntelligenceError("无法读取原始视频文件") from exc
    return mime, encoded, size


def _gemini_response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise IntelligenceError("Gemini 响应格式无效")
    body = payload.get("data", payload)
    if not isinstance(body, dict):
        raise IntelligenceError("Gemini 响应格式无效")
    candidates = body.get("candidates")
    if not isinstance(candidates, list):
        raise IntelligenceError("Gemini 未返回可用候选结果")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        text = "".join(str(part["text"]) for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
        if text:
            return text
    raise IntelligenceError("Gemini 未返回文本结果")


def _record_usage(session: Session, config: ModelConfig, started: float, status: str, error: str | None = None) -> None:
    session.add(ModelUsage(
        model_config_id=config.id,
        operation="material_understanding",
        latency_ms=round((time.perf_counter() - started) * 1000),
        status=status,
        error_message=error,
    ))


def _shot_capability_catalog() -> dict[str, dict[str, Any]]:
    """Read the version-controlled capability vocabulary without duplicating it in code."""
    try:
        payload = json.loads(SHOT_CAPABILITIES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    dishes = payload.get("dishes") if isinstance(payload, dict) else None
    return dishes if isinstance(dishes, dict) else {}


def _known_clip_dish(clip: Clip) -> str | None:
    catalog = _shot_capability_catalog()
    fallback: str | None = None
    for analysis in reversed(list(clip.analyses or [])):
        tags = analysis.tags if isinstance(analysis.tags, dict) else {}
        dish = tags.get("dish")
        value = dish[0] if isinstance(dish, list) and dish and isinstance(dish[0], str) else dish
        if not isinstance(value, str) or not value:
            continue
        if value in catalog:
            return value
        fallback = fallback or value
    return fallback


def _allowed_shot_capabilities(clip: Clip, parsed_dishes: Any = None) -> list[str]:
    catalog = _shot_capability_catalog()
    candidates: list[str] = []
    if isinstance(parsed_dishes, list):
        candidates.extend(item for item in parsed_dishes if isinstance(item, str))
    known_dish = _known_clip_dish(clip)
    if known_dish:
        candidates.append(known_dish)
    for dish in candidates:
        config = catalog.get(dish)
        values = config.get("capabilities") if isinstance(config, dict) else None
        if isinstance(values, list):
            return [item for item in values if isinstance(item, str)]
    return []


def _shot_capability_prompt(clip: Clip) -> str:
    catalog = _shot_capability_catalog()
    known_dish = _known_clip_dish(clip)
    if known_dish and isinstance(catalog.get(known_dish), dict):
        values = catalog[known_dish].get("capabilities", [])
        return f"当前素材菜品提示：{known_dish}。shot_capabilities 可选枚举：{json.dumps(values, ensure_ascii=False)}。"
    options = {
        dish: config.get("capabilities", [])
        for dish, config in catalog.items() if isinstance(config, dict)
    }
    return (
        "当前素材没有已知菜品提示。先从画面识别 dish；仅当识别出的菜品存在于下列词表时才填写 "
        f"shot_capabilities，否则输出空数组。词表：{json.dumps(options, ensure_ascii=False)}。"
    )


def _native_video_failure_diagnostic(clip: Clip) -> str:
    """Perform local media inspection only after a native-provider failure.

    This never extracts frames or persists media data.  It distinguishes a
    locally damaged source from a provider/model failure without exposing a
    request body, Base64 payload, or credential.
    """
    path = Path(clip.file_path)
    if not path.is_file():
        return "本地诊断：原始视频文件不存在"
    command = ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    except FileNotFoundError:
        return "本地诊断：未安装 FFmpeg，无法检查原视频"
    except subprocess.TimeoutExpired:
        return "本地诊断：FFmpeg 解码检查超时"
    if completed.returncode == 0:
        return "本地诊断：原视频可完整解码；请检查 MiMo 模型、媒体限制或服务端状态"
    detail = (completed.stderr or completed.stdout).strip().splitlines()
    summary = detail[-1] if detail else "FFmpeg 无法完整解码该原视频"
    return f"本地诊断：原视频解码失败（{summary[:300]}）"


def _parse(content: str, clip: Clip) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise IntelligenceError("模型没有返回有效 JSON") from exc
    if not isinstance(value, dict):
        raise IntelligenceError("模型响应必须是 JSON 对象")
    raw_dishes = value.get("dish")
    if isinstance(raw_dishes, list):
        dishes = [item for item in raw_dishes if isinstance(item, str)]
    elif isinstance(raw_dishes, str):
        dishes = [raw_dishes]
    else:
        dishes = []
    # A reviewed clip already belongs to a known capability catalog.  Keep that
    # label when re-analysing it so a provider's more specific scene name (for
    # example “柠檬水”) cannot silently remove it from the same-dish pool.
    known_dish = _known_clip_dish(clip)
    if known_dish in _shot_capability_catalog():
        dishes = [known_dish]
    duration = clip.duration_seconds or 0
    usable = value.get("usable_range") or {"start": 0, "end": duration}
    try:
        start, end = float(usable.get("start", 0)), float(usable.get("end", duration))
        usable = {"start": max(0, start), "end": min(duration, max(start + 0.1, end))}
    except (TypeError, ValueError):
        usable = {"start": 0, "end": duration}
    role = value.get("segment_role") if value.get("segment_role") in {"head", "middle", "tail"} else "middle"
    raw_commerce_roles = value.get("commerce_roles")
    if not isinstance(raw_commerce_roles, list):
        raw_commerce_roles = []
    commerce_roles = list(dict.fromkeys(
        item for item in raw_commerce_roles if isinstance(item, str) and item in COMMERCE_ROLES
    ))
    raw_capabilities = value.get("shot_capabilities")
    if not isinstance(raw_capabilities, list):
        raw_capabilities = []
    allowed_capabilities = set(_allowed_shot_capabilities(clip, dishes))
    shot_capabilities = list(dict.fromkeys(
        item for item in raw_capabilities if isinstance(item, str) and item in allowed_capabilities
    ))
    return {
        "summary": str(value.get("summary", ""))[:2000], "segment_role": role,
        "tags": {
            **{key: value.get(key, []) for key in ("actions", "visual_hooks", "audio_hooks", "shot_type")},
            "dish": dishes,
            "commerce_roles": commerce_roles,
            "shot_capabilities": shot_capabilities,
        },
        "climax_time": _number(value.get("climax_time"), min(max(duration / 2, 0), duration)),
        "usable_range": usable, "quality_score": _score(value.get("quality_score")), "confidence": _score(value.get("confidence")),
    }


def _number(value: Any, fallback: float) -> float:
    try: return float(value)
    except (TypeError, ValueError): return fallback


def _score(value: Any) -> float:
    try: return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError): return 0.0


async def understand_clip(session: Session, clip_id: str, mode: str = "auto") -> ClipAnalysis:
    clip = session.get(Clip, clip_id)
    if clip is None: raise IntelligenceError("素材不存在")
    config = _default_model(session)
    if config.protocol.lower() == "gemini":
        return await _understand_gemini_video(session, clip, config, mode)
    if config.protocol.lower() == "mimo":
        return await _understand_mimo_video(session, clip, config, mode)
    return await _understand_openai_frames(session, clip, config, mode)


def _system_prompt(session: Session, clip: Clip) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n{_shot_capability_prompt(clip)}"
        f"\n\n项目业务背景（只作标签用途说明，不可视为视频事实）：\n{get_business_context(session)}"
    )


async def _understand_gemini_video(session: Session, clip: Clip, config: ModelConfig, mode: str) -> ClipAnalysis:
    if not config.supports_native_video:
        raise IntelligenceError("当前 Gemini 配置未启用原生视频能力")
    if mode not in {"auto", "native"}:
        raise IntelligenceError("当前 Gemini 配置仅支持 auto 或 native 原生视频理解")
    started = time.perf_counter()
    try:
        mime, encoded, size = _native_video_data(clip, config.max_native_media_bytes)
        request = {
            "system_instruction": {"parts": [{"text": _system_prompt(session, clip)}]},
            "contents": [{"role": "user", "parts": [
                {"text": f"素材时长 {clip.duration_seconds or 0:.2f}s。请直接分析该原始视频及其内嵌音轨。"},
                {"inline_data": {"mime_type": mime, "data": encoded}},
            ]}],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json",
                "responseSchema": GEMINI_RESPONSE_SCHEMA,
            },
        }
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(
                f"{config.base_url.rstrip('/')}/v1beta/models/{config.model_name}:generateContent",
                headers={"Authorization": f"Bearer {decrypt_api_key(config.api_key_encrypted)}", "Content-Type": "application/json"},
                json=request,
            )
        response.raise_for_status()
        parsed = _parse(_gemini_response_text(response.json()), clip)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, IntelligenceError) as exc:
        error = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        diagnostic = _native_video_failure_diagnostic(clip)
        _record_usage(session, config, started, "failed", f"{error}; {diagnostic}")
        session.commit()
        raise IntelligenceError(f"模型理解失败：{error}。{diagnostic}") from exc
    evidence = [{
        "source": "native_video", "mime_type": mime, "file_size_bytes": size,
        "duration_seconds": clip.duration_seconds, "has_audio": clip.has_audio,
    }]
    preserved_status = clip.review_status
    analysis = ClipAnalysis(clip_id=clip.id, model_config_id=config.id, mode="native_video", evidence_frames=evidence, review_status=preserved_status, **parsed)
    clip.import_status = "understood"
    session.add(analysis); _record_usage(session, config, started, "success")
    session.commit(); session.refresh(analysis)
    return analysis


async def _understand_mimo_video(session: Session, clip: Clip, config: ModelConfig, mode: str) -> ClipAnalysis:
    """Send a complete local video to MiMo's OpenAI-compatible video endpoint."""
    if not config.supports_native_video:
        raise IntelligenceError("当前 MiMo 配置未启用原生视频能力")
    if mode not in {"auto", "native"}:
        raise IntelligenceError("当前 MiMo 配置仅支持 auto 或 native 原生视频理解")
    started = time.perf_counter()
    try:
        if Path(clip.file_path).suffix.lower() not in MIMO_VIDEO_SUFFIXES:
            raise IntelligenceError("MiMo 原生视频仅支持 MP4、MOV、AVI 或 WMV 文件")
        mime, encoded, size = _native_video_data(clip, config.max_native_media_bytes)
        video_data_url = f"data:{mime};base64,{encoded}"
        if len(video_data_url.encode("ascii")) > MIMO_MAX_BASE64_VIDEO_BYTES:
            raise IntelligenceError("MiMo Base64 视频请求超过 50 MB 上限")
        content: list[dict[str, Any]] = [
            {"type": "text", "text": f"素材时长 {clip.duration_seconds or 0:.2f}s。请直接分析该原始视频及其内嵌音轨。"},
            {
                "type": "video_url",
                "video_url": {"url": video_data_url},
                "fps": 2,
                "media_resolution": "default",
            },
        ]
        request: dict[str, Any] = {
            "model": config.model_name,
            "messages": [
                {"role": "system", "content": _system_prompt(session, clip)},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
        }
        if config.supports_structured_json:
            request["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(
                f"{config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {decrypt_api_key(config.api_key_encrypted)}", "Content-Type": "application/json"},
                json=request,
            )
        response.raise_for_status()
        payload = response.json()
        raw = payload["choices"][0]["message"]["content"]
        if not isinstance(raw, str):
            raise IntelligenceError("MiMo 未返回文本结果")
        parsed = _parse(raw, clip)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, IntelligenceError) as exc:
        error = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        diagnostic = _native_video_failure_diagnostic(clip)
        _record_usage(session, config, started, "failed", f"{error}; {diagnostic}")
        session.commit()
        raise IntelligenceError(f"模型理解失败：{error}。{diagnostic}") from exc
    evidence = [{
        "source": "mimo_native_video", "mime_type": mime, "file_size_bytes": size,
        "duration_seconds": clip.duration_seconds, "has_audio": clip.has_audio,
    }]
    preserved_status = clip.review_status
    analysis = ClipAnalysis(clip_id=clip.id, model_config_id=config.id, mode="native_video", evidence_frames=evidence, review_status=preserved_status, **parsed)
    clip.import_status = "understood"
    session.add(analysis); _record_usage(session, config, started, "success")
    session.commit(); session.refresh(analysis)
    return analysis


async def _understand_openai_frames(session: Session, clip: Clip, config: ModelConfig, mode: str) -> ClipAnalysis:
    effective_mode = "adaptive_frames"
    frames = _evidence_frames(clip, config.max_frames_per_video)
    if not frames: raise IntelligenceError("关键帧尚未生成，请等待媒体任务完成")
    content: list[dict[str, Any]] = [{"type": "text", "text": f"素材时长 {clip.duration_seconds or 0:.2f}s。按时间顺序分析以下关键帧。"}]
    for frame in frames:
        content.append({"type": "text", "text": f"时间戳：{frame.get('time', 0)} 秒"})
        content.append({"type": "image_url", "image_url": {"url": _image_data(frame["path"]), "detail": "low"}})
    # Native video formats are provider-specific. For OpenAI-compatible APIs the
    # portable default is evidence images; 'auto' records this transparent fallback.
    if mode == "native" and not config.supports_native_video:
        raise IntelligenceError("当前模型未启用原生视频能力")
    request = {"model": config.model_name, "messages": [{"role": "system", "content": _system_prompt(session, clip)}, {"role": "user", "content": content}], "temperature": 0.1}
    if config.supports_structured_json: request["response_format"] = {"type": "json_object"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(f"{config.base_url.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {decrypt_api_key(config.api_key_encrypted)}", "Content-Type": "application/json"}, json=request)
        response.raise_for_status()
        payload = response.json(); raw = payload["choices"][0]["message"]["content"]
        parsed = _parse(raw, clip)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, IntelligenceError) as exc:
        error = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
        _record_usage(session, config, started, "failed", error)
        session.commit()
        raise IntelligenceError(f"模型理解失败：{error}") from exc
    preserved_status = clip.review_status
    analysis = ClipAnalysis(clip_id=clip.id, model_config_id=config.id, mode=effective_mode, evidence_frames=frames, review_status=preserved_status, **parsed)
    clip.import_status = "understood"
    session.add(analysis); _record_usage(session, config, started, "success")
    session.commit(); session.refresh(analysis)
    return analysis
