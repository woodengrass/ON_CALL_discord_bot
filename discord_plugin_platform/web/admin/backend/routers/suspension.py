"""
外掛停權路由，見 design.md H.3「停權」、H.2：這裡只負責寫進資料庫，
bot 行程的停權快取靠既有的 core/suspension.py 週期任務自動撿到變更，最多約 10 秒生效。
"""

from fastapi import APIRouter, Depends, HTTPException

from core import admin_operations, repository
from web.admin.backend.dependencies import get_current_operator

router = APIRouter(prefix="/api/plugins/{plugin_id}", tags=["suspension"])


@router.post("/suspend")
async def suspend_plugin(plugin_id: str, operator: str = Depends(get_current_operator)) -> dict:
    """
    全域停權外掛，解除既有安裝並清 storage。
    """
    updated = await admin_operations.request_suspend(plugin_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    return await repository.get_plugin(plugin_id)


@router.post("/unsuspend")
async def unsuspend_plugin(plugin_id: str, operator: str = Depends(get_current_operator)) -> dict:
    """
    解除外掛停權，不自動復裝任何伺服器。
    """
    updated = await admin_operations.request_unsuspend(plugin_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"找不到外掛：{plugin_id}")
    return await repository.get_plugin(plugin_id)
