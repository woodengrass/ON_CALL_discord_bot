"""
資源方案（resource tier）管理路由，見 design.md H.3「方案管理」、I.5。
"""

from fastapi import APIRouter, Depends, HTTPException

from core import admin_operations, repository
from web.admin.backend.dependencies import get_current_operator
from web.admin.backend.schemas import (
    ResourceTierCreateRequest,
    ResourceTierResponse,
    ResourceTierUpdateRequest,
    TierConfigInput,
)

router = APIRouter(prefix="/api/tiers", tags=["tiers"])


@router.get("", response_model=list[ResourceTierResponse])
async def list_tiers(operator: str = Depends(get_current_operator)) -> list[dict]:
    """
    列出所有資源方案。
    """
    return await repository.list_resource_tiers()


@router.post("", response_model=ResourceTierResponse)
async def create_tier(request: ResourceTierCreateRequest, operator: str = Depends(get_current_operator)) -> dict:
    """
    建立資源方案。
    """
    await admin_operations.create_resource_tier(request.tier_name, request.display_order, request.description)
    return await repository.get_resource_tier(request.tier_name)


@router.put("/{tier_name}", response_model=ResourceTierResponse)
async def update_tier(
    tier_name: str, request: ResourceTierUpdateRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    更新資源方案。
    """
    updated = await admin_operations.update_resource_tier(tier_name, request.display_order, request.description)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到資源方案：{tier_name}")
    return await repository.get_resource_tier(tier_name)


@router.delete("/{tier_name}")
async def delete_tier(tier_name: str, force: bool = False, operator: str = Depends(get_current_operator)) -> dict:
    """
    刪除資源方案，force=true 時強制清理引用。
    """
    deleted = await admin_operations.delete_resource_tier(tier_name, force=force)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"找不到資源方案：{tier_name}")
    return {"deleted": True}


@router.get("/{tier_name}/plugins/{plugin_id}")
async def get_plugin_tier_config(
    tier_name: str, plugin_id: str, operator: str = Depends(get_current_operator)
) -> dict:
    """
    查詢外掛在指定方案下的設定。
    """
    config = await repository.get_plugin_tier_config(plugin_id, tier_name)
    if config is None:
        raise HTTPException(status_code=404, detail=f"找不到方案設定：{plugin_id}@{tier_name}")
    return config


@router.put("/{tier_name}/plugins/{plugin_id}")
async def update_plugin_tier_config(
    tier_name: str, plugin_id: str, request: TierConfigInput, operator: str = Depends(get_current_operator)
) -> dict:
    """
    更新已核准外掛在指定方案下的設定，不需要重新走一次審核。
    """
    await admin_operations.update_plugin_tier_config(plugin_id, tier_name, request.model_dump())
    return await repository.get_plugin_tier_config(plugin_id, tier_name)
