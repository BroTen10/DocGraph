"""系统设置路由（批次 11）。

- GET  /api/settings            全部设置（提示词 + 运行参数）
- PUT  /api/settings            批量更新（value=None 表示重置回默认）
- POST /api/settings/optimize-prompt  LLM 优化单个提示词（返回建议，不落库）
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import settings_service

router = APIRouter(prefix="/api/settings", tags=["系统设置"])


class SettingItem(BaseModel):
    key: str
    value: object | None = None


class SettingsUpdateRequest(BaseModel):
    items: list[SettingItem] = Field(default_factory=list)


class OptimizePromptRequest(BaseModel):
    key: str
    current: str
    instruction: str = ""


@router.get("")
def list_settings(db: Session = Depends(get_db)) -> dict:
    """返回全部可配置项（含内置默认值与 DB 覆盖值）。"""
    return {"settings": settings_service.get_all_settings(db)}


@router.put("")
def update_settings(
    payload: SettingsUpdateRequest,
    db: Session = Depends(get_db),
) -> dict:
    """批量更新；value 为 null 表示重置回默认。"""
    items = [{"key": it.key, "value": it.value} for it in payload.items]
    return {"settings": settings_service.update_settings(db, items)}


@router.post("/optimize-prompt")
def optimize_prompt(
    payload: OptimizePromptRequest,
    db: Session = Depends(get_db),
) -> dict:
    """LLM 生成提示词优化建议（不直接应用）。"""
    return settings_service.optimize_prompt(
        db,
        payload.key,
        payload.current,
        payload.instruction,
    )
