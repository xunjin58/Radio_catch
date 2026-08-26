"""HTTP API for clip review, controlled renders and performance learning."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_session
from .models import Clip, Experiment, Job, PlatformMetric, Render
from .remix_planning import RemixPlanningError, plan_remix
from .workflow import (
    WorkflowError,
    analyze_patterns,
    create_render_thumbnail,
    create_experiment,
    import_metrics_csv,
    recommend_next_experiments,
    render_thumbnail_path,
    review_clip,
    run_render,
    serialize_clip,
    update_clip_metadata,
)


router = APIRouter(prefix="/api", tags=["workflow"])
EXPORT_DIR = Path(os.getenv("RADIO_CATCH_EXPORT_DIR", Path(__file__).resolve().parents[1] / "data" / "exports"))


class ClipReviewRequest(BaseModel):
    status: Literal["approved", "rejected", "needs_review", "pending"]
    updates: dict[str, Any] = Field(default_factory=dict)


class ClipMetadataUpdateRequest(BaseModel):
    summary: Optional[str] = Field(default=None, max_length=2000)
    dish: Optional[str] = Field(default=None, max_length=120)
    segment_role: Optional[Literal["head", "middle", "tail"]] = None
    climax_time: Optional[float] = Field(default=None, ge=0)
    usable_range: Optional[dict[str, float]] = None
    tags: Optional[dict[str, Any]] = None


class TimelineSegment(BaseModel):
    clip_id: str
    start: Optional[float] = Field(default=None, ge=0)
    end: Optional[float] = Field(default=None, gt=0)
    speed: float = Field(default=1, ge=0.25, le=4)


class RenderVariant(BaseModel):
    name: Optional[str] = Field(default=None, max_length=300)
    clips: list[TimelineSegment] = Field(min_length=1)
    values: dict[str, Any] = Field(default_factory=dict)


class ExperimentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dish: str = Field(min_length=1, max_length=120)
    target_duration_seconds: float = Field(default=12.0, ge=9.5, le=15.5)
    generation_count: int = Field(default=1, ge=1, le=100)
    variables: dict[str, Any] = Field(default_factory=dict)
    notes: Optional[str] = None
    status: str = "draft"
    variants: list[RenderVariant] = Field(min_length=1, max_length=100)


class RemixPlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    dish: str = Field(min_length=1, max_length=120)
    requested_count: int = Field(ge=1, le=10)
    target_duration_seconds: float = Field(default=12.0, ge=9.5, le=15.5)


def _error(exc: WorkflowError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


def _serialize_render(render: Render) -> dict[str, Any]:
    return {
        "id": render.id,
        "video_id": render.video_id,
        "experiment_id": render.experiment_id,
        "dish": render.dish,
        "title": render.title,
        "status": render.status,
        "output_path": render.output_path,
        "duration_seconds": render.duration_seconds,
        "width": render.width,
        "height": render.height,
        "edit_decision_list": render.edit_decision_list,
        "experiment_values": render.experiment_values,
        "published_at": render.published_at,
        "created_at": render.created_at,
        "updated_at": render.updated_at,
    }


def _serialize_experiment(experiment: Experiment) -> dict[str, Any]:
    return {
        "id": experiment.id,
        "name": experiment.name,
        "dish": experiment.dish,
        "target_duration_seconds": experiment.target_duration_seconds,
        "generation_count": experiment.generation_count,
        "experiment_ratio": experiment.experiment_ratio,
        "variables": experiment.variables,
        "status": experiment.status,
        "notes": experiment.notes,
        "created_at": experiment.created_at,
        "updated_at": experiment.updated_at,
        "render_count": len(experiment.renders),
    }


def _serialize_job(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "task_type": job.task_type,
        "status": job.status,
        "progress": job.progress,
        "payload": job.payload,
        "result": job.result,
        "error_message": job.error_message,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


@router.get("/dashboard")
def dashboard(session: Session = Depends(get_session)) -> dict[str, int]:
    clips = session.scalars(select(Clip)).all()
    renders = session.scalars(select(Render)).all()
    metrics = session.scalars(select(PlatformMetric)).all()
    return {
        "pending_understanding": sum(clip.import_status in {"imported", "queued"} for clip in clips),
        "pending_review": sum(clip.review_status in {"pending", "needs_review"} for clip in clips),
        "rendering": sum(render.status in {"queued", "rendering"} for render in renders),
        "pending_publish": sum(render.status == "completed" and render.published_at is None for render in renders),
        "pending_metrics": sum(render.published_at is not None and not render.metrics for render in renders),
        "metric_rows": len(metrics),
        "failed_renders": sum(render.status == "failed" for render in renders),
    }


@router.get("/clips")
def list_clips(
    dish: Optional[str] = None,
    review_status: Optional[str] = None,
    query: Optional[str] = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    clips = session.scalars(select(Clip).order_by(Clip.created_at.desc())).all()
    output = [serialize_clip(clip) for clip in clips]
    if dish:
        output = [item for item in output if item.get("dish") == dish]
    if review_status:
        output = [item for item in output if item["review_status"] == review_status]
    if query:
        needle = query.lower()
        output = [item for item in output if needle in str(item).lower()]
    return output


@router.get("/clips/{clip_id}")
def get_clip(clip_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    clip = session.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    return serialize_clip(clip)


@router.patch("/clips/{clip_id}/review")
def update_clip_review(
    clip_id: str, payload: ClipReviewRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        return serialize_clip(review_clip(session, clip_id, payload.status, payload.updates))
    except WorkflowError as exc:
        if str(exc) == "素材不存在":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise _error(exc) from exc


@router.patch("/clips/{clip_id}/metadata")
def update_clip_metadata_endpoint(
    clip_id: str, payload: ClipMetadataUpdateRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        return serialize_clip(update_clip_metadata(session, clip_id, payload.model_dump(exclude_unset=True)))
    except WorkflowError as exc:
        if str(exc) == "素材不存在":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise _error(exc) from exc


@router.get("/experiments")
def list_experiments(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return [_serialize_experiment(row) for row in session.scalars(select(Experiment).order_by(Experiment.created_at.desc())).all()]


@router.post("/experiments", status_code=status.HTTP_201_CREATED)
def create_experiment_endpoint(
    payload: ExperimentCreateRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        raw = payload.model_dump()
        raw["variants"] = [
            {**variant, "clips": [clip for clip in variant["clips"]]}
            for variant in raw["variants"]
        ]
        experiment, renders = create_experiment(session, raw)
        return {"experiment": _serialize_experiment(experiment), "renders": [_serialize_render(row) for row in renders]}
    except WorkflowError as exc:
        raise _error(exc) from exc


@router.post("/remix-plans")
async def create_remix_plan_endpoint(
    payload: RemixPlanRequest, session: Session = Depends(get_session)
) -> dict[str, Any]:
    try:
        return await plan_remix(
            session, dish=payload.dish, requested_count=payload.requested_count,
            target_duration_seconds=payload.target_duration_seconds,
        )
    except RemixPlanningError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/renders")
def list_renders(
    experiment_id: Optional[str] = None, status_filter: Optional[str] = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = session.scalars(select(Render).order_by(Render.created_at.desc())).all()
    if experiment_id:
        rows = [row for row in rows if row.experiment_id == experiment_id]
    if status_filter:
        rows = [row for row in rows if row.status == status_filter]
    return [_serialize_render(row) for row in rows]


@router.get("/renders/{render_id}")
def get_render(render_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    render = session.get(Render, render_id)
    if render is None:
        raise HTTPException(status_code=404, detail="成片不存在")
    return _serialize_render(render)


def _completed_render_output(render: Render) -> Path:
    """Resolve a completed export while keeping file access scoped locally."""
    if render.status != "completed" or not render.output_path:
        raise HTTPException(status_code=409, detail="成片尚未导出完成")
    output = Path(render.output_path).resolve()
    export_root = EXPORT_DIR.resolve()
    if export_root not in output.parents or not output.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    return output


def _get_completed_render(render_id: str, session: Session) -> tuple[Render, Path]:
    render = session.get(Render, render_id)
    if render is None:
        raise HTTPException(status_code=404, detail="成片不存在")
    return render, _completed_render_output(render)


@router.get("/renders/{render_id}/video")
def play_render(render_id: str, session: Session = Depends(get_session)) -> FileResponse:
    """Stream a completed local render for the in-app player."""
    _, output = _get_completed_render(render_id, session)
    return FileResponse(output, media_type="video/mp4")


@router.get("/renders/{render_id}/thumbnail")
def render_thumbnail(render_id: str, session: Session = Depends(get_session)) -> FileResponse:
    """Return a derived cover image, backfilling legacy finished renders."""
    render, output = _get_completed_render(render_id, session)
    thumbnail = render_thumbnail_path(output).resolve()
    export_root = EXPORT_DIR.resolve()
    if export_root not in thumbnail.parents:
        raise HTTPException(status_code=404, detail="成片封面不可用")
    if not thumbnail.is_file():
        try:
            thumbnail = create_render_thumbnail(output, render.duration_seconds).resolve()
        except Exception as exc:
            raise HTTPException(status_code=404, detail="成片封面不可用") from exc
    if not thumbnail.is_file():
        raise HTTPException(status_code=404, detail="成片封面不可用")
    return FileResponse(thumbnail, media_type="image/jpeg")


@router.get("/renders/{render_id}/download")
def download_render(render_id: str, session: Session = Depends(get_session)) -> FileResponse:
    """Download a completed local render without exposing arbitrary file paths."""
    render, output = _get_completed_render(render_id, session)
    return FileResponse(output, media_type="video/mp4", filename=f"{render.video_id}.mp4")


@router.get("/jobs")
def list_jobs(
    task_type: Optional[str] = None, status_filter: Optional[str] = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    jobs = session.scalars(select(Job).order_by(Job.created_at.desc())).all()
    if task_type:
        jobs = [job for job in jobs if job.task_type == task_type]
    if status_filter:
        jobs = [job for job in jobs if job.status == status_filter]
    return [_serialize_job(job) for job in jobs]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _serialize_job(job)


@router.post("/renders/{render_id}/run")
def render_now(render_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Run one queued FFmpeg render synchronously (safe for a single-user V1)."""
    try:
        return _serialize_render(run_render(session, render_id, EXPORT_DIR))
    except WorkflowError as exc:
        if str(exc) == "成片不存在":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise _error(exc) from exc


@router.post("/metrics/import")
async def import_metrics(
    file: UploadFile = File(...), session: Session = Depends(get_session)
) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="仅支持 CSV 数据文件")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件不能超过 20MB")
    try:
        return import_metrics_csv(session, content, file.filename or "metrics.csv")
    except WorkflowError as exc:
        raise _error(exc) from exc


@router.get("/analysis/patterns")
def patterns(session: Session = Depends(get_session)) -> dict[str, Any]:
    return analyze_patterns(session)


@router.get("/analysis/recommendations")
def recommendations(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"recommendations": recommend_next_experiments(session)}
