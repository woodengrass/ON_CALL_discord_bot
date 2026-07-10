"""
伺服器管理路由，見 design.md H.3「伺服器管理」：指定套用的資源方案、封鎖名單。
"""

from fastapi import APIRouter, Depends, HTTPException

from core import admin_operations, repository
from web.admin.backend.dependencies import get_current_operator
from web.admin.backend.schemas import GuildResourceTierRequest, InstallationBlockRequest

router = APIRouter(prefix="/api/guilds", tags=["guilds"])


@router.get("")
async def list_guilds(operator: str = Depends(get_current_operator)) -> list[int]:
    """
    列出目前平台知道的所有伺服器 ID，供伺服器管理頁做快速選單。
    """
    return await repository.list_known_guild_ids()


@router.get("/{guild_id}/resource-tier")
async def get_guild_resource_tier(guild_id: int, operator: str = Depends(get_current_operator)) -> dict:
    """
    取得伺服器目前套用的資源方案，未指定時回傳 default。
    """
    tier_name = await repository.get_guild_resource_tier(guild_id)
    return {"guild_id": guild_id, "tier_name": tier_name}


@router.put("/{guild_id}/resource-tier")
async def set_guild_resource_tier(
    guild_id: int, request: GuildResourceTierRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    指定伺服器套用的資源方案。
    """
    await admin_operations.set_guild_resource_tier(guild_id, request.tier_name)
    return {"guild_id": guild_id, "tier_name": request.tier_name}


@router.get("/{guild_id}/blocks")
async def list_guild_blocks(guild_id: int, operator: str = Depends(get_current_operator)) -> list[dict]:
    """
    列出伺服器封鎖的所有外掛。
    """
    return await repository.list_plugin_installation_blocks(guild_id)


@router.put("/{guild_id}/blocks/{plugin_id}")
async def block_plugin(
    guild_id: int, plugin_id: str, request: InstallationBlockRequest, operator: str = Depends(get_current_operator)
) -> dict:
    """
    封鎖指定伺服器安裝某個外掛。
    """
    await admin_operations.block_plugin_installation(guild_id, plugin_id, request.reason)
    return await repository.get_plugin_installation_block(guild_id, plugin_id)


@router.delete("/{guild_id}/blocks/{plugin_id}")
async def unblock_plugin(guild_id: int, plugin_id: str, operator: str = Depends(get_current_operator)) -> dict:
    """
    解除指定伺服器對外掛的安裝封鎖。
    """
    deleted = await admin_operations.unblock_plugin_installation(guild_id, plugin_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"找不到封鎖紀錄：{guild_id}/{plugin_id}")
    return {"deleted": True}
