import time
from collections import deque

DEFAULT_EXECUTION_QUOTA_PER_MINUTE = 60
DEFAULT_ACTION_QUOTA_PER_MINUTE = 30

QUOTA_WINDOW_SECONDS = 60

_execution_timestamps: dict[tuple[int, str], deque] = {}
_action_timestamps: dict[tuple[int, str], deque] = {}


def clear_usage(guild_id: int, plugin_id: str) -> None:
    """
    清除指定安裝的配額使用紀錄，解除安裝時呼叫，避免殘留 key 長期累積。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
    """
    key = (guild_id, plugin_id)
    _execution_timestamps.pop(key, None)
    _action_timestamps.pop(key, None)


def clear_guild_usage(guild_id: int) -> None:
    """
    清除指定伺服器所有外掛的配額使用紀錄。

    Args:
        guild_id: 伺服器 ID
    """
    for key in list(_execution_timestamps.keys()):
        if key[0] == guild_id:
            _execution_timestamps.pop(key, None)
    for key in list(_action_timestamps.keys()):
        if key[0] == guild_id:
            _action_timestamps.pop(key, None)


def _prune_window(timestamps: deque, now: float) -> None:
    """
    移除超過配額時間窗的舊時間戳記。

    Args:
        timestamps: 時間戳記佇列
        now: 目前時間（time.time() 回傳值）
    """
    while timestamps and now - timestamps[0] > QUOTA_WINDOW_SECONDS:
        timestamps.popleft()


async def check_and_consume_execution_quota(
    guild_id: int, plugin_id: str, limit_override: int | None = None
) -> bool:
    """
    檢查並消耗一次執行配額，配額用完時不消耗、直接回傳 False。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        limit_override: 已解析的執行配額；None 代表使用平台預設值

    Returns:
        True 表示配額足夠、已計入這次執行；False 表示配額已用完
    """
    limit = limit_override if limit_override is not None else DEFAULT_EXECUTION_QUOTA_PER_MINUTE

    key = (guild_id, plugin_id)
    timestamps = _execution_timestamps.setdefault(key, deque())
    now = time.time()
    _prune_window(timestamps, now)

    if len(timestamps) >= limit:
        return False

    timestamps.append(now)
    return True


async def check_and_consume_action_quota(
    guild_id: int, plugin_id: str, action_count: int, limit_override: int | None = None
) -> bool:
    """
    檢查並消耗指定數量的動作配額，配額不足時完全不消耗、直接回傳 False。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        action_count: 這次要執行的動作數量
        limit_override: 已解析的動作配額；None 代表使用平台預設值

    Returns:
        True 表示配額足夠、已計入這次的動作數量；False 表示配額不足
    """
    limit = limit_override if limit_override is not None else DEFAULT_ACTION_QUOTA_PER_MINUTE

    key = (guild_id, plugin_id)
    timestamps = _action_timestamps.setdefault(key, deque())
    now = time.time()
    _prune_window(timestamps, now)

    if len(timestamps) + action_count > limit:
        return False

    for _ in range(action_count):
        timestamps.append(now)
    return True
