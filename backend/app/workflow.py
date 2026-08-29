"""Business workflows for review, rendering, metric import and learning loops.

The functions in this module deliberately keep orchestration separate from HTTP.
They work with the SQLAlchemy entities defined in :mod:`app.models` and only use
small, explicit JSON payloads so every rendered video remains reproducible.
"""

from __future__ import annotations

import csv
import io
import json
import math
import shutil
import subprocess
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


class WorkflowError(ValueError):
    """An input is valid JSON but cannot be processed by a workflow."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _models() -> Any:
    # Imported lazily to keep utility functions importable during migrations and
    # to avoid a circular import from routes -> workflow -> models.
    from . import models

    return models


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _set(obj: Any, value: Any, *names: str) -> None:
    """Set the first model attribute that exists (models evolved during V1)."""
    for name in names:
        if hasattr(obj, name):
            setattr(obj, name, value)
            return


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _json_value(obj: Any, value: Any, *names: str) -> None:
    """Store JSON correctly for either a JSON or legacy text model column."""
    for name in names:
        if not hasattr(obj, name):
            continue
        # SQLAlchemy exposes the column type through the mapper; default to the
        # native object, which is right for JSON columns in the V1 schema.
        try:
            column = obj.__table__.columns[name]
            if "JSON" not in column.type.__class__.__name__.upper():
                setattr(obj, name, json.dumps(value, ensure_ascii=False))
            else:
                setattr(obj, name, value)
        except Exception:  # pragma: no cover - supports lightweight test fakes
            setattr(obj, name, value)
        return


def _all(session: Any, model: Any) -> list[Any]:
    return list(session.query(model).all())


def _by_id(session: Any, model: Any, entity_id: Any) -> Any | None:
    try:
        return session.get(model, entity_id)
    except Exception:
        return session.query(model).filter(model.id == entity_id).first()


def _commit(session: Any) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def _clip_path(clip: Any) -> str | None:
    value = _get(clip, "file_path", "path", "storage_path", "source_path")
    return str(value) if value else None


def _clip_meta(clip: Any) -> dict[str, Any]:
    analysis = _latest_analysis(clip)
    if analysis:
        # Return a fresh mapping so review edits are detected by SQLAlchemy's
        # plain JSON column instead of mutating its existing value in place.
        return dict(_as_dict(_get(analysis, "tags", "analysis", "analysis_json", "metadata", default={})))
    return dict(_as_dict(_get(clip, "analysis", "analysis_json", "metadata", "tags", default={})))


def _analysis_updated_at(analysis: Any) -> datetime:
    """Provide a SQLite-safe ordering key for historical and new analyses."""
    value = _get(analysis, "updated_at", "created_at", default=datetime.min)
    if not isinstance(value, datetime):
        return datetime.min
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _latest_analysis(clip: Any) -> Any | None:
    analyses = list(_get(clip, "analyses", default=[]) or [])
    if not analyses:
        return None
    return max(analyses, key=_analysis_updated_at)


def _clip_role(clip: Any) -> str:
    analysis = _latest_analysis(clip)
    return str(_get(analysis, "segment_role", "role", default=_get(clip, "segment_role", "role", default=_clip_meta(clip).get("segment_role", "middle"))))


def _clip_dish(clip: Any) -> str | None:
    dish = _get(clip, "dish", "dish_name", default=_clip_meta(clip).get("dish"))
    if isinstance(dish, list):
        return str(dish[0]) if dish else None
    return str(dish) if dish else None


def serialize_clip(clip: Any) -> dict[str, Any]:
    meta = _clip_meta(clip)
    analysis = _latest_analysis(clip)
    return {
        "clip_id": str(_get(clip, "clip_id", "id")),
        "id": _get(clip, "id"),
        "filename": _get(clip, "filename", "original_filename", "original_name", "name"),
        "path": _clip_path(clip),
        "duration_seconds": _get(clip, "duration_seconds"),
        "segment_role": _clip_role(clip),
        "dish": _clip_dish(clip),
        "review_status": _get(clip, "review_status", "status", default="pending"),
        "summary": _get(analysis, "summary", default=_get(clip, "summary", default=meta.get("summary"))),
        "tags": meta,
        "climax_time": _get(analysis, "climax_time", default=_get(clip, "climax_time", default=meta.get("climax_time"))),
        "quality_score": _get(analysis, "quality_score", default=_get(clip, "quality_score", default=meta.get("quality_score"))),
        "confidence": _get(analysis, "confidence", default=_get(clip, "confidence", default=meta.get("confidence"))),
        "usable_range": _get(analysis, "usable_range", default=_get(clip, "usable_range", default=meta.get("usable_range"))),
        "created_at": _get(clip, "created_at"),
    }


def review_clip(session: Any, clip_id: Any, status: str, updates: dict[str, Any] | None = None) -> Any:
    """Approve, reject, or amend AI clip understanding while retaining evidence."""
    if status not in {"approved", "rejected", "needs_review", "pending"}:
        raise WorkflowError("status 必须是 approved、rejected、needs_review 或 pending")
    clip = _by_id(session, _models().Clip, clip_id)
    if not clip:
        raise WorkflowError("素材不存在")
    updates = updates or {}
    _set(clip, status, "review_status")
    analysis = _latest_analysis(clip)
    if analysis is None:
        ClipAnalysis = getattr(_models(), "ClipAnalysis", None)
        if ClipAnalysis is not None:
            analysis = ClipAnalysis()
            _set(analysis, _get(clip, "id"), "clip_id")
            if hasattr(clip, "analyses"):
                clip.analyses.append(analysis)
            session.add(analysis)
    _set(clip, utcnow(), "reviewed_at", "updated_at")
    if "summary" in updates:
        _set(analysis or clip, str(updates["summary"]), "summary")
    if "segment_role" in updates:
        role = str(updates["segment_role"])
        if role not in {"head", "middle", "tail"}:
            raise WorkflowError("segment_role 必须是 head、middle 或 tail")
        _set(analysis or clip, role, "segment_role", "role")
    if "dish" in updates:
        # Dish is a semantic tag in the V1 ClipAnalysis schema.
        target = analysis or clip
        tags = _clip_meta(clip)
        tags["dish"] = updates["dish"]
        _json_value(target, tags, "tags", "analysis", "analysis_json", "metadata")
    if "usable_range" in updates:
        usable = updates["usable_range"]
        if not isinstance(usable, dict) or float(usable.get("end", -1)) <= float(usable.get("start", 0)):
            raise WorkflowError("usable_range 需要有效的 start 和 end")
        _json_value(analysis or clip, usable, "usable_range")
    tag_updates = updates.get("tags")
    if tag_updates is not None:
        if not isinstance(tag_updates, dict):
            raise WorkflowError("tags 必须是对象")
        merged = _clip_meta(clip)
        merged.update(tag_updates)
        _json_value(analysis or clip, merged, "tags", "analysis", "analysis_json", "metadata")
    if analysis is not None:
        _set(analysis, status, "review_status")
    _commit(session)
    return clip


def update_clip_metadata(session: Any, clip_id: Any, updates: dict[str, Any]) -> Any:
    """Amend the latest analysis without changing its review decision.

    Unlike :func:`review_clip`, this is deliberately status-neutral so the
    material-library inspector can correct model output without approving,
    rejecting, or re-queuing the source clip.
    """
    clip = _by_id(session, _models().Clip, clip_id)
    if not clip:
        raise WorkflowError("素材不存在")
    if not updates:
        return clip

    analysis = _latest_analysis(clip)
    if analysis is None:
        ClipAnalysis = getattr(_models(), "ClipAnalysis", None)
        if ClipAnalysis is not None:
            analysis = ClipAnalysis()
            _set(analysis, _get(clip, "id"), "clip_id")
            _set(analysis, _get(clip, "review_status", default="pending"), "review_status")
            if hasattr(clip, "analyses"):
                clip.analyses.append(analysis)
            session.add(analysis)

    target = analysis or clip
    if "summary" in updates:
        summary = updates["summary"]
        if summary is not None and not isinstance(summary, str):
            raise WorkflowError("summary 必须是字符串或 null")
        _set(target, summary, "summary")
    if "segment_role" in updates:
        role = updates["segment_role"]
        if role not in {"head", "middle", "tail"}:
            raise WorkflowError("segment_role 必须是 head、middle 或 tail")
        _set(target, role, "segment_role", "role")
    if "climax_time" in updates:
        climax_time = updates["climax_time"]
        if climax_time is not None:
            try:
                climax_time = float(climax_time)
            except (TypeError, ValueError) as exc:
                raise WorkflowError("climax_time 必须是非负秒数或 null") from exc
            if not math.isfinite(climax_time) or climax_time < 0:
                raise WorkflowError("climax_time 必须是非负秒数或 null")
            duration = _get(clip, "duration_seconds")
            if duration is not None and climax_time > float(duration):
                raise WorkflowError("climax_time 不能超过素材时长")
        _set(target, climax_time, "climax_time")
    if "usable_range" in updates:
        usable = updates["usable_range"]
        if usable is not None:
            if not isinstance(usable, dict) or float(usable.get("end", -1)) <= float(usable.get("start", 0)):
                raise WorkflowError("usable_range 需要有效的 start 和 end")
            usable = {"start": float(usable["start"]), "end": float(usable["end"])}
        _json_value(target, usable, "usable_range")

    existing_tags = _clip_meta(clip)
    tags = existing_tags
    if "tags" in updates:
        tag_value = updates["tags"]
        if not isinstance(tag_value, dict):
            raise WorkflowError("tags 必须是对象")
        # Metadata edits replace the full JSON payload so deleted keys stay
        # deleted; review updates intentionally retain their merge semantics.
        tags = dict(tag_value)
        # The thumbnail is local media plumbing rather than a semantic label;
        # keep it when the user replaces the editable label document.
        if existing_tags.get("thumbnail_path"):
            tags["thumbnail_path"] = existing_tags["thumbnail_path"]
    if "dish" in updates:
        dish = updates["dish"]
        if dish is not None and not isinstance(dish, str):
            raise WorkflowError("dish 必须是字符串或 null")
        if dish and dish.strip():
            tags["dish"] = dish.strip()
        else:
            tags.pop("dish", None)
    if "tags" in updates or "dish" in updates:
        _json_value(target, tags, "tags", "analysis", "analysis_json", "metadata")

    _set(target, utcnow(), "updated_at")
    _commit(session)
    return clip


def _new_job(session: Any, job_type: str, payload: dict[str, Any], title: str) -> Any | None:
    Job = getattr(_models(), "Job", None)
    if Job is None:
        return None
    job = Job()
    _set(job, job_type, "task_type", "job_type", "type", "kind")
    _set(job, title, "title", "name")
    _set(job, "queued", "status")
    _set(job, 0, "progress")
    _json_value(job, payload, "payload", "payload_json", "input_data")
    _set(job, utcnow(), "created_at")
    session.add(job)
    return job


def job_for_render(session: Any, render_id: Any) -> Any | None:
    """Find the durable queue record without duplicating job_id on Render."""
    Job = getattr(_models(), "Job", None)
    if Job is None:
        return None
    for job in _all(session, Job):
        payload = _as_dict(_get(job, "payload", "payload_json", default={}))
        if str(payload.get("render_id")) == str(render_id):
            return job
    return None


def create_experiment(session: Any, data: dict[str, Any]) -> Any:
    """Create a controlled experiment and its initial reproducible render specs."""
    Models = _models()
    name = str(data.get("name") or "").strip()
    dish = str(data.get("dish") or "").strip()
    variants = data.get("variants") or []
    if not name or not dish:
        raise WorkflowError("实验名称和菜品不能为空")
    if not isinstance(variants, list) or not variants:
        raise WorkflowError("至少提供一个 variants 成片组合")

    experiment = Models.Experiment()
    _set(experiment, name, "name", "title")
    _set(experiment, dish, "dish", "dish_name")
    _set(experiment, str(data.get("status") or "draft"), "status")
    _json_value(experiment, data.get("variables") or {}, "variables", "variables_json", "experiment_variables")
    _set(experiment, data.get("notes"), "notes")
    _set(experiment, float(data.get("target_duration_seconds") or 22), "target_duration_seconds")
    _set(experiment, int(data.get("generation_count") or len(variants)), "generation_count")
    _set(experiment, utcnow(), "created_at")
    session.add(experiment)
    session.flush()

    renders: list[Any] = []
    for index, variant in enumerate(variants, start=1):
        manifest = validate_manifest(session, dish, variant.get("clips") if isinstance(variant, dict) else variant)
        render = Models.Render()
        _set(render, experiment.id, "experiment_id")
        _set(render, dish, "dish", "dish_name")
        _set(render, f"RC-{uuid.uuid4().hex[:12]}", "video_id")
        _set(render, variant.get("name", f"{name}-{index:02d}") if isinstance(variant, dict) else f"{name}-{index:02d}", "name", "title")
        _set(render, "queued", "status")
        _json_value(render, manifest, "edit_decision_list", "manifest", "timeline", "clip_manifest", "recipe")
        _json_value(render, (variant.get("values") or {}) if isinstance(variant, dict) else {}, "experiment_values")
        _set(render, sum((row["end"] - row["start"]) / row["speed"] for row in manifest), "duration_seconds")
        _set(render, utcnow(), "created_at")
        session.add(render)
        session.flush()
        job = _new_job(session, "render", {"render_id": render.id, "manifest": manifest}, f"导出成片 {index}")
        if job:
            _set(render, job.id, "job_id")
        renders.append(render)
    _commit(session)
    return experiment, renders


def validate_manifest(session: Any, dish: str, entries: Any) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not entries:
        raise WorkflowError("成片需要至少一个素材片段")
    normalized: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict) or item.get("clip_id") is None:
            raise WorkflowError("每个片段必须包含 clip_id")
        clip = _by_id(session, _models().Clip, item["clip_id"])
        if not clip:
            raise WorkflowError(f"素材 {item['clip_id']} 不存在")
        if _get(clip, "review_status", default="approved") != "approved":
            raise WorkflowError(f"素材 {item['clip_id']} 尚未审核通过")
        clip_dish = _clip_dish(clip)
        if clip_dish and clip_dish != dish:
            raise WorkflowError(f"素材 {item['clip_id']} 属于“{clip_dish}”，不能混入“{dish}”")
        path = _clip_path(clip)
        if not path or not Path(path).is_file():
            raise WorkflowError(f"素材 {item['clip_id']} 的文件不存在")
        try:
            end = float(_get(clip, "duration_seconds"))
        except (TypeError, ValueError):
            raise WorkflowError(f"素材 {item['clip_id']} 缺少有效时长，无法整段拼接")
        if end <= 0:
            raise WorkflowError(f"素材 {item['clip_id']} 缺少有效时长，无法整段拼接")
        normalized.append({
            # Full source only: callers cannot select a subrange or alter speed.
            "clip_id": _get(clip, "id"), "source_path": path, "start": 0.0, "end": end,
            "speed": 1.0, "role": _clip_role(clip), "dish": dish,
        })
    duration = sum((row["end"] - row["start"]) / row["speed"] for row in normalized)
    if not 20.0 <= duration <= 60.0:
        raise WorkflowError(f"成片时长为 {duration:.1f} 秒，应控制在 20–60 秒")
    return normalized


def _render_manifest(render: Any) -> list[dict[str, Any]]:
    return _as_list(_get(render, "edit_decision_list", "manifest", "timeline", "clip_manifest", "recipe", default=[]))


def render_thumbnail_path(output_path: str | Path) -> Path:
    """Return the local, derived cover path for an exported render."""
    return Path(output_path).with_suffix(".thumbnail.jpg")


def create_render_thumbnail(output_path: str | Path, duration_seconds: float | None = None) -> Path:
    """Extract a representative JPEG from a finished render with FFmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise WorkflowError("未找到 ffmpeg，无法生成成片封面")
    output = Path(output_path).expanduser().resolve()
    if not output.is_file():
        raise WorkflowError("导出文件不存在，无法生成成片封面")
    thumbnail = render_thumbnail_path(output).resolve()
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    timestamp = max(0.0, float(duration_seconds or 0) * 0.15)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{timestamp:.3f}", "-i", str(output),
        "-frames:v", "1", "-q:v", "3", str(thumbnail),
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode or not thumbnail.is_file() or thumbnail.stat().st_size == 0:
        raise WorkflowError("无法从成片生成封面")
    return thumbnail


def render_with_ffmpeg(
    manifest: list[dict[str, Any]], output_path: str | Path, *, progress: Callable[[int], None] | None = None
) -> Path:
    """Render a vertical, hard-cut H.264/AAC MP4 from complete source clips.

    FFmpeg runs without a shell. Audio is intentionally a silent AAC track in V1:
    it makes mixed source audio deterministic and lets a later voice-over pass
    replace it without re-encoding the timeline.
    """
    if not manifest:
        raise WorkflowError("空时间线无法导出")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise WorkflowError("未找到 ffmpeg，请安装后重试")
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    filters: list[str] = []
    for i, segment in enumerate(manifest):
        path = Path(str(segment["source_path"])).expanduser()
        if not path.is_file():
            raise WorkflowError(f"源文件不存在：{path}")
        # Never seek, limit duration, or change speed: every input is whole.
        cmd.extend(["-i", str(path)])
        chain = (
            f"[{i}:v]setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,format=yuv420p[v"
            f"{i}]"
        )
        filters.append(chain)
    concat_inputs = "".join(f"[v{i}]" for i in range(len(manifest)))
    filters.append(f"{concat_inputs}concat=n={len(manifest)}:v=1:a=0[vout]")
    # A generated AAC track avoids concat failures from clips with incompatible
    # source audio layouts and still produces a platform-compatible MP4.
    cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
    cmd.extend([
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", f"{len(manifest)}:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", str(output),
    ])
    if progress:
        progress(10)
    completed = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise WorkflowError(f"FFmpeg 导出失败：{completed.stderr[-1200:]}")
    if not output.is_file() or output.stat().st_size == 0:
        raise WorkflowError("FFmpeg 未生成有效文件")
    if progress:
        progress(100)
    return output


def run_render(session: Any, render_id: Any, output_dir: str | Path) -> Any:
    """Run one queued render. Safe to call from a worker rather than a request."""
    render = _by_id(session, _models().Render, render_id)
    if not render:
        raise WorkflowError("成片不存在")
    manifest = _render_manifest(render)
    if not manifest:
        raise WorkflowError("成片缺少剪辑清单")
    job = job_for_render(session, _get(render, "id"))
    _set(render, "rendering", "status")
    if job:
        _set(job, "running", "status")
        _set(job, utcnow(), "started_at")
    _commit(session)

    filename = f"{_get(render, 'video_id', 'id') or 'render'}.mp4"
    target = Path(output_dir) / filename
    try:
        output = render_with_ffmpeg(manifest, target, progress=lambda value: _set(job, value, "progress") if job else None)
        # The cover is derived data: a failed frame extraction must not discard
        # an otherwise valid export, and historical exports are backfilled when
        # their thumbnail endpoint is first requested.
        try:
            create_render_thumbnail(output, _get(render, "duration_seconds"))
        except Exception:
            pass
        _set(render, str(output), "output_path", "file_path", "path")
        _set(render, "completed", "status")
        _set(render, utcnow(), "rendered_at", "completed_at")
        if job:
            _set(job, "completed", "status")
            _set(job, 100, "progress")
            _set(job, utcnow(), "completed_at")
    except Exception as exc:
        _set(render, "failed", "status")
        _set(render, str(exc), "error", "error_message")
        if job:
            _set(job, "failed", "status")
            _set(job, str(exc), "error", "error_message")
            _set(job, utcnow(), "completed_at")
        _commit(session)
        raise
    _commit(session)
    return render


_METRIC_ALIASES = {
    "video_id": ("video_id", "视频id", "视频ID", "作品id", "作品ID"),
    "views": ("views", "播放量", "播放次数"),
    "retention_2s": ("retention_2s", "2秒留存", "2秒留存率"),
    "retention_5s": ("retention_5s", "5秒留存", "5秒留存率"),
    "avg_watch_seconds": ("avg_watch_seconds", "平均观看时长", "平均播放时长"),
    "completion_rate": ("completion_rate", "完播率"),
    "likes": ("likes", "点赞", "点赞数"),
    "comments": ("comments", "评论", "评论数"),
    "saves": ("saves", "收藏", "收藏数"),
    "shares": ("shares", "分享", "分享数"),
    "published_at": ("published_at", "发布时间", "发布日"),
    "observed_at": ("observed_at", "数据观察时间", "统计时间"),
    "observation_hours": ("observation_hours", "观察小时数", "观察时长"),
}


def _canonical_row(row: dict[str, Any]) -> dict[str, Any]:
    folded = {str(k).strip().lower(): v for k, v in row.items() if k}
    result: dict[str, Any] = {}
    for canonical, aliases in _METRIC_ALIASES.items():
        for alias in aliases:
            if alias.lower() in folded and folded[alias.lower()] not in (None, ""):
                result[canonical] = folded[alias.lower()]
                break
    return result


def _number(value: Any, *, rate: bool = False) -> float | int | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "")
    try:
        if text.endswith("%"):
            number = float(text[:-1]) / 100
        else:
            number = float(text)
            if rate and number > 1:
                number /= 100
        return number
    except ValueError:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for candidate in (text, text.replace("/", "-")):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass
    return None


def _match_metric(session: Any, video_id: str, hours: int | None) -> Any | None:
    Metric = _models().PlatformMetric
    render = _render_by_video_id(session, video_id)
    if render is None:
        return None
    for metric in _all(session, Metric):
        same_video = str(_get(metric, "render_id", default="")) == str(_get(render, "id"))
        existing_hours = _get(metric, "observation_hours", "hours_after_publish")
        if same_video and (hours is None or str(existing_hours) == str(hours)):
            return metric
    return None


def _render_by_video_id(session: Any, video_id: str) -> Any | None:
    for render in _all(session, _models().Render):
        if str(_get(render, "video_id", "platform_video_id", "id")) == video_id:
            return render
    return None


def import_metrics_csv(session: Any, content: bytes, filename: str = "metrics.csv") -> dict[str, Any]:
    """Import a Douyin export and upsert rows by video_id + observation window."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("gb18030")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise WorkflowError("CSV 缺少表头")
    inserted = updated = skipped = 0
    errors: list[dict[str, Any]] = []
    numeric_fields = {"views", "avg_watch_seconds", "likes", "comments", "saves", "shares"}
    rate_fields = {"retention_2s", "retention_5s", "completion_rate"}
    for line, raw in enumerate(reader, start=2):
        row = _canonical_row(raw)
        video_id = str(row.get("video_id") or "").strip()
        if not video_id:
            errors.append({"line": line, "error": "缺少 video_id"})
            skipped += 1
            continue
        hours_raw = _number(row.get("observation_hours"))
        hours = int(hours_raw) if hours_raw is not None else None
        render = _render_by_video_id(session, video_id)
        if render is None:
            errors.append({"line": line, "error": f"video_id {video_id} 未关联本系统成片"})
            skipped += 1
            continue
        metric = _match_metric(session, video_id, hours)
        if metric is None:
            metric = _models().PlatformMetric()
            _set(metric, video_id, "video_id", "platform_video_id")
            _set(metric, _get(render, "id"), "render_id")
            _set(metric, _get(render, "experiment_id"), "experiment_id")
            session.add(metric)
            inserted += 1
        else:
            updated += 1
        for field in numeric_fields:
            if field in row:
                value = _number(row[field])
                if value is not None:
                    target = {"avg_watch_seconds": "average_watch_seconds", "saves": "favorites"}.get(field, field)
                    _set(metric, value, target, field)
        for field in rate_fields:
            if field in row:
                value = _number(row[field], rate=True)
                if value is not None:
                    _set(metric, value, field)
        if hours is not None:
            _set(metric, hours, "observation_hours", "hours_after_publish")
        if "observed_at" in row:
            observed_at = _parse_datetime(row["observed_at"])
            if observed_at is None:
                errors.append({"line": line, "error": "数据观察时间格式无法识别，已采用导入时间"})
            else:
                _set(metric, observed_at, "observed_at")
        if "published_at" in row:
            published_at = _parse_datetime(row["published_at"])
            if published_at is not None:
                _set(render, published_at, "published_at")
        _set(metric, filename, "source_file", "import_source")
        _json_value(metric, raw, "source_row")
        _set(metric, utcnow(), "updated_at", "imported_at")
    _commit(session)
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "errors": errors}


def _rate(value: Any) -> float | None:
    number = _number(value, rate=True)
    return float(number) if number is not None else None


def _analysis_observations(session: Any) -> list[tuple[Any, Any, Any]]:
    renders = {str(_get(row, "id")): row for row in _all(session, _models().Render)}
    observations: list[tuple[Any, Any, Any]] = []
    # Prefer 72-hour records, and ignore low-sample publications by default.
    best: dict[str, Any] = {}
    for metric in _all(session, _models().PlatformMetric):
        render = renders.get(str(_get(metric, "render_id"))) or _render_by_video_id(session, str(_get(metric, "video_id", default="")))
        if not render:
            continue
        key = str(_get(render, "id"))
        current_hours = _get(metric, "observation_hours", "hours_after_publish", default=0) or 0
        existing = best.get(key)
        if existing is None or abs(float(current_hours) - 72) < abs(float(_get(existing, "observation_hours", "hours_after_publish", default=0) or 0) - 72):
            best[key] = metric
    for render_id, metric in best.items():
        views = _number(_get(metric, "views", default=0)) or 0
        if views < 500:
            continue
        completion = _rate(_get(metric, "completion_rate"))
        retention5 = _rate(_get(metric, "retention_5s"))
        retention2 = _rate(_get(metric, "retention_2s"))
        score_values = [x for x in (completion, retention5, retention2) if x is not None]
        if not score_values:
            continue
        observations.append((renders[render_id], metric, sum(score_values) / len(score_values)))
    return observations


def analyze_patterns(session: Any) -> dict[str, Any]:
    """Return repeatable associations, never an unsupported 'viral formula'."""
    observations = _analysis_observations(session)
    if not observations:
        return {"sample_size": 0, "patterns": [], "global_score": None, "message": "暂无满足 72 小时且播放量 ≥500 的数据"}
    global_score = sum(score for _, _, score in observations) / len(observations)
    buckets: dict[tuple[str, str], list[tuple[float, Any]]] = defaultdict(list)
    for render, _metric, score in observations:
        for segment in _render_manifest(render):
            clip = _by_id(session, _models().Clip, segment.get("clip_id"))
            if not clip:
                continue
            meta = _clip_meta(clip)
            role = _clip_role(clip)
            buckets[(f"{role}部动作", str(meta.get("actions", [])))].append((score, render))
            buckets[(f"{role}部视觉", str(meta.get("visual_hooks", [])))].append((score, render))
            buckets[("菜品", str(_clip_dish(clip) or "未标注"))].append((score, render))
    patterns = []
    for (dimension, value), records in buckets.items():
        if value in {"[]", "", "None"}:
            continue
        count = len(records)
        average = sum(row[0] for row in records) / count
        experiments = {str(_get(row[1], "experiment_id", "id")) for row in records}
        lift = (average / global_score - 1) if global_score else 0
        status = "已验证规律" if count >= 3 and len(experiments) >= 3 else "候选规律"
        confidence = min(0.95, 0.35 + 0.12 * count + 0.08 * len(experiments))
        patterns.append({
            "dimension": dimension, "value": value, "sample_size": count,
            "experiment_count": len(experiments), "average_score": round(average, 4),
            "relative_lift": round(lift, 4), "confidence": round(confidence, 2), "status": status,
        })
    patterns.sort(key=lambda row: (row["status"] == "已验证规律", row["relative_lift"], row["sample_size"]), reverse=True)
    return {"sample_size": len(observations), "global_score": round(global_score, 4), "patterns": patterns}


def recommend_next_experiments(session: Any) -> list[dict[str, Any]]:
    analysis = analyze_patterns(session)
    patterns = analysis.get("patterns", [])
    positive = [row for row in patterns if row["relative_lift"] > 0][:3]
    negative = [row for row in patterns if row["relative_lift"] < 0][:2]
    recommendations: list[dict[str, Any]] = []
    for row in positive:
        recommendations.append({
            "type": "controlled", "priority": "high" if row["status"] == "已验证规律" else "medium",
            "suggestion": f"保持其他条件不变，复测 {row['dimension']}「{row['value']}」",
            "reason": f"相对基线提升 {row['relative_lift']:.0%}，样本 {row['sample_size']} 条",
            "variable": row["dimension"], "candidate": row["value"],
        })
    for row in negative:
        recommendations.append({
            "type": "controlled", "priority": "medium",
            "suggestion": f"用替代素材对照 {row['dimension']}「{row['value']}」",
            "reason": f"当前相对基线 {row['relative_lift']:.0%}，需要排除混杂因素",
            "variable": row["dimension"], "candidate": row["value"],
        })
    recommendations.append({
        "type": "exploration", "priority": "normal",
        "suggestion": "保留约 20% 成片测试未出现过的头部视觉钩子组合",
        "reason": "探索预算用于发现新的候选规律，不纳入已验证结论",
    })
    return recommendations
