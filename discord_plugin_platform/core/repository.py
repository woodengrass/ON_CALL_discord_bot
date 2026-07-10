import datetime
import json
import logging
import time

from core.database import get_db

logger = logging.getLogger(__name__)

MIN_QUOTA_OVERRIDE = 0
MAX_QUOTA_OVERRIDE = 10_000
EVENT_SUBSCRIPTION_CACHE_TTL_SECONDS = 10
PRICING_TIERS = {"free", "paid"}
DEFAULT_RESOURCE_TIER = "default"
RESOURCE_LIMIT_KEYS = {
    "storage_key_length_limit",
    "storage_value_bytes_limit",
    "storage_keys_per_installation_limit",
    "instruction_limit",
    "memory_limit_bytes",
}
QUOTA_LIMIT_KEYS = {"execution_quota", "action_quota"}
RESOLVED_LIMIT_KEYS = RESOURCE_LIMIT_KEYS | QUOTA_LIMIT_KEYS
TIER_CONFIG_KEYS = RESOURCE_LIMIT_KEYS | {"allowed", "execution_quota", "action_quota"}

_event_subscription_cache: dict[tuple[int, tuple[str, ...]], tuple[float, bool]] = {}


def _now_iso() -> str:
    """
    取得目前 UTC 時間的 ISO 格式字串。

    Returns:
        ISO 8601 格式的時間字串
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _plugin_from_row(row: tuple[object, ...]) -> dict:
    """
    將 plugins 查詢結果轉成外掛中繼資料 dict。

    Args:
        row: plugins 資料表查詢結果

    Returns:
        dict，包含外掛中繼資料欄位
    """
    return {
        "plugin_id": row[0],
        "author_id": row[1],
        "name": row[2],
        "latest_version": row[3],
        "status": row[4],
        "pricing_tier": row[5],
    }


def _validate_positive_int(value: object, field_name: str) -> None:
    """
    驗證資源限制值必須是正整數，且 bool 不能混作 int。

    Args:
        value: 要驗證的值
        field_name: 欄位名稱，用於錯誤訊息
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} 必須是正整數")


def _validate_resource_overrides(overrides: dict) -> None:
    """
    驗證逐安裝資源覆蓋欄位，只接受已知資源 key 與正整數值。

    Args:
        overrides: 操作者設定的資源覆蓋值
    """
    unknown_keys = set(overrides) - RESOURCE_LIMIT_KEYS
    if unknown_keys:
        raise ValueError(f"未知的資源覆蓋欄位：{', '.join(sorted(unknown_keys))}")
    for key, value in overrides.items():
        _validate_positive_int(value, key)


def _validate_tier_config(config: dict) -> None:
    """
    驗證外掛在單一資源方案下的設定。

    Args:
        config: 方案設定，至少需要 allowed 欄位
    """
    unknown_keys = set(config) - TIER_CONFIG_KEYS
    if unknown_keys:
        raise ValueError(f"未知的方案設定欄位：{', '.join(sorted(unknown_keys))}")
    if not isinstance(config.get("allowed"), bool):
        raise ValueError("allowed 必須是 bool")
    for key in TIER_CONFIG_KEYS - {"allowed"}:
        value = config.get(key)
        if value is not None:
            _validate_positive_int(value, key)


def clear_event_subscription_cache(guild_id: int | None = None) -> None:
    """
    清除事件訂閱快取，避免安裝、解除安裝或審核狀態變更後沿用舊結果。

    Args:
        guild_id: 指定伺服器 ID；None 表示清除全部快取
    """
    if guild_id is None:
        _event_subscription_cache.clear()
        return
    for cache_key in list(_event_subscription_cache.keys()):
        if cache_key[0] == guild_id:
            _event_subscription_cache.pop(cache_key, None)


async def submit_plugin_version(
    plugin_id: str,
    author_id: int,
    name: str,
    version: str,
    manifest_json: str,
    source_code: str,
    capability_api_version: int,
) -> None:
    """
    提交一個外掛版本，並同步建立或更新外掛中繼資料。

    Args:
        plugin_id: 外掛 ID
        author_id: 作者 Discord 使用者 ID
        name: 外掛名稱
        version: 外掛版本
        manifest_json: manifest 原始 JSON 字串
        source_code: Lua 原始碼
        capability_api_version: 能力 API 版本
    """
    db = get_db()
    try:
        async with db.execute("SELECT status FROM plugins WHERE plugin_id = ?", (plugin_id,)) as cursor:
            existing_plugin = await cursor.fetchone()
        if existing_plugin is not None and existing_plugin[0] == "banned":
            raise ValueError(f"外掛已被永久封鎖，不能重新提交：{plugin_id}")
        await db.execute(
            """
            INSERT INTO plugins (plugin_id, author_id, name, latest_version, status, pricing_tier)
            VALUES (?, ?, ?, ?, 'pending_review', 'free')
            ON CONFLICT(plugin_id) DO UPDATE SET
                author_id = excluded.author_id,
                name = excluded.name,
                latest_version = excluded.latest_version,
                status = 'pending_review'
            """,
            (plugin_id, author_id, name, version),
        )
        await db.execute(
            """
            INSERT INTO plugin_versions
                (plugin_id, version, manifest_json, source_code, capability_api_version, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (plugin_id, version, manifest_json, source_code, capability_api_version, _now_iso()),
        )
        await db.commit()
        clear_event_subscription_cache()
    except Exception as error:
        await db.rollback()
        logger.error(f"提交外掛版本失敗：{error}", exc_info=True)
        raise


async def get_plugin(plugin_id: str) -> dict | None:
    """
    取得單一外掛的中繼資料。

    Args:
        plugin_id: 外掛 ID

    Returns:
        dict，包含外掛中繼資料；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT plugin_id, author_id, name, latest_version, status, pricing_tier
        FROM plugins
        WHERE plugin_id = ?
        """,
        (plugin_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return _plugin_from_row(row)


async def get_plugin_source(plugin_id: str, version: str) -> str | None:
    """
    取得指定外掛版本的 Lua 原始碼。

    Args:
        plugin_id: 外掛 ID
        version: 外掛版本

    Returns:
        Lua 原始碼；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT source_code
        FROM plugin_versions
        WHERE plugin_id = ? AND version = ?
        """,
        (plugin_id, version),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def get_plugin_manifest(plugin_id: str, version: str) -> str | None:
    """
    取得指定外掛版本的 manifest JSON 字串。

    Args:
        plugin_id: 外掛 ID
        version: 外掛版本

    Returns:
        manifest JSON 字串；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT manifest_json
        FROM plugin_versions
        WHERE plugin_id = ? AND version = ?
        """,
        (plugin_id, version),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def list_plugins(status: str | None = None) -> list[dict]:
    """
    列出外掛中繼資料，可依狀態篩選。

    Args:
        status: 外掛狀態；None 表示列出全部

    Returns:
        list of dict，外掛中繼資料列表
    """
    db = get_db()
    if status is None:
        async with db.execute(
            """
            SELECT plugin_id, author_id, name, latest_version, status, pricing_tier
            FROM plugins
            ORDER BY plugin_id
            """
        ) as cursor:
            rows = await cursor.fetchall()
    else:
        async with db.execute(
            """
            SELECT plugin_id, author_id, name, latest_version, status, pricing_tier
            FROM plugins
            WHERE status = ?
            ORDER BY plugin_id
            """,
            (status,),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_plugin_from_row(row) for row in rows]


async def _set_plugin_status(plugin_id: str, status: str) -> bool:
    """
    更新外掛狀態。

    Args:
        plugin_id: 外掛 ID
        status: 新狀態

    Returns:
        True 表示成功更新；若外掛不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "UPDATE plugins SET status = ? WHERE plugin_id = ?",
        (status, plugin_id),
    )
    await db.commit()
    if cursor.rowcount > 0:
        clear_event_subscription_cache()
    return cursor.rowcount > 0


async def approve_plugin(plugin_id: str) -> bool:
    """
    核准指定外掛，並寫入審核稽核紀錄。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示成功核准；若外掛不存在則回傳 False
    """
    db = get_db()
    try:
        async with db.execute(
            "SELECT latest_version FROM plugins WHERE plugin_id = ?",
            (plugin_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return False
        latest_version = row[0]
        cursor = await db.execute(
            "UPDATE plugins SET status = 'approved' WHERE plugin_id = ?",
            (plugin_id,),
        )
        await db.execute(
            """
            INSERT INTO plugin_review_log (plugin_id, version, reviewer_action, reason, created_at)
            VALUES (?, ?, 'approved', NULL, ?)
            """,
            (plugin_id, latest_version, _now_iso()),
        )
        await db.commit()
        clear_event_subscription_cache()
        return True
    except Exception as error:
        await db.rollback()
        logger.error(f"核准外掛失敗：{error}", exc_info=True)
        raise


async def reject_plugin(plugin_id: str, reason: str) -> bool:
    """
    退回指定外掛，並寫入審核稽核紀錄與原因。

    Args:
        plugin_id: 外掛 ID
        reason: 退回原因

    Returns:
        True 表示成功退回；若外掛不存在則回傳 False
    """
    db = get_db()
    try:
        async with db.execute(
            "SELECT latest_version FROM plugins WHERE plugin_id = ?",
            (plugin_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return False
        latest_version = row[0]
        cursor = await db.execute(
            "UPDATE plugins SET status = 'rejected' WHERE plugin_id = ?",
            (plugin_id,),
        )
        await db.execute(
            """
            INSERT INTO plugin_review_log (plugin_id, version, reviewer_action, reason, created_at)
            VALUES (?, ?, 'rejected', ?, ?)
            """,
            (plugin_id, latest_version, reason, _now_iso()),
        )
        await db.commit()
        clear_event_subscription_cache()
        return True
    except Exception as error:
        await db.rollback()
        logger.error(f"退回外掛失敗：{error}", exc_info=True)
        raise


async def suspend_plugin(plugin_id: str) -> bool:
    """
    停權指定外掛。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示成功停權；若外掛不存在則回傳 False
    """
    return await _set_plugin_status(plugin_id, "suspended")


async def unsuspend_plugin(plugin_id: str) -> bool:
    """
    解除指定外掛停權，將狀態改回 approved。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示成功解除停權；若外掛不存在則回傳 False
    """
    return await _set_plugin_status(plugin_id, "approved")


async def ban_plugin(plugin_id: str, reason: str) -> bool:
    """
    將外掛狀態改成 banned，並寫入審核稽核紀錄。

    Args:
        plugin_id: 外掛 ID
        reason: 封鎖原因

    Returns:
        True 表示成功封鎖；外掛不存在則回傳 False
    """
    db = get_db()
    try:
        async with db.execute("SELECT latest_version FROM plugins WHERE plugin_id = ?", (plugin_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return False
        await db.execute("UPDATE plugins SET status = 'banned' WHERE plugin_id = ?", (plugin_id,))
        await db.execute(
            """
            INSERT INTO plugin_review_log (plugin_id, version, reviewer_action, reason, created_at)
            VALUES (?, ?, 'banned', ?, ?)
            """,
            (plugin_id, row[0], reason, _now_iso()),
        )
        await db.commit()
        clear_event_subscription_cache()
        return True
    except Exception as error:
        await db.rollback()
        logger.error(f"封鎖外掛失敗：{error}", exc_info=True)
        raise


async def unban_plugin(plugin_id: str) -> bool:
    """
    解除 banned 狀態，讓外掛回到 rejected 等待作者重新提交。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示成功解除封鎖；外掛不存在則回傳 False
    """
    return await _set_plugin_status(plugin_id, "rejected")


async def set_plugin_pricing_tier(plugin_id: str, pricing_tier: str) -> bool:
    """
    設定外掛的計價分類。分類只是營運標籤，不會自動影響配額。

    Args:
        plugin_id: 外掛 ID
        pricing_tier: free 或 paid

    Returns:
        True 表示成功更新；外掛不存在則回傳 False
    """
    if pricing_tier not in PRICING_TIERS:
        raise ValueError("pricing_tier 只能是 free 或 paid")
    db = get_db()
    cursor = await db.execute(
        "UPDATE plugins SET pricing_tier = ? WHERE plugin_id = ?",
        (pricing_tier, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def reject_plugin_structured(
    plugin_id: str,
    reason_presets: list[str],
    custom_reason: str | None,
    flagged_capabilities: list[str],
) -> bool:
    """
    用結構化欄位退回外掛版本。

    Args:
        plugin_id: 外掛 ID
        reason_presets: 選中的退回原因 preset_id 清單
        custom_reason: 自由文字退回原因
        flagged_capabilities: 有疑慮的能力旗標清單

    Returns:
        True 表示成功退回；外掛不存在則回傳 False
    """
    db = get_db()
    try:
        async with db.execute(
            "SELECT latest_version FROM plugins WHERE plugin_id = ?", (plugin_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return False
        latest_version = row[0]
        await db.execute("UPDATE plugins SET status = 'rejected' WHERE plugin_id = ?", (plugin_id,))
        await db.execute(
            """
            INSERT INTO plugin_review_log (
                plugin_id, version, reviewer_action, reason, reason_presets_json,
                flagged_capabilities_json, created_at
            )
            VALUES (?, ?, 'rejected', ?, ?, ?, ?)
            """,
            (
                plugin_id,
                latest_version,
                custom_reason,
                json.dumps(reason_presets, ensure_ascii=False),
                json.dumps(flagged_capabilities, ensure_ascii=False),
                _now_iso(),
            ),
        )
        await db.commit()
        clear_event_subscription_cache()
        return True
    except Exception as error:
        await db.rollback()
        logger.error(f"結構化退回外掛失敗：{error}", exc_info=True)
        raise


async def create_installation(
    guild_id: int, plugin_id: str, version: str, granted_capabilities: list[str]
) -> None:
    """
    建立或更新指定伺服器的外掛安裝紀錄。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        version: 安裝版本
        granted_capabilities: 使用者同意授權的能力清單
    """
    db = get_db()
    granted_capabilities_json = json.dumps(granted_capabilities, ensure_ascii=False)
    await db.execute(
        """
        INSERT INTO plugin_installations
            (guild_id, plugin_id, installed_version, granted_capabilities_json, enabled, installed_at)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(guild_id, plugin_id) DO UPDATE SET
            installed_version = excluded.installed_version,
            granted_capabilities_json = excluded.granted_capabilities_json,
            enabled = 1
        """,
        (guild_id, plugin_id, version, granted_capabilities_json, _now_iso()),
    )
    await db.commit()
    clear_event_subscription_cache(guild_id)


async def delete_installation(guild_id: int, plugin_id: str) -> bool:
    """
    刪除指定伺服器的外掛安裝紀錄，並清掉這個安裝底下所有尚未執行的排程任務。

    一併清掉排程任務是必要的，不是順手做的清理：`consume_due_scheduled_tasks()`
    每分鐘會重新分派所有到期任務，如果安裝已經刪除但任務還留著，
    `dispatch_event(target_plugin_id=...)` 永遠找不到對應安裝、永遠回傳失敗，
    任務會被永遠保留、永遠重試，變成每分鐘執行一次、永遠不會成功也不會消失的殭屍任務。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        True 表示成功刪除；若安裝紀錄不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM plugin_installations WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    )
    await db.execute(
        "DELETE FROM plugin_scheduled_tasks WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    )
    await db.commit()
    if cursor.rowcount > 0:
        clear_event_subscription_cache(guild_id)
    return cursor.rowcount > 0


async def delete_all_installations_for_guild(guild_id: int) -> list[str]:
    """
    刪除指定伺服器的所有外掛安裝紀錄與其排程任務，機器人被踢出伺服器時呼叫。

    不呼叫 delete_installation() 逐一刪（會變成每個安裝各自一次 commit），
    直接用 guild_id 一次刪完兩張表，理由跟 delete_installation() 一樣：
    沒清排程任務會變成永遠重試、永遠不會消失的殭屍任務。

    Args:
        guild_id: 伺服器 ID

    Returns:
        被刪除的 plugin_id 清單，呼叫端可能需要知道刪了哪些外掛（例如記錄稽核紀錄）
    """
    db = get_db()
    async with db.execute(
        "SELECT plugin_id FROM plugin_installations WHERE guild_id = ?", (guild_id,)
    ) as cursor:
        rows = await cursor.fetchall()
    deleted_plugin_ids = [row[0] for row in rows]

    await db.execute("DELETE FROM plugin_installations WHERE guild_id = ?", (guild_id,))
    await db.execute("DELETE FROM plugin_scheduled_tasks WHERE guild_id = ?", (guild_id,))
    await db.commit()
    clear_event_subscription_cache(guild_id)
    return deleted_plugin_ids


async def delete_all_installations_for_plugin(plugin_id: str) -> list[int]:
    """
    刪除指定外掛在所有伺服器的安裝與排程任務，供停權/封鎖連鎖效應使用。

    Args:
        plugin_id: 外掛 ID

    Returns:
        受影響的 guild_id 清單
    """
    db = get_db()
    async with db.execute(
        "SELECT guild_id FROM plugin_installations WHERE plugin_id = ?", (plugin_id,)
    ) as cursor:
        rows = await cursor.fetchall()
    affected_guild_ids = [row[0] for row in rows]
    await db.execute("DELETE FROM plugin_installations WHERE plugin_id = ?", (plugin_id,))
    await db.execute("DELETE FROM plugin_scheduled_tasks WHERE plugin_id = ?", (plugin_id,))
    await db.commit()
    clear_event_subscription_cache()
    return affected_guild_ids


async def get_due_scheduled_tasks(now_iso: str) -> list[dict]:
    """
    取得所有已到期的外掛排程任務。

    Args:
        now_iso: 目前時間的 ISO 8601 字串

    Returns:
        list of dict，已到期任務列表
    """
    db = get_db()
    async with db.execute(
        """
        SELECT plugin_scheduled_tasks.task_id, plugin_scheduled_tasks.guild_id,
               plugin_scheduled_tasks.plugin_id, plugin_scheduled_tasks.run_at,
               plugin_scheduled_tasks.payload_json,
               plugin_scheduled_tasks.recurring_interval_seconds,
               plugin_versions.manifest_json
        FROM plugin_scheduled_tasks
        JOIN plugin_installations
          ON plugin_installations.guild_id = plugin_scheduled_tasks.guild_id
         AND plugin_installations.plugin_id = plugin_scheduled_tasks.plugin_id
         AND plugin_installations.enabled = 1
        JOIN plugins
          ON plugins.plugin_id = plugin_scheduled_tasks.plugin_id
         AND plugins.status = 'approved'
        JOIN plugin_versions
          ON plugin_versions.plugin_id = plugin_installations.plugin_id
         AND plugin_versions.version = plugin_installations.installed_version
        WHERE plugin_scheduled_tasks.run_at <= ?
        ORDER BY plugin_scheduled_tasks.run_at, plugin_scheduled_tasks.task_id
        """,
        (now_iso,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "task_id": row[0],
            "guild_id": row[1],
            "plugin_id": row[2],
            "run_at": row[3],
            "payload_json": row[4],
            "recurring_interval_seconds": row[5],
            "manifest_json": row[6],
        }
        for row in rows
    ]


async def update_scheduled_task_run_at(task_id: str, run_at: str) -> bool:
    """
    更新週期性排程任務的下一次執行時間。

    Args:
        task_id: 排程任務 ID
        run_at: 下一次執行時間的 ISO 8601 字串

    Returns:
        True 表示成功更新；若任務不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "UPDATE plugin_scheduled_tasks SET run_at = ? WHERE task_id = ?",
        (run_at, task_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_scheduled_task(task_id: str) -> None:
    """
    刪除指定的外掛排程任務。

    Args:
        task_id: 排程任務 ID
    """
    db = get_db()
    await db.execute("DELETE FROM plugin_scheduled_tasks WHERE task_id = ?", (task_id,))
    await db.commit()


async def guild_has_event_subscription(guild_id: int, event_types: set[str]) -> bool:
    """
    檢查指定伺服器是否有啟用外掛訂閱任一事件。

    Args:
        guild_id: 伺服器 ID
        event_types: 要檢查的事件名稱集合

    Returns:
        True 表示至少一個啟用安裝訂閱其中一個事件
    """
    cache_key = (guild_id, tuple(sorted(event_types)))
    now = time.monotonic()
    cached_result = _event_subscription_cache.get(cache_key)
    if cached_result is not None and cached_result[0] > now:
        return cached_result[1]

    db = get_db()
    async with db.execute(
        """
        SELECT plugin_versions.manifest_json
        FROM plugin_installations
        JOIN plugin_versions
          ON plugin_versions.plugin_id = plugin_installations.plugin_id
         AND plugin_versions.version = plugin_installations.installed_version
        JOIN plugins
          ON plugins.plugin_id = plugin_installations.plugin_id
        WHERE plugin_installations.guild_id = ?
          AND plugin_installations.enabled = 1
          AND plugins.status != 'suspended'
        """,
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    for row in rows:
        try:
            manifest_data = json.loads(row[0])
        except json.JSONDecodeError as error:
            logger.error(f"讀取外掛 manifest 事件訂閱失敗：{error}", exc_info=True)
            continue
        if event_types.intersection(manifest_data.get("event_hooks", [])):
            _event_subscription_cache[cache_key] = (now + EVENT_SUBSCRIPTION_CACHE_TTL_SECONDS, True)
            return True
    _event_subscription_cache[cache_key] = (now + EVENT_SUBSCRIPTION_CACHE_TTL_SECONDS, False)
    return False


async def get_installation(guild_id: int, plugin_id: str) -> dict | None:
    """
    取得指定伺服器對某個外掛的安裝紀錄。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        dict，包含安裝紀錄欄位；若尚未安裝則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT guild_id, plugin_id, installed_version, granted_capabilities_json, enabled,
               execution_quota_override, action_quota_override, resource_overrides_json
        FROM plugin_installations
        WHERE guild_id = ? AND plugin_id = ?
        """,
        (guild_id, plugin_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "guild_id": row[0],
        "plugin_id": row[1],
        "installed_version": row[2],
        "granted_capabilities_json": row[3],
        "enabled": bool(row[4]),
        "execution_quota_override": row[5],
        "action_quota_override": row[6],
        "resource_overrides_json": row[7],
    }


async def get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
    """
    取得指定伺服器目前所有已啟用的外掛安裝紀錄。

    Args:
        guild_id: 伺服器 ID

    Returns:
        list of dict，每筆包含安裝紀錄欄位
    """
    db = get_db()
    async with db.execute(
        """
        SELECT plugin_installations.guild_id, plugin_installations.plugin_id,
               plugin_installations.installed_version, plugin_installations.granted_capabilities_json,
               plugin_installations.execution_quota_override, plugin_installations.action_quota_override,
               plugin_installations.resource_overrides_json, plugin_versions.manifest_json
        FROM plugin_installations
        JOIN plugin_versions
          ON plugin_versions.plugin_id = plugin_installations.plugin_id
         AND plugin_versions.version = plugin_installations.installed_version
        WHERE plugin_installations.guild_id = ? AND plugin_installations.enabled = 1
        """,
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "guild_id": row[0],
            "plugin_id": row[1],
            "installed_version": row[2],
            "granted_capabilities_json": row[3],
            "execution_quota_override": row[4],
            "action_quota_override": row[5],
            "resource_overrides_json": row[6],
            "manifest_json": row[7],
        }
        for row in rows
    ]


async def set_installation_quota_override(
    guild_id: int, plugin_id: str, execution_quota: int | None, action_quota: int | None
) -> bool:
    """
    設定指定安裝的動態配額覆蓋值。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        execution_quota: 每分鐘執行次數上限；None 代表恢復使用平台預設值
        action_quota: 每分鐘動作次數上限；None 代表恢復使用平台預設值

    Returns:
        True 表示成功更新；若該安裝不存在則回傳 False
    """
    for quota_value in (execution_quota, action_quota):
        if quota_value is not None and (quota_value < MIN_QUOTA_OVERRIDE or quota_value > MAX_QUOTA_OVERRIDE):
            raise ValueError(f"配額覆蓋值必須介於 {MIN_QUOTA_OVERRIDE} 到 {MAX_QUOTA_OVERRIDE}")

    db = get_db()
    cursor = await db.execute(
        """
        UPDATE plugin_installations
        SET execution_quota_override = ?, action_quota_override = ?
        WHERE guild_id = ? AND plugin_id = ?
        """,
        (execution_quota, action_quota, guild_id, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def set_resource_overrides(guild_id: int, plugin_id: str, overrides: dict) -> bool:
    """
    設定指定安裝的五項資源覆蓋值。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        overrides: 資源覆蓋 dict，空 dict 代表清除覆蓋

    Returns:
        True 表示成功更新；安裝不存在則回傳 False
    """
    _validate_resource_overrides(overrides)
    overrides_json = json.dumps(overrides, ensure_ascii=False) if overrides else None
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE plugin_installations
        SET resource_overrides_json = ?
        WHERE guild_id = ? AND plugin_id = ?
        """,
        (overrides_json, guild_id, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def list_resource_tiers() -> list[dict]:
    """
    列出所有資源方案。

    Returns:
        依 display_order 與 tier_name 排序的方案列表
    """
    db = get_db()
    async with db.execute(
        "SELECT tier_name, display_order, description FROM resource_tiers ORDER BY display_order, tier_name"
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"tier_name": row[0], "display_order": row[1], "description": row[2]} for row in rows]


async def get_resource_tier(tier_name: str) -> dict | None:
    """
    查詢單一資源方案。

    Args:
        tier_name: 方案名稱

    Returns:
        方案 dict；不存在則回傳 None
    """
    db = get_db()
    async with db.execute(
        "SELECT tier_name, display_order, description FROM resource_tiers WHERE tier_name = ?",
        (tier_name,),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {"tier_name": row[0], "display_order": row[1], "description": row[2]}


async def create_resource_tier(tier_name: str, display_order: int, description: str) -> None:
    """
    建立資源方案。

    Args:
        tier_name: 方案名稱
        display_order: 顯示排序
        description: 操作者說明
    """
    db = get_db()
    await db.execute(
        "INSERT INTO resource_tiers (tier_name, display_order, description) VALUES (?, ?, ?)",
        (tier_name, display_order, description),
    )
    await db.commit()


async def update_resource_tier(tier_name: str, display_order: int, description: str) -> bool:
    """
    更新資源方案顯示資訊。

    Args:
        tier_name: 方案名稱
        display_order: 顯示排序
        description: 操作者說明

    Returns:
        True 表示成功更新；方案不存在則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        "UPDATE resource_tiers SET display_order = ?, description = ? WHERE tier_name = ?",
        (display_order, description, tier_name),
    )
    await db.commit()
    return cursor.rowcount > 0


async def delete_resource_tier(tier_name: str, force: bool = False) -> bool:
    """
    刪除資源方案；預設若仍被引用會拒絕，force=True 時改派伺服器為 default 並刪除矩陣設定。

    Args:
        tier_name: 方案名稱
        force: 是否強制清理引用

    Returns:
        True 表示成功刪除；方案不存在則回傳 False
    """
    if tier_name == DEFAULT_RESOURCE_TIER:
        raise ValueError("default 方案不能刪除")
    db = get_db()
    async with db.execute("SELECT 1 FROM resource_tiers WHERE tier_name = ?", (tier_name,)) as cursor:
        exists = await cursor.fetchone()
    if exists is None:
        return False
    async with db.execute("SELECT COUNT(*) FROM guild_resource_tiers WHERE tier_name = ?", (tier_name,)) as cursor:
        guild_count = (await cursor.fetchone())[0]
    async with db.execute("SELECT COUNT(*) FROM plugin_tier_config WHERE tier_name = ?", (tier_name,)) as cursor:
        plugin_count = (await cursor.fetchone())[0]
    if (guild_count or plugin_count) and not force:
        raise ValueError(f"方案仍被引用：guilds={guild_count}, plugins={plugin_count}")
    if force:
        await db.execute(
            "UPDATE guild_resource_tiers SET tier_name = ?, assigned_at = ? WHERE tier_name = ?",
            (DEFAULT_RESOURCE_TIER, _now_iso(), tier_name),
        )
        await db.execute("DELETE FROM plugin_tier_config WHERE tier_name = ?", (tier_name,))
    cursor = await db.execute("DELETE FROM resource_tiers WHERE tier_name = ?", (tier_name,))
    await db.commit()
    return cursor.rowcount > 0


async def set_guild_resource_tier(guild_id: int, tier_name: str) -> None:
    """
    指定伺服器使用的資源方案。

    Args:
        guild_id: 伺服器 ID
        tier_name: 方案名稱
    """
    if await get_resource_tier(tier_name) is None:
        raise ValueError(f"找不到資源方案：{tier_name}")
    db = get_db()
    await db.execute(
        """
        INSERT INTO guild_resource_tiers (guild_id, tier_name, assigned_at)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            tier_name = excluded.tier_name,
            assigned_at = excluded.assigned_at
        """,
        (guild_id, tier_name, _now_iso()),
    )
    await db.commit()


async def get_guild_resource_tier(guild_id: int) -> str:
    """
    取得伺服器目前資源方案，未指定時回傳 default。

    Args:
        guild_id: 伺服器 ID

    Returns:
        方案名稱
    """
    db = get_db()
    async with db.execute("SELECT tier_name FROM guild_resource_tiers WHERE guild_id = ?", (guild_id,)) as cursor:
        row = await cursor.fetchone()
    return row[0] if row is not None else DEFAULT_RESOURCE_TIER


async def set_plugin_tier_config(plugin_id: str, tier_name: str, config: dict) -> None:
    """
    設定外掛在單一資源方案下的允許狀態、配額與資源限制。

    Args:
        plugin_id: 外掛 ID
        tier_name: 方案名稱
        config: 方案設定
    """
    if await get_resource_tier(tier_name) is None:
        raise ValueError(f"找不到資源方案：{tier_name}")
    _validate_tier_config(config)
    db = get_db()
    await db.execute(
        """
        INSERT INTO plugin_tier_config (
            plugin_id, tier_name, allowed, execution_quota, action_quota,
            storage_key_length_limit, storage_value_bytes_limit,
            storage_keys_per_installation_limit, instruction_limit, memory_limit_bytes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plugin_id, tier_name) DO UPDATE SET
            allowed = excluded.allowed,
            execution_quota = excluded.execution_quota,
            action_quota = excluded.action_quota,
            storage_key_length_limit = excluded.storage_key_length_limit,
            storage_value_bytes_limit = excluded.storage_value_bytes_limit,
            storage_keys_per_installation_limit = excluded.storage_keys_per_installation_limit,
            instruction_limit = excluded.instruction_limit,
            memory_limit_bytes = excluded.memory_limit_bytes
        """,
        (
            plugin_id,
            tier_name,
            int(config["allowed"]),
            config.get("execution_quota"),
            config.get("action_quota"),
            config.get("storage_key_length_limit"),
            config.get("storage_value_bytes_limit"),
            config.get("storage_keys_per_installation_limit"),
            config.get("instruction_limit"),
            config.get("memory_limit_bytes"),
        ),
    )
    await db.commit()


async def get_plugin_tier_config(plugin_id: str, tier_name: str) -> dict | None:
    """
    查詢外掛在指定方案下的設定。

    Args:
        plugin_id: 外掛 ID
        tier_name: 方案名稱

    Returns:
        設定 dict；不存在則回傳 None
    """
    db = get_db()
    async with db.execute(
        """
        SELECT allowed, execution_quota, action_quota, storage_key_length_limit,
               storage_value_bytes_limit, storage_keys_per_installation_limit,
               instruction_limit, memory_limit_bytes
        FROM plugin_tier_config
        WHERE plugin_id = ? AND tier_name = ?
        """,
        (plugin_id, tier_name),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {
        "allowed": bool(row[0]),
        "execution_quota": row[1],
        "action_quota": row[2],
        "storage_key_length_limit": row[3],
        "storage_value_bytes_limit": row[4],
        "storage_keys_per_installation_limit": row[5],
        "instruction_limit": row[6],
        "memory_limit_bytes": row[7],
    }


async def block_plugin_installation(guild_id: int, plugin_id: str, reason: str | None = None) -> None:
    """
    封鎖指定伺服器安裝某個外掛。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        reason: 操作者備註原因
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO plugin_installation_blocks (guild_id, plugin_id, blocked_at, reason)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, plugin_id) DO UPDATE SET
            blocked_at = excluded.blocked_at,
            reason = excluded.reason
        """,
        (guild_id, plugin_id, _now_iso(), reason),
    )
    await db.commit()


async def unblock_plugin_installation(guild_id: int, plugin_id: str) -> bool:
    """
    解除指定伺服器對外掛的安裝封鎖。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        True 表示有刪除封鎖紀錄
    """
    db = get_db()
    cursor = await db.execute(
        "DELETE FROM plugin_installation_blocks WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_plugin_installation_block(guild_id: int, plugin_id: str) -> dict | None:
    """
    查詢指定伺服器是否封鎖某個外掛。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        封鎖紀錄；不存在則回傳 None
    """
    db = get_db()
    async with db.execute(
        "SELECT blocked_at, reason FROM plugin_installation_blocks WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {"guild_id": guild_id, "plugin_id": plugin_id, "blocked_at": row[0], "reason": row[1]}


async def create_rejection_reason_preset(preset_id: str, label: str) -> None:
    """
    建立或更新退回原因預設選項。

    Args:
        preset_id: 預設原因 ID
        label: 顯示文字
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO rejection_reason_presets (preset_id, label)
        VALUES (?, ?)
        ON CONFLICT(preset_id) DO UPDATE SET label = excluded.label
        """,
        (preset_id, label),
    )
    await db.commit()


async def list_rejection_reason_presets() -> list[dict]:
    """
    列出退回原因預設選項。

    Returns:
        preset dict 清單
    """
    db = get_db()
    async with db.execute("SELECT preset_id, label FROM rejection_reason_presets ORDER BY preset_id") as cursor:
        rows = await cursor.fetchall()
    return [{"preset_id": row[0], "label": row[1]} for row in rows]


async def enqueue_guild_notification(guild_id: int, notification_type: str, payload: dict) -> None:
    """
    寫入一筆待 bot 行程背景任務送出的伺服器通知。

    Args:
        guild_id: 伺服器 ID
        notification_type: 通知類型
        payload: 通知資料
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO guild_notifications (guild_id, notification_type, payload_json, created_at, sent_at)
        VALUES (?, ?, ?, ?, NULL)
        """,
        (guild_id, notification_type, json.dumps(payload, ensure_ascii=False), _now_iso()),
    )
    await db.commit()


async def get_pending_guild_notifications(limit: int = 100) -> list[dict]:
    """
    取得尚未送出的伺服器通知。

    Args:
        limit: 最多回傳筆數

    Returns:
        通知 dict 清單
    """
    db = get_db()
    async with db.execute(
        """
        SELECT notification_id, guild_id, notification_type, payload_json, created_at
        FROM guild_notifications
        WHERE sent_at IS NULL
        ORDER BY notification_id
        LIMIT ?
        """,
        (limit,),
    ) as cursor:
        rows = await cursor.fetchall()
    return [
        {
            "notification_id": row[0],
            "guild_id": row[1],
            "notification_type": row[2],
            "payload_json": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


async def mark_guild_notification_sent(notification_id: int) -> bool:
    """
    將伺服器通知標記為已送出。

    Args:
        notification_id: 通知 ID

    Returns:
        True 表示有更新資料列
    """
    db = get_db()
    cursor = await db.execute(
        "UPDATE guild_notifications SET sent_at = ? WHERE notification_id = ?",
        (_now_iso(), notification_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def resolve_resource_limits(guild_id: int, plugin_id: str) -> dict:
    """
    解析指定安裝的七項資源限制，套用「個別覆蓋 → 外掛×方案設定 → 平台常數」優先順序。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        七項資源限制的最終值
    """
    from core import plugin_storage_repository, quota
    from sandbox import engine

    defaults = {
        "execution_quota": quota.DEFAULT_EXECUTION_QUOTA_PER_MINUTE,
        "action_quota": quota.DEFAULT_ACTION_QUOTA_PER_MINUTE,
        "storage_key_length_limit": plugin_storage_repository.MAX_STORAGE_KEY_LENGTH,
        "storage_value_bytes_limit": plugin_storage_repository.MAX_STORAGE_VALUE_BYTES,
        "storage_keys_per_installation_limit": plugin_storage_repository.MAX_STORAGE_KEYS_PER_INSTALLATION,
        "instruction_limit": engine.INSTRUCTION_LIMIT,
        "memory_limit_bytes": engine.MEMORY_LIMIT_BYTES,
    }
    tier_name = await get_guild_resource_tier(guild_id)
    tier_config = await get_plugin_tier_config(plugin_id, tier_name) or {}
    resolved = {key: tier_config.get(key) or defaults[key] for key in RESOLVED_LIMIT_KEYS}
    installation = await get_installation(guild_id, plugin_id)
    if installation is None:
        return resolved
    if installation["execution_quota_override"] is not None:
        resolved["execution_quota"] = installation["execution_quota_override"]
    if installation["action_quota_override"] is not None:
        resolved["action_quota"] = installation["action_quota_override"]
    if installation.get("resource_overrides_json") is not None:
        overrides = json.loads(installation["resource_overrides_json"])
        _validate_resource_overrides(overrides)
        for key, value in overrides.items():
            resolved[key] = value
    return resolved


async def is_plugin_suspended(plugin_id: str) -> bool:
    """
    檢查指定外掛目前是否處於停權狀態。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示已停權
    """
    db = get_db()
    async with db.execute(
        "SELECT status FROM plugins WHERE plugin_id = ?", (plugin_id,)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return False
    return row[0] == "suspended"


async def log_execution(
    guild_id: int,
    plugin_id: str,
    event_type: str,
    actions_json: str,
    execution_ms: int,
    outcome: str,
    error: str | None = None,
) -> None:
    """
    記錄一筆外掛執行的稽核紀錄。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        event_type: 觸發執行的事件名稱
        actions_json: 本次執行產生的動作清單（JSON 字串）
        execution_ms: 執行耗時（毫秒）
        outcome: 執行結果，success/quota_exceeded/crashed/rejected_invalid_action
        error: 失敗訊息或成功執行中的 action-level 錯誤摘要
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO plugin_execution_log
            (guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error, _now_iso()),
    )
    await db.commit()


async def get_execution_stats(
    plugin_id: str, guild_id: int | None = None, since: str | None = None
) -> dict:
    """
    彙總 plugin_execution_log，回答「這個外掛穩不穩、慢不慢」，見 design.md 附錄 A.6.2
    觀測性規劃第一步：不用新依賴，直接對既有稽核紀錄做 GROUP BY。

    Args:
        plugin_id: 外掛 ID
        guild_id: 只統計指定伺服器；None 表示統計所有伺服器
        since: 只統計這個 ISO 時間之後（含）的紀錄；None 表示統計全部歷史

    Returns:
        {"total": 總筆數,
         "outcome_counts": {outcome: 筆數, ...},
         "avg_execution_ms": 平均耗時或 None（沒有紀錄時）,
         "p95_execution_ms": p95 耗時或 None（沒有紀錄時）}
    """
    db = get_db()
    where_clauses = ["plugin_id = ?"]
    params: list = [plugin_id]
    if guild_id is not None:
        where_clauses.append("guild_id = ?")
        params.append(guild_id)
    if since is not None:
        where_clauses.append("created_at >= ?")
        params.append(since)
    where_sql = " AND ".join(where_clauses)

    async with db.execute(
        f"SELECT outcome, execution_ms FROM plugin_execution_log WHERE {where_sql} ORDER BY execution_ms",
        params,
    ) as cursor:
        rows = await cursor.fetchall()

    total = len(rows)
    outcome_counts: dict[str, int] = {}
    for outcome, _ in rows:
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    if total == 0:
        return {"total": 0, "outcome_counts": {}, "avg_execution_ms": None, "p95_execution_ms": None}

    execution_times = [row[1] for row in rows]
    avg_execution_ms = sum(execution_times) / total
    p95_index = min(total - 1, int(total * 0.95))
    p95_execution_ms = execution_times[p95_index]

    return {
        "total": total,
        "outcome_counts": outcome_counts,
        "avg_execution_ms": avg_execution_ms,
        "p95_execution_ms": p95_execution_ms,
    }
