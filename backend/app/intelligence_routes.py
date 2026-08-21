"""HTTP facade for configurable clip understanding."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .database import get_session
from .intelligence import IntelligenceError, understand_clip

router = APIRouter(prefix="/api/clips", tags=["understanding"])

class UnderstandRequest(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|native|adaptive|dense)$")

@router.post("/{clip_id}/analyze")
async def analyze(clip_id: str, payload: UnderstandRequest, session: Session = Depends(get_session)) -> dict:
    try:
        row = await understand_clip(session, clip_id, payload.mode)
    except IntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"id": row.id, "clip_id": row.clip_id, "mode": row.mode, "summary": row.summary, "segment_role": row.segment_role, "tags": row.tags, "confidence": row.confidence, "evidence_frames": row.evidence_frames}
