"""Local, FFmpeg based media ingestion and frame extraction services.

This module deliberately contains no application-specific SQLAlchemy imports.  The
application supplies a small repository (``SQLAlchemyClipRepository`` is included
below) so the media pipeline can be started before the final Clip schema settles.
All generated paths are local paths rooted under ``storage_root``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Optional, Protocol

logger = logging.getLogger(__name__)


class MediaProcessingError(RuntimeError):
    """A media operation failed in a way that can be presented to an API user."""


class FFmpegUnavailable(MediaProcessingError):
    """Raised when ffmpeg or ffprobe cannot be found on the host."""


class ClipRepository(Protocol):
    """Minimal persistence contract used by :class:`LocalMediaService`."""

    def find_by_checksum(self, checksum: str) -> Any | None: ...

    def create_clip(self, values: dict[str, Any]) -> Any: ...

    def update_clip(self, clip: Any, values: dict[str, Any]) -> Any: ...


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def object_value(obj: Any, *names: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    table = getattr(obj, "__table__", None)
    columns = set(table.columns.keys()) if table is not None else None
    for name in names:
        # SQLAlchemy's declarative base exposes ``metadata`` on every model;
        # that is framework state, not a persisted media_metadata value.
        if columns is not None and name not in columns:
            continue
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def public_clip(clip: Any) -> dict[str, Any]:
    """Return a conservative serialisable view for either model or dict records."""
    result = {
        "id": object_value(clip, "id", "clip_id"),
        "checksum": object_value(clip, "checksum", "sha256", "file_checksum"),
        "original_filename": object_value(clip, "original_filename", "filename", "name"),
        "storage_path": object_value(clip, "storage_path", "file_path", "source_path", "path"),
        "status": object_value(clip, "status", "import_status", default="imported"),
        "duration": object_value(clip, "duration", "duration_seconds"),
        "width": object_value(clip, "width"),
        "height": object_value(clip, "height"),
        "fps": object_value(clip, "fps"),
        "has_audio": object_value(clip, "has_audio"),
        "file_size_bytes": object_value(clip, "file_size_bytes", "size_bytes"),
        "thumbnail_path": object_value(clip, "thumbnail_path"),
        "keyframes": object_value(clip, "keyframes", "keyframe_data"),
        "metadata": object_value(clip, "metadata", "media_metadata"),
    }
    return {key: value for key, value in result.items() if value is not None}


class SQLAlchemyClipRepository:
    """Best-effort adapter for the application's SQLAlchemy ``Clip`` model.

    It only writes columns actually present on the passed model.  This supports
    modest schema naming differences (``file_path`` vs ``storage_path``) while
    still giving V1 a DB-backed record rather than an in-memory upload list.
    """

    _aliases: dict[str, tuple[str, ...]] = {
        "checksum": ("checksum", "sha256", "file_checksum"),
        "original_filename": ("original_filename", "filename", "name"),
        "storage_path": ("storage_path", "file_path", "source_path", "path"),
        "status": ("status", "import_status"),
        "duration": ("duration", "duration_seconds"),
        "width": ("width",),
        "height": ("height",),
        "fps": ("fps",),
        "has_audio": ("has_audio",),
        "file_size_bytes": ("file_size_bytes", "size_bytes"),
        "orientation": ("orientation", "rotation"),
        "thumbnail_path": ("thumbnail_path",),
        "keyframes": ("keyframes", "keyframe_data"),
        "metadata": ("metadata", "media_metadata"),
        "error_message": ("error_message", "processing_error"),
    }

    def __init__(self, session: Any, clip_model: type[Any]):
        self.session = session
        self.clip_model = clip_model
        table = getattr(clip_model, "__table__", None)
        self.columns = set(table.columns.keys()) if table is not None else set()

    def _field(self, canonical: str) -> str | None:
        return next((name for name in self._aliases[canonical] if name in self.columns), None)

    def _translated(self, values: dict[str, Any]) -> dict[str, Any]:
        translated: dict[str, Any] = {}
        for key, value in values.items():
            field = self._field(key) if key in self._aliases else (key if key in self.columns else None)
            if field:
                translated[field] = value
        return translated

    def find_by_checksum(self, checksum: str) -> Any | None:
        field = self._field("checksum")
        if not field:
            return None
        return self.session.query(self.clip_model).filter(getattr(self.clip_model, field) == checksum).first()

    def get_by_id(self, clip_id: str | int) -> Any | None:
        return self.session.get(self.clip_model, clip_id)

    def worker_copy(self) -> "SQLAlchemyClipRepository":
        """Open an independent SQLAlchemy session for a background thread."""
        session_class = type(self.session)
        return SQLAlchemyClipRepository(session_class(bind=self.session.get_bind()), self.clip_model)

    def close(self) -> None:
        self.session.close()

    def create_clip(self, values: dict[str, Any]) -> Any:
        clip = self.clip_model(**self._translated(values))
        self.session.add(clip)
        self.session.commit()
        self.session.refresh(clip)
        return clip

    def update_clip(self, clip: Any, values: dict[str, Any]) -> Any:
        for field, value in self._translated(values).items():
            setattr(clip, field, value)
        self.session.add(clip)
        self.session.commit()
        self.session.refresh(clip)
        return clip

    def save_evidence_frames(self, clip: Any, frames: list[dict[str, Any]], thumbnail_path: str | None = None) -> None:
        """Persist generated frame evidence when the Clip has an analysis relation.

        The core schema keeps model output in ``ClipAnalysis`` rather than adding
        transient extraction columns to Clip.  Relationship introspection lets
        this adapter preserve evidence without hard-importing a specific model.
        """
        relationship = getattr(type(clip), "analyses", None)
        mapper = getattr(getattr(relationship, "property", None), "mapper", None)
        analysis_model = getattr(mapper, "class_", None)
        if analysis_model is None:
            return
        tags = {"thumbnail_path": thumbnail_path} if thumbnail_path else {}
        analysis = analysis_model(
            clip_id=object_value(clip, "id"), mode="adaptive_frames",
            evidence_frames=frames, tags=tags,
        )
        self.session.add(analysis)
        self.session.commit()


@dataclass
class MediaJob:
    id: str
    clip_id: str | int | None
    kind: str = "media_process"
    state: str = "queued"
    progress: int = 0
    message: str = "Waiting to start"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=utcnow)
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MediaJobService:
    """Small local task queue with observable state; suitable for one-user V1."""

    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max(1, max_workers), thread_name_prefix="media")
        self._jobs: dict[str, MediaJob] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._lock = threading.Lock()

    def submit(self, clip_id: str | int | None, work: Callable[[Callable[[int, str], None]], dict[str, Any]]) -> MediaJob:
        job = MediaJob(id=str(uuid.uuid4()), clip_id=clip_id)
        with self._lock:
            self._jobs[job.id] = job
        self._futures[job.id] = self._executor.submit(self._run, job.id, work)
        return job

    def _run(self, job_id: str, work: Callable[[Callable[[int, str], None]], dict[str, Any]]) -> None:
        self._update(job_id, state="running", progress=2, message="Reading media", started_at=utcnow())
        try:
            result = work(lambda progress, message: self._update(job_id, progress=progress, message=message))
            self._update(job_id, state="succeeded", progress=100, message="Complete", result=result, completed_at=utcnow())
        except Exception as exc:  # errors must not kill the worker or other jobs
            logger.exception("Media job %s failed", job_id)
            self._update(job_id, state="failed", message="Processing failed", error=str(exc), completed_at=utcnow())

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in values.items():
                setattr(job, key, value)

    def get(self, job_id: str) -> MediaJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)


class LocalMediaService:
    """Persists uploads, probes video properties and creates audit-friendly frames."""

    def __init__(
        self,
        storage_root: str | Path,
        repository: ClipRepository,
        *,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
        max_upload_bytes: int = 4 * 1024 * 1024 * 1024,
    ):
        self.root = Path(storage_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = repository
        self.ffmpeg_bin = ffmpeg_bin
        self.ffprobe_bin = ffprobe_bin
        self.max_upload_bytes = max_upload_bytes

    def check_binaries(self) -> dict[str, str]:
        versions: dict[str, str] = {}
        for binary in (self.ffmpeg_bin, self.ffprobe_bin):
            found = shutil.which(binary) if os.path.sep not in binary else binary
            if not found:
                raise FFmpegUnavailable(f"Required executable '{binary}' was not found. Install FFmpeg and restart the server.")
            try:
                completed = subprocess.run([found, "-version"], capture_output=True, text=True, timeout=10, check=True)
            except (OSError, subprocess.SubprocessError) as exc:
                raise FFmpegUnavailable(f"Unable to run '{binary}': {exc}") from exc
            versions[binary] = (completed.stdout or completed.stderr).splitlines()[0]
        return versions

    def import_upload(self, source: BinaryIO, original_filename: str, content_type: str | None = None) -> tuple[Any, bool]:
        """Copy an uploaded stream atomically and return ``(clip, was_duplicate)``."""
        safe_name = Path(original_filename or "upload.mp4").name
        suffix = Path(safe_name).suffix.lower() or ".mp4"
        staging = self.root / ".staging"
        staging.mkdir(exist_ok=True)
        temporary = staging / f"{uuid.uuid4().hex}{suffix}.part"
        digest = hashlib.sha256()
        bytes_written = 0
        try:
            with temporary.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    bytes_written += len(chunk)
                    if bytes_written > self.max_upload_bytes:
                        raise MediaProcessingError(f"Upload exceeds the {self.max_upload_bytes // (1024 * 1024)} MB limit.")
                    digest.update(chunk)
                    target.write(chunk)
            if bytes_written == 0:
                raise MediaProcessingError("The uploaded file is empty.")
            checksum = digest.hexdigest()
            duplicate = self.repository.find_by_checksum(checksum)
            if duplicate is not None:
                return duplicate, True
            destination = self.root / "clips" / checksum[:2] / f"{checksum}{suffix}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, destination)
            try:
                metadata = self.probe(destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            record = self.repository.create_clip({
                "checksum": checksum,
                "original_filename": safe_name,
                "storage_path": str(destination),
                "status": "imported",
                "duration": metadata["duration"],
                "width": metadata["width"],
                "height": metadata["height"],
                "fps": metadata["fps"],
                "has_audio": metadata["has_audio"],
                "file_size_bytes": bytes_written,
                "orientation": str(metadata["rotation"]),
                "metadata": metadata,
            })
            return record, False
        finally:
            temporary.unlink(missing_ok=True)

    def _run(self, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True)
        except FileNotFoundError as exc:
            raise FFmpegUnavailable(f"Required executable '{args[0]}' was not found. Install FFmpeg and restart the server.") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaProcessingError("Media processing timed out.") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "FFmpeg returned an unknown error").strip()
            raise MediaProcessingError(detail[-1500:]) from exc

    def probe(self, source_path: str | Path) -> dict[str, Any]:
        path = Path(source_path)
        if not path.is_file():
            raise MediaProcessingError("The local media file no longer exists.")
        output = self._run([self.ffprobe_bin, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=60)
        try:
            payload = json.loads(output.stdout)
        except json.JSONDecodeError as exc:
            raise MediaProcessingError("ffprobe returned invalid metadata.") from exc
        streams = payload.get("streams", [])
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        if video is None:
            raise MediaProcessingError("The upload does not contain a video stream.")
        duration_value = video.get("duration") or payload.get("format", {}).get("duration") or 0
        try:
            duration = round(float(duration_value), 3)
        except (ValueError, TypeError):
            duration = 0.0
        fps = self._frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        rotation = self._rotation(video)
        width, height = int(video.get("width", 0)), int(video.get("height", 0))
        if rotation in (90, 270):
            width, height = height, width
        return {
            "duration": duration,
            "fps": fps,
            "width": width,
            "height": height,
            "rotation": rotation,
            "codec": video.get("codec_name"),
            "format": payload.get("format", {}).get("format_name"),
            "size_bytes": int(payload.get("format", {}).get("size", 0) or 0),
            "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
        }

    @staticmethod
    def _frame_rate(value: Any) -> float:
        try:
            numerator, denominator = str(value).split("/", 1)
            return round(float(numerator) / float(denominator), 3) if float(denominator) else 0.0
        except (ValueError, ZeroDivisionError):
            return 0.0

    @staticmethod
    def _rotation(stream: dict[str, Any]) -> int:
        rotation = (stream.get("tags") or {}).get("rotate")
        if rotation is None:
            rotation = next((item.get("rotation") for item in stream.get("side_data_list", []) if "rotation" in item), 0)
        try:
            return int(float(rotation)) % 360
        except (ValueError, TypeError):
            return 0

    def process_clip(self, clip: Any, progress: Callable[[int, str], None] | None = None, max_frames: int = 12) -> dict[str, Any]:
        """Generate a thumbnail and adaptive keyframe evidence, then update the Clip."""
        report = progress or (lambda _p, _m: None)
        source = object_value(clip, "storage_path", "file_path", "source_path", "path")
        if not source:
            raise MediaProcessingError("Clip has no persisted storage path.")
        source_path = Path(source)
        metadata = self.probe(source_path)
        checksum = object_value(clip, "checksum", "sha256", "file_checksum") or hashlib.sha256(str(source_path).encode()).hexdigest()
        asset_dir = self.root / "derived" / checksum
        asset_dir.mkdir(parents=True, exist_ok=True)
        report(15, "Creating thumbnail")
        # PNG avoids platform-dependent MJPEG encoder failures and preserves a
        # clean, lossless frame for later model evidence/review.
        thumbnail = asset_dir / "thumbnail.png"
        thumbnail_time = min(max(metadata["duration"] * 0.15, 0), self._last_decodable_time(metadata["duration"]))
        self._extract_frame(source_path, thumbnail_time, thumbnail)
        report(35, "Selecting adaptive evidence frames")
        times = self.adaptive_timestamps(source_path, metadata["duration"], max_frames=max_frames)
        frames: list[dict[str, Any]] = []
        for index, timestamp in enumerate(times, start=1):
            destination = asset_dir / f"frame_{index:02d}.png"
            self._extract_frame(source_path, timestamp, destination)
            frames.append({"time": round(timestamp, 3), "path": str(destination)})
            report(35 + int(index / max(1, len(times)) * 55), f"Extracting evidence frame {index}/{len(times)}")
        values = {"status": "ready", "thumbnail_path": str(thumbnail), "keyframes": frames, "metadata": metadata, "error_message": None}
        updated = self.repository.update_clip(clip, values)
        save_evidence = getattr(self.repository, "save_evidence_frames", None)
        if callable(save_evidence):
            save_evidence(updated, frames, str(thumbnail))
        report(96, "Saving results")
        response = public_clip(updated)
        response["keyframes"] = frames
        return response

    def worker_copy(self) -> "LocalMediaService":
        """Make a service whose DB session is safe to use inside a worker thread."""
        copier = getattr(self.repository, "worker_copy", None)
        repository = copier() if callable(copier) else self.repository
        return LocalMediaService(
            self.root, repository, ffmpeg_bin=self.ffmpeg_bin,
            ffprobe_bin=self.ffprobe_bin, max_upload_bytes=self.max_upload_bytes,
        )

    def get_clip(self, clip_id: str | int) -> Any | None:
        getter = getattr(self.repository, "get_by_id", None)
        return getter(clip_id) if callable(getter) else None

    def close(self) -> None:
        closer = getattr(self.repository, "close", None)
        if callable(closer):
            closer()

    def _extract_frame(self, source: Path, timestamp: float, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._run([self.ffmpeg_bin, "-y", "-ss", f"{max(0, timestamp):.3f}", "-i", str(source), "-frames:v", "1", str(destination)], timeout=90)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise MediaProcessingError("FFmpeg did not produce an image frame.")

    def adaptive_timestamps(self, source: Path, duration: float, max_frames: int = 12) -> list[float]:
        """Use start/end, uniform coverage and FFmpeg scene-change candidates.

        This is intentionally deterministic: a later review can reproduce exactly
        why a particular evidence set was sent to the model.
        """
        if duration <= 0:
            return [0.0]
        limit = max(3, min(max_frames, 48))
        uniform_count = min(7, limit)
        times = {0.0, self._last_decodable_time(duration)}
        for index in range(1, uniform_count - 1):
            times.add(duration * index / (uniform_count - 1))
        # showinfo prints pts_time for the frames selected by scene score.  A
        # non-zero ffmpeg exit is non-fatal here: uniform coverage remains valid.
        try:
            run = self._run([self.ffmpeg_bin, "-hide_banner", "-i", str(source), "-vf", "select='gt(scene,0.30)',showinfo", "-an", "-f", "null", "-"], timeout=150)
            candidates = [float(value) for value in re.findall(r"pts_time:([0-9.+-]+)", run.stderr)]
            candidates = sorted(set(t for t in candidates if 0 < t < duration))
            # spread the available scene changes evenly instead of spending all
            # evidence frames on one rapid burst.
            remaining = max(0, limit - len(times))
            if remaining and candidates:
                stride = max(1, len(candidates) // remaining)
                times.update(candidates[::stride][:remaining])
        except MediaProcessingError as exc:
            logger.info("Scene detection skipped for %s: %s", source.name, exc)
        selected = sorted(times)
        # Keep temporal diversity and honor the cap even with many scene changes.
        if len(selected) > limit:
            selected = [selected[round(i * (len(selected) - 1) / (limit - 1))] for i in range(limit)]
        return [round(value, 3) for value in selected]

    @staticmethod
    def _last_decodable_time(duration: float) -> float:
        """Keep away from a container's nominal tail where no frame may exist."""
        return max(0.0, duration - max(0.1, min(0.5, duration * 0.1)))
