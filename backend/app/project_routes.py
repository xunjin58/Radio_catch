"""Project-wide, non-secret settings used by the local media workflow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .database import get_session
from .models import ProjectSettings
from .schemas import ProjectSettingsResponse, ProjectSettingsUpdate


DEFAULT_BUSINESS_CONTEXT = """你正在为一名销售新鲜柠檬的商家标注短视频素材。后续用途是从已审核片段拼接 20–60 秒的竖版商品展示视频。
仅依据视频画面、可听见的音频和画面内文字标注事实；不得推断或编造产地、价格、甜度、农残、新鲜度等不可验证卖点。看不清是否为柠檬时，不要将其标为柠檬。
为每段素材补充可用于混剪的 commerce_roles：hook（视觉或声音上的开场吸引）、product_proof（可见的产品或品质展示）、usage（可见的食用或使用场景）、cta（画面或音频中明确出现的行动引导）。只选择有证据支持的角色。"""

router = APIRouter(prefix="/api/project-settings", tags=["project-settings"])


def get_business_context(session: Session) -> str:
    """Return the saved context, or the safe lemon-seller default for new projects."""
    settings = session.get(ProjectSettings, 1)
    if settings is None or not settings.business_context.strip():
        return DEFAULT_BUSINESS_CONTEXT
    return settings.business_context


@router.get("", response_model=ProjectSettingsResponse)
def get_project_settings(session: Session = Depends(get_session)) -> ProjectSettingsResponse:
    return ProjectSettingsResponse(business_context=get_business_context(session))


@router.patch("", response_model=ProjectSettingsResponse)
def update_project_settings(
    payload: ProjectSettingsUpdate, session: Session = Depends(get_session)
) -> ProjectSettingsResponse:
    context = payload.business_context.strip()
    if not context:
        raise HTTPException(status_code=422, detail="business_context 不能为空")
    settings = session.get(ProjectSettings, 1)
    if settings is None:
        settings = ProjectSettings(id=1, business_context=context)
        session.add(settings)
    else:
        settings.business_context = context
    session.commit()
    return ProjectSettingsResponse(business_context=settings.business_context)
