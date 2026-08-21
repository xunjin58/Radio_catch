"""Request/response models. API keys are intentionally write-only."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ModelTaskType = Literal["clip_understanding", "tag_cleanup", "data_analysis", "copywriting"]


class ModelConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(default="OpenAI-compatible", min_length=1, max_length=80)
    protocol: str = Field(default="openai", min_length=1, max_length=40)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str = Field(min_length=1, max_length=2000, repr=False)
    model_name: str = Field(min_length=1, max_length=160)
    supports_images: bool = False
    supports_native_video: bool = False
    supports_structured_json: bool = True
    timeout_seconds: int = Field(default=60, ge=5, le=900)
    max_concurrency: int = Field(default=2, ge=1, le=32)
    daily_budget: Optional[float] = Field(default=None, ge=0)
    max_frames_per_video: int = Field(default=24, ge=1, le=240)
    max_native_media_bytes: int = Field(default=104857600, ge=1)
    is_default: bool = False


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    provider: Optional[str] = Field(default=None, min_length=1, max_length=80)
    protocol: Optional[str] = Field(default=None, min_length=1, max_length=40)
    base_url: Optional[str] = Field(default=None, min_length=8, max_length=500)
    api_key: Optional[str] = Field(default=None, min_length=1, max_length=2000, repr=False)
    model_name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    supports_images: Optional[bool] = None
    supports_native_video: Optional[bool] = None
    supports_structured_json: Optional[bool] = None
    timeout_seconds: Optional[int] = Field(default=None, ge=5, le=900)
    max_concurrency: Optional[int] = Field(default=None, ge=1, le=32)
    daily_budget: Optional[float] = Field(default=None, ge=0)
    max_frames_per_video: Optional[int] = Field(default=None, ge=1, le=240)
    max_native_media_bytes: Optional[int] = Field(default=None, ge=1)
    is_active: Optional[bool] = None


class ModelConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    provider: str
    protocol: str
    base_url: str
    api_key_masked: str
    model_name: str
    supports_images: bool
    supports_native_video: bool
    supports_structured_json: bool
    timeout_seconds: int
    max_concurrency: int
    daily_budget: Optional[float]
    max_frames_per_video: int
    max_native_media_bytes: int
    is_default: bool
    is_active: bool
    last_error: Optional[str]
    last_tested_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class AssignModelRequest(BaseModel):
    task_type: ModelTaskType
    model_config_id: str


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_type: str
    model_config_id: str
    created_at: datetime
    updated_at: datetime


class TestConnectionRequest(BaseModel):
    timeout_seconds: Optional[int] = Field(default=None, ge=5, le=900)


class TestConnectionResponse(BaseModel):
    ok: bool
    status_code: Optional[int] = None
    latency_ms: int
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    database: Literal["ok"] = "ok"
