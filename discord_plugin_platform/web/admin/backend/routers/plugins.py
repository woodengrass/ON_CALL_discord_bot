"""
外掛審核相關路由，見 design.md H.3「審核流程」。只呼叫 core/admin_operations.py，
不在這裡重寫核准/退回/封鎖的業務邏輯。
"""

from fastapi import APIRouter, Depends, HTTPException

from core import admin_operations, repository
from core.manifest import parse_manifest
from web.admin.backend.dependencies import get_current_operator
from web.admin.backend.schemas import (
    ApprovePluginRequest,
    BanPluginRequest,
    PluginDetail,
    PluginSummary,
    PricingTierRequest,
    RejectPluginRequest,
)

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.get("", response_model=list[PluginSummary])
async def list_plugins(status: str | None = None, operator: str = Depends(get_current_operator)) -> list[dict]:
    """
    列出外掛，可依狀態篩選（例如 status=pending_review）。
    """
    return await repository.list_plugins(status)


@router.get("/{plugin_id}", response_model=PluginDetail)
async def get_plugin_detail(plugin_id: str, operator: str = Depends(get_current_operator)) -> dict:
    """
    取得單一外掛詳情，包含 manifest 完整內容、要求的能力清單與原始碼。
    """
    plugin = await repository.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    manifest_json = await repository.get_plugin_manifest(plugin_id, plugin["latest_version"])
    source_code = await repository.get_plugin_source(plugin_id, plugin["latest_version"])
    if manifest_json is None or source_code is None:
        raise HTTPException(status_code=404, detail=f"找不到外掛版本內容：{plugin_id}@{plugin['latest_version']}")
    manifest = parse_manifest(manifest_json)
    return {
        **plugin,
        "manifest_json": manifest_json,
        "source_code": source_code,
        "required_capabilities": manifest.required_capabilities,
    }


@router.post("/{plugin_id}/approve", response_model=PluginSummary)
async def approve_plugin(
    plugin_id: str, request: ApprovePluginRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    核准外掛版本，須逐方案填資源設定，見 design.md I.9。
    """
    tier_configs = {tier_name: config.model_dump() for tier_name, config in request.tier_configs.items()}
    updated = await admin_operations.approve_plugin_version(plugin_id, tier_configs)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    return await repository.get_plugin(plugin_id)


@router.post("/{plugin_id}/reject", response_model=PluginSummary)
async def reject_plugin(
    plugin_id: str, request: RejectPluginRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    結構化退回外掛版本，可勾選有疑慮的能力，見 design.md I.9。
    """
    updated = await admin_operations.reject_plugin_version(
        plugin_id, request.reason_presets, request.custom_reason, request.flagged_capabilities
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    return await repository.get_plugin(plugin_id)


@router.post("/{plugin_id}/ban", response_model=PluginSummary)
async def ban_plugin(
    plugin_id: str, request: BanPluginRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    永久封鎖外掛，解除既有安裝並清 storage，見 design.md I.9。
    """
    updated = await admin_operations.ban_plugin_version(plugin_id, request.reason)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    return await repository.get_plugin(plugin_id)


@router.post("/{plugin_id}/unban", response_model=PluginSummary)
async def unban_plugin(plugin_id: str, operator: str = Depends(get_current_operator)) -> dict:
    """
    解除封鎖狀態並改回 rejected，不自動核准、不自動復裝。
    """
    updated = await admin_operations.unban_plugin(plugin_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    return await repository.get_plugin(plugin_id)


@router.post("/{plugin_id}/pricing-tier", response_model=PluginSummary)
async def set_pricing_tier(
    plugin_id: str, request: PricingTierRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    設定外掛計價分類，純標籤、不驅動任何自動行為，見 design.md I.1。
    """
    updated = await admin_operations.set_plugin_pricing_tier(plugin_id, request.pricing_tier)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    return await repository.get_plugin(plugin_id)
