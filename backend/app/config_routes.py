"""Safe model-configuration endpoints, including connection diagnostics."""

from __future__ import annotations

import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_session
from .models import ModelConfig, ModelTaskAssignment
from .schemas import (
    AssignModelRequest,
    AssignmentResponse,
    HealthResponse,
    ModelConfigCreate,
    ModelConfigResponse,
    ModelConfigUpdate,
    TestConnectionRequest,
    TestConnectionResponse,
)
from .security import decrypt_api_key, encrypt_api_key, mask_api_key


router = APIRouter(prefix="/api/model-configs", tags=["model-configs"])
health_router = APIRouter(prefix="/api", tags=["system"])


@health_router.get("/health", response_model=HealthResponse)
def health_check(session: Session = Depends(get_session)) -> HealthResponse:
    """Read-only liveness check that also verifies SQLite is reachable."""
    try:
        session.execute(select(1))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return HealthResponse()


def _serialize_config(config: ModelConfig) -> ModelConfigResponse:
    try:
        api_key_masked = mask_api_key(decrypt_api_key(config.api_key_encrypted))
        credential_error = None
    except RuntimeError:
        # A deployment secret may have changed since this local record was
        # created. Keep the record visible without exposing or attempting to
        # recover its credential, so a user can replace the key deliberately.
        api_key_masked = "密钥需要重新保存"
        credential_error = "已保存的 API Key 无法解密，请重新保存该配置的密钥。"
    return ModelConfigResponse(
        id=config.id,
        name=config.name,
        provider=config.provider,
        protocol=config.protocol,
        base_url=config.base_url,
        api_key_masked=api_key_masked,
        model_name=config.model_name,
        supports_images=config.supports_images,
        supports_native_video=config.supports_native_video,
        supports_structured_json=config.supports_structured_json,
        timeout_seconds=config.timeout_seconds,
        max_concurrency=config.max_concurrency,
        daily_budget=config.daily_budget,
        max_frames_per_video=config.max_frames_per_video,
        max_native_media_bytes=config.max_native_media_bytes,
        is_default=config.is_default,
        is_active=config.is_active,
        last_error=credential_error or config.last_error,
        last_tested_at=config.last_tested_at,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


def _get_config_or_404(session: Session, config_id: str) -> ModelConfig:
    config = session.get(ModelConfig, config_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    return config


@router.get("", response_model=list[ModelConfigResponse])
def list_model_configs(session: Session = Depends(get_session)) -> list[ModelConfigResponse]:
    configs = session.scalars(select(ModelConfig).order_by(ModelConfig.created_at.desc())).all()
    return [_serialize_config(config) for config in configs]


@router.post("", response_model=ModelConfigResponse, status_code=status.HTTP_201_CREATED)
def create_model_config(payload: ModelConfigCreate, session: Session = Depends(get_session)) -> ModelConfigResponse:
    config = ModelConfig(
        **payload.model_dump(exclude={"api_key"}), api_key_encrypted=encrypt_api_key(payload.api_key)
    )
    if payload.is_default:
        session.execute(update(ModelConfig).values(is_default=False))
    session.add(config)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A model configuration with this name already exists") from exc
    session.refresh(config)
    return _serialize_config(config)


@router.get("/assignments/all", response_model=list[AssignmentResponse])
def list_assignments(session: Session = Depends(get_session)) -> list[ModelTaskAssignment]:
    return session.scalars(select(ModelTaskAssignment).order_by(ModelTaskAssignment.task_type)).all()


@router.put("/assignments", response_model=AssignmentResponse)
def assign_model(payload: AssignModelRequest, session: Session = Depends(get_session)) -> ModelTaskAssignment:
    config = _get_config_or_404(session, payload.model_config_id)
    if not config.is_active:
        raise HTTPException(status_code=409, detail="Cannot assign an inactive model configuration")
    assignment = session.scalar(
        select(ModelTaskAssignment).where(ModelTaskAssignment.task_type == payload.task_type)
    )
    if assignment is None:
        assignment = ModelTaskAssignment(task_type=payload.task_type, model_config_id=config.id)
        session.add(assignment)
    else:
        assignment.model_config_id = config.id
    session.commit()
    session.refresh(assignment)
    return assignment


@router.get("/{config_id}", response_model=ModelConfigResponse)
def get_model_config(config_id: str, session: Session = Depends(get_session)) -> ModelConfigResponse:
    return _serialize_config(_get_config_or_404(session, config_id))


@router.patch("/{config_id}", response_model=ModelConfigResponse)
def update_model_config(
    config_id: str, payload: ModelConfigUpdate, session: Session = Depends(get_session)
) -> ModelConfigResponse:
    config = _get_config_or_404(session, config_id)
    changes = payload.model_dump(exclude_unset=True, exclude={"api_key"})
    for field, value in changes.items():
        setattr(config, field, value)
    if payload.api_key is not None:
        config.api_key_encrypted = encrypt_api_key(payload.api_key)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A model configuration with this name already exists") from exc
    session.refresh(config)
    return _serialize_config(config)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_config(config_id: str, session: Session = Depends(get_session)) -> Response:
    config = _get_config_or_404(session, config_id)
    session.delete(config)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{config_id}/default", response_model=ModelConfigResponse)
def set_default_model_config(config_id: str, session: Session = Depends(get_session)) -> ModelConfigResponse:
    config = _get_config_or_404(session, config_id)
    session.execute(update(ModelConfig).values(is_default=False))
    config.is_default = True
    session.commit()
    session.refresh(config)
    return _serialize_config(config)


def _safe_error_detail(response: httpx.Response) -> str:
    # Provider bodies can contain echo'ed request headers; keep diagnostics concise.
    return f"Provider returned HTTP {response.status_code}"


@router.post("/{config_id}/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    config_id: str, payload: TestConnectionRequest, session: Session = Depends(get_session)
) -> TestConnectionResponse:
    config = _get_config_or_404(session, config_id)
    try:
        api_key = decrypt_api_key(config.api_key_encrypted)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail="Saved API key cannot be decrypted; save a replacement key before testing the connection",
        ) from exc
    timeout = payload.timeout_seconds or config.timeout_seconds
    base_url = config.base_url.rstrip("/")
    protocol = config.protocol.lower()
    endpoint = f"{base_url}/v1/models" if protocol == "gemini" else f"{base_url}/models"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(endpoint, headers={"Authorization": f"Bearer {api_key}"})
        elapsed = round((time.perf_counter() - started) * 1000)
        if response.is_success:
            config.last_error = None
            config.last_tested_at = datetime.utcnow()
            session.commit()
            detail = (
                "Model-list connection succeeded; native media inference is not tested"
                if protocol in {"gemini", "mimo"} else "Connection succeeded"
            )
            return TestConnectionResponse(ok=True, status_code=response.status_code, latency_ms=elapsed, detail=detail)
        detail = _safe_error_detail(response)
        config.last_error = detail
        config.last_tested_at = datetime.utcnow()
        session.commit()
        return TestConnectionResponse(ok=False, status_code=response.status_code, latency_ms=elapsed, detail=detail)
    except (httpx.HTTPError, ValueError) as exc:
        elapsed = round((time.perf_counter() - started) * 1000)
        detail = f"Connection failed: {type(exc).__name__}"
        config.last_error = detail
        config.last_tested_at = datetime.utcnow()
        session.commit()
        return TestConnectionResponse(ok=False, latency_ms=elapsed, detail=detail)
