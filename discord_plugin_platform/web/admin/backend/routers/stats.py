"""
稽核可見性路由，見 design.md H.3「稽核可見性」：直接用既有的
core/repository.py get_execution_stats()（附錄 A.6.2）做成簡單的儀表板資料。
"""

from fastapi import APIRouter, Depends

from core import repository
from web.admin.backend.dependencies import get_current_operator
from web.admin.backend.schemas import ExecutionStatsResponse

router = APIRouter(prefix="/api/plugins/{plugin_id}/stats", tags=["stats"])


@router.get("", response_model=ExecutionStatsResponse)
async def get_execution_stats(
    plugin_id: str,
    guild_id: int | None = None,
    since: str | None = None,
    operator: str = Depends(get_current_operator),
) -> dict:
    """
    彙總指定外掛的執行紀錄：成功率、各結果比例、平均/p95 耗時。
    """
    return await repository.get_execution_stats(plugin_id, guild_id=guild_id, since=since)
