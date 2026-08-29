"""Persistent entities for the local-first video workflow."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.utcnow()


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class ProjectSettings(Timestamped, Base):
    """Single local workspace setting record, kept separate from model credentials."""

    __tablename__ = "project_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    business_context: Mapped[str] = mapped_column(Text, nullable=False)


class ModelConfig(Timestamped, Base):
    __tablename__ = "model_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(80), default="OpenAI-compatible", nullable=False)
    protocol: Mapped[str] = mapped_column(String(40), default="openai", nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    supports_images: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_native_video: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_structured_json: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    daily_budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_frames_per_video: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    max_native_media_bytes: Mapped[int] = mapped_column(Integer, default=104857600, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    assignments: Mapped[list["ModelTaskAssignment"]] = relationship(
        back_populates="model_config", cascade="all, delete-orphan"
    )
    usage_records: Mapped[list["ModelUsage"]] = relationship(back_populates="model_config")


class ModelTaskAssignment(Timestamped, Base):
    """Maps a workflow task type to the model configuration selected by the user."""

    __tablename__ = "model_task_assignments"
    __table_args__ = (UniqueConstraint("task_type", name="uq_model_task_assignment_task_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_config_id: Mapped[str] = mapped_column(ForeignKey("model_configs.id", ondelete="CASCADE"), nullable=False)
    model_config: Mapped[ModelConfig] = relationship(back_populates="assignments")


class ModelUsage(Timestamped, Base):
    __tablename__ = "model_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_config_id: Mapped[str] = mapped_column(ForeignKey("model_configs.id"), nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="success", nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_config: Mapped[ModelConfig] = relationship(back_populates="usage_records")


class Clip(Timestamped, Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    orientation: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    import_status: Mapped[str] = mapped_column(String(32), default="imported", nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    analyses: Mapped[list["ClipAnalysis"]] = relationship(back_populates="clip", cascade="all, delete-orphan")


class ClipAnalysis(Timestamped, Base):
    __tablename__ = "clip_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True)
    model_config_id: Mapped[Optional[str]] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), default="auto", nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    segment_role: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    climax_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    usable_range: Mapped[Optional[dict[str, float]]] = mapped_column(JSON, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_frames: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    clip: Mapped[Clip] = relationship(back_populates="analyses")


class BackgroundTask(Timestamped, Base):
    """Durable task record; a worker implementation can claim queued records safely."""

    __tablename__ = "background_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    model_config_id: Mapped[Optional[str]] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Experiment(Timestamped, Base):
    """A controlled mixing experiment and its declared variable(s)."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    dish: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    target_duration_seconds: Mapped[float] = mapped_column(Float, default=22.0, nullable=False)
    generation_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    experiment_ratio: Mapped[str] = mapped_column(String(32), default="controlled", nullable=False)
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    renders: Mapped[list["Render"]] = relationship(back_populates="experiment")


class Render(Timestamped, Base):
    """An exported or in-progress finished video, with a complete edit decision list."""

    __tablename__ = "renders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    video_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    experiment_id: Mapped[Optional[str]] = mapped_column(ForeignKey("experiments.id", ondelete="SET NULL"), nullable=True, index=True)
    dish: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    output_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[int] = mapped_column(Integer, default=1080, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=1920, nullable=False)
    edit_decision_list: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    experiment_values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    experiment: Mapped[Optional[Experiment]] = relationship(back_populates="renders")
    metrics: Mapped[list["PlatformMetric"]] = relationship(back_populates="render", cascade="all, delete-orphan")


class PlatformMetric(Timestamped, Base):
    """An imported platform observation for one render at one observation window."""

    __tablename__ = "platform_metrics"
    __table_args__ = (
        UniqueConstraint("render_id", "observation_hours", "observed_at", name="uq_metric_observation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    render_id: Mapped[str] = mapped_column(ForeignKey("renders.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), default="douyin", nullable=False)
    observation_hours: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retention_2s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    retention_5s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    average_watch_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    completion_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    likes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    favorites: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_row: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    render: Mapped[Render] = relationship(back_populates="metrics")


# Compatibility name for workflow modules that use the shorter queue term.
Job = BackgroundTask
