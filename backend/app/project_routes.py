"""Project-wide, non-secret settings used by the local media workflow."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .database import get_session
from .models import ProjectSettings
from .schemas import ProjectSettingsResponse, ProjectSettingsUpdate


DEFAULT_BUSINESS_CONTEXT = """你正在为柠檬商品相关的短视频素材做视觉事实标注。

仅依据视频画面、可听见的音频和画面内文字记录事实。商家背景只用于理解标签用途，不能作为产地、价格、甜度、农残、新鲜度、口感或功效等信息的依据；看不清是否为柠檬时，不要标注为柠檬。

为每段素材补充仅有证据支持的 commerce_roles：
- hook：可作为开场吸引注意力的明确视觉、声音或文字信号；
- product_proof：可见的商品展示、包装、规格或可直接观察的品质细节；
- usage：可见的食用、制作或使用场景；
- cta：画面或音频中明确出现的行动引导。

同时标注可见的 shot_capabilities，供后续将已确认文案中的画面事实与素材匹配使用。不要生成或推断口播文案、商品卖点。
"""

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
