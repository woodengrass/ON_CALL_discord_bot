"""
安裝管理路由，見 design.md H.3「安裝管理」：對應 admin_console.py 現有的
install／uninstall／quota set 指令，install_plugin 會先過封鎖與方案檢查（I.7、I.8）。
"""

from fastapi import APIRouter, Depends, HTTPException

from core import admin_operations, repository
from core.admin_operations import AdminOperationError, PluginInstallationBlockedError, PluginTierNotAllowedError
from web.admin.backend.dependencies import get_current_operator
from web.admin.backend.schemas import InstallationResponse, QuotaOverrideRequest, ResourceOverridesRequest

router = APIRouter(prefix="/api/guilds/{guild_id}/installations", tags=["installations"])


@router.get("", response_model=list[InstallationResponse])
async def list_installations(guild_id: int, operator: str = Depends(get_current_operator)) -> list[dict]:
    """
    列出伺服器目前已啟用的外掛安裝。
    """
    return await repository.get_enabled_installations_for_guild(guild_id)


@router.put("/{plugin_id}")
async def install_plugin(guild_id: int, plugin_id: str, operator: str = Depends(get_current_operator)) -> dict:
    """
    安裝已核准外掛，安裝前檢查伺服器封鎖與資源方案是否允許。
    """
    try:
        await admin_operations.install_plugin(guild_id, plugin_id)
    except PluginInstallationBlockedError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except PluginTierNotAllowedError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except AdminOperationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return await repository.get_installation(guild_id, plugin_id)


@router.delete("/{plugin_id}")
async def uninstall_plugin(guild_id: int, plugin_id: str, operator: str = Depends(get_current_operator)) -> dict:
    """
    解除安裝外掛並清理配額與訊息快取。
    """
    deleted = await admin_operations.uninstall_plugin(guild_id, plugin_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"找不到安裝紀錄：{guild_id}/{plugin_id}")
    return {"deleted": True}


@router.put("/{plugin_id}/quota-override")
async def set_quota_override(
    guild_id: int, plugin_id: str, request: QuotaOverrideRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    設定單一安裝的執行/動作配額覆蓋。
    """
    try:
        updated = await admin_operations.set_quota_override(
            guild_id, plugin_id, request.execution_quota, request.action_quota
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到安裝紀錄：{guild_id}/{plugin_id}")
    return await repository.get_installation(guild_id, plugin_id)


@router.put("/{plugin_id}/resource-overrides")
async def set_resource_overrides(
    guild_id: int, plugin_id: str, request: ResourceOverridesRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    設定單一安裝的五項資源覆蓋值，缺漏的欄位代表沿用方案/平台預設值。
    """
    overrides = request.model_dump(exclude_none=True)
    try:
        updated = await admin_operations.set_installation_resource_overrides(guild_id, plugin_id, overrides)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到安裝紀錄：{guild_id}/{plugin_id}")
    return await repository.get_installation(guild_id, plugin_id)
