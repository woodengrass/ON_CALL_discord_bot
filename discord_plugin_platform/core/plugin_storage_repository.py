"""
外掛專屬 KV 儲存（`storage_*` 能力）與排程任務（`schedule_task` 能力）的資料存取層。

刻意獨立於 core/repository.py（Track B 負責的外掛市集/審核資料表存取層），避免兩邊
同時改同一個檔案造成合併衝突；這裡管的 plugin_kv_store、plugin_scheduled_tasks
兩張表也跟市集審核邏輯無關，是能力 API 專屬的資料。
"""

import datetime
import json
import os
import time
import uuid
from typing import Any

import aiosqlite

from core.capability_errors import ScheduledTaskLimitExceededError, StorageLimitExceededError
from core.database import get_db


def _get_int_setting(name: str, default: int) -> int:
    """
    從環境變數讀取正整數設定，未設定時使用預設值。

    Args:
        name: 環境變數名稱
        default: 預設值

    Returns:
        設定值；若環境變數不存在則回傳 default
    """
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    parsed_value = int(raw_value)
    if parsed_value < 1:
        raise ValueError(f"{name} 必須是正整數")
    return parsed_value


# storage 能力沒有任何大小/數量上限的話，外掛可以把 SQLite 當成無限儲存空間濫用
# （不管是惡意還是單純寫壞的迴圈），這幾個常數就是防這個的軟性上限，數字不是
# 精算出來的，是「一般排行榜/計數器類外掛用得很夠、濫用起來很快就會撞到」的量級，
# 之後有真實用量數據再校準（比照 design.md 第 5.4 節資源限制的做法）。
MAX_STORAGE_KEY_LENGTH = _get_int_setting("PLUGIN_PLATFORM_MAX_STORAGE_KEY_LENGTH", 256)
MAX_STORAGE_VALUE_BYTES = _get_int_setting("PLUGIN_PLATFORM_MAX_STORAGE_VALUE_BYTES", 64 * 1024)
MAX_STORAGE_KEYS_PER_INSTALLATION = _get_int_setting("PLUGIN_PLATFORM_MAX_STORAGE_KEYS_PER_INSTALLATION", 1000)
MAX_LEADERBOARD_LIMIT = _get_int_setting("PLUGIN_PLATFORM_MAX_LEADERBOARD_LIMIT", 100)
MAX_SCHEDULED_TASKS_PER_INSTALLATION = _get_int_setting("PLUGIN_PLATFORM_MAX_SCHEDULED_TASKS_PER_INSTALLATION", 1000)
MAX_SCHEDULED_TASK_NAME_LENGTH = _get_int_setting("PLUGIN_PLATFORM_MAX_SCHEDULED_TASK_NAME_LENGTH", 128)
MAX_SCHEDULED_TASK_PAYLOAD_BYTES = _get_int_setting("PLUGIN_PLATFORM_MAX_SCHEDULED_TASK_PAYLOAD_BYTES", 16 * 1024)
MIN_SCHEDULE_DELAY_SECONDS = _get_int_setting("PLUGIN_PLATFORM_MIN_SCHEDULE_DELAY_SECONDS", 1)
MAX_SCHEDULE_DELAY_SECONDS = _get_int_setting("PLUGIN_PLATFORM_MAX_SCHEDULE_DELAY_SECONDS", 60 * 60 * 24 * 365)
MIN_RECURRING_INTERVAL_SECONDS = _get_int_setting("PLUGIN_PLATFORM_MIN_RECURRING_INTERVAL_SECONDS", 60)


def _limit_value(resource_overrides: dict | None, key: str, default: int) -> int:
    """
    從逐安裝資源覆蓋讀取限制值，沒有覆蓋時回傳平台預設常數。

    Args:
        resource_overrides: 已解析的資源限制 dict
        key: 資源欄位名稱
        default: 平台預設值

    Returns:
        最終限制值
    """
    if resource_overrides is None:
        return default
    value = resource_overrides.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else default


def _now_iso() -> str:
    """
    取得目前 UTC 時間的 ISO 格式字串。

    Returns:
        ISO 8601 格式的時間字串
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _escape_like_pattern(prefix: str) -> str:
    """
    跳脫 LIKE 語法裡的萬用字元，避免外掛傳入的 prefix 裡剛好含有 % 或 _
    被誤判成萬用字元，導致查詢結果跟外掛預期的不一致。

    Args:
        prefix: 外掛傳入的 key 前綴

    Returns:
        跳脫過的字串，需搭配 `LIKE ... ESCAPE '\\'` 使用
    """
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def storage_get(guild_id: int, plugin_id: str, key: str, db: aiosqlite.Connection | None = None) -> Any:
    """
    讀取外掛專屬的 KV 資料，以 (guild_id, plugin_id, key) 隔離。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        key: 資料鍵值
        db: 指定要用哪條連線，None 代表用共用連線。外掛執行期間呼叫時，
            這裡要跟 storage_set() 用同一條執行專用連線，才能讀到「自己這次
            執行剛寫的值」（見 design.md 第 5.4.2 節）。

    Returns:
        對應的值（已還原成原本的 JSON 型別）；找不到則回傳 None
    """
    db = db or get_db()
    async with db.execute(
        "SELECT value_json FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key = ?",
        (guild_id, plugin_id, key),
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return json.loads(row[0])


async def storage_set(
    guild_id: int,
    plugin_id: str,
    key: str,
    value: Any,
    db: aiosqlite.Connection | None = None,
    resource_overrides: dict | None = None,
) -> None:
    """
    寫入外掛專屬的 KV 資料，key 已存在則覆蓋。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        key: 資料鍵值
        value: 要儲存的值，必須是可以 JSON 序列化的型別
        db: 這次執行專用的連線（有 storage 能力時由 core/dispatcher.py 準備），
            None 代表用共用連線，交易邊界（commit／rollback）一律由呼叫端決定，
            這裡不呼叫 commit()，見 design.md 第 5.4.2 節
        resource_overrides: 已解析的資源限制，None 代表使用平台常數

    Raises:
        StorageLimitExceededError: key 長度、value 大小超過上限，或這個安裝已經用滿
            MAX_STORAGE_KEYS_PER_INSTALLATION 筆資料且這是一個新 key（覆蓋既有 key 不受此限）
    """
    key_length_limit = _limit_value(resource_overrides, "storage_key_length_limit", MAX_STORAGE_KEY_LENGTH)
    value_bytes_limit = _limit_value(resource_overrides, "storage_value_bytes_limit", MAX_STORAGE_VALUE_BYTES)
    keys_per_installation_limit = _limit_value(
        resource_overrides,
        "storage_keys_per_installation_limit",
        MAX_STORAGE_KEYS_PER_INSTALLATION,
    )

    if len(key) > key_length_limit:
        raise StorageLimitExceededError(f"key 長度超過上限（{key_length_limit} 字元）")

    value_json = json.dumps(value)
    if len(value_json.encode("utf-8")) > value_bytes_limit:
        raise StorageLimitExceededError(f"value 大小超過上限（{value_bytes_limit} bytes）")

    db = db or get_db()

    # 「檢查數量上限」跟「寫入」原本是兩個分開的陳述式（先 SELECT COUNT(*) 再
    # INSERT），中間有 TOCTOU 競態視窗：兩個併發呼叫可能同時通過檢查，實際
    # 寫入後總數超過上限。改成一個原子陳述式：SELECT 的 WHERE 子句同時決定
    # 「這個 key 已經存在（覆蓋不受數量限制）」或「還沒超過上限」才真的插入，
    # 兩個條件的判斷與寫入在同一個陳述式裡完成，SQLite 保證原子性；插入 0 筆
    # （WHERE 兩個條件都不成立）就代表被上限擋下。
    cursor = await db.execute(
        """
        INSERT INTO plugin_kv_store (guild_id, plugin_id, key, value_json, updated_at)
        SELECT ?, ?, ?, ?, ?
        WHERE EXISTS (
            SELECT 1 FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key = ?
        ) OR (
            SELECT COUNT(*) FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ?
        ) < ?
        ON CONFLICT (guild_id, plugin_id, key)
        DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
        """,
        (
            guild_id,
            plugin_id,
            key,
            value_json,
            _now_iso(),
            guild_id,
            plugin_id,
            key,
            guild_id,
            plugin_id,
            keys_per_installation_limit,
        ),
    )
    if cursor.rowcount == 0:
        raise StorageLimitExceededError(
            f"這個安裝的 storage key 數量已達上限（{keys_per_installation_limit} 筆）"
        )


async def storage_delete(guild_id: int, plugin_id: str, key: str, db: aiosqlite.Connection | None = None) -> None:
    """
    刪除外掛專屬的 KV 資料，key 不存在時安靜跳過。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        key: 資料鍵值
        db: 同 storage_set() 的說明
    """
    db = db or get_db()
    await db.execute(
        "DELETE FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key = ?",
        (guild_id, plugin_id, key),
    )


async def delete_all_storage_for_guild(guild_id: int) -> None:
    """
    刪除指定伺服器所有外掛的 KV 儲存資料，機器人被踢出伺服器時呼叫。

    只清 plugin_kv_store：plugin_scheduled_tasks 由
    core.repository.delete_all_installations_for_guild() 一併清掉，不用在
    這裡重複刪一次。

    Args:
        guild_id: 伺服器 ID
    """
    db = get_db()
    await db.execute("DELETE FROM plugin_kv_store WHERE guild_id = ?", (guild_id,))
    await db.commit()


async def delete_all_storage_for_plugin(plugin_id: str) -> None:
    """
    刪除指定外掛在所有伺服器的 KV 儲存資料，供停權/封鎖連鎖解除安裝使用。

    Args:
        plugin_id: 外掛 ID
    """
    db = get_db()
    await db.execute("DELETE FROM plugin_kv_store WHERE plugin_id = ?", (plugin_id,))
    await db.commit()


async def delete_storage_for_installation(guild_id: int, plugin_id: str) -> None:
    """
    刪除單一安裝的 KV 儲存資料，伺服器主動解除安裝外掛時呼叫。

    只清 plugin_kv_store：plugin_scheduled_tasks 由
    core.repository.delete_installation() 一併清掉，不用在這裡重複刪一次。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
    """
    db = get_db()
    await db.execute(
        "DELETE FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ?",
        (guild_id, plugin_id),
    )
    await db.commit()


async def storage_list_keys(
    guild_id: int, plugin_id: str, prefix: str, db: aiosqlite.Connection | None = None
) -> list[str]:
    """
    列舉指定前綴的所有 key。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        prefix: key 前綴，空字串代表列出全部
        db: 同 storage_set() 的說明

    Returns:
        符合前綴的 key 清單
    """
    db = db or get_db()
    async with db.execute(
        "SELECT key FROM plugin_kv_store WHERE guild_id = ? AND plugin_id = ? AND key LIKE ? ESCAPE '\\'",
        (guild_id, plugin_id, _escape_like_pattern(prefix) + "%"),
    ) as cursor:
        rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def storage_get_leaderboard(
    guild_id: int, plugin_id: str, prefix: str, limit: int, db: aiosqlite.Connection | None = None
) -> list[dict]:
    """
    依數值由大到小排序，回傳指定前綴底下的前 limit 筆資料，由宿主端排序，
    避免外掛在 Lua 裡自己排序耗盡執行步數。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        prefix: key 前綴
        limit: 最多回傳幾筆
        db: 同 storage_set() 的說明

    Returns:
        list of {"key": str, "value": int | float}，只包含值為數字的項目，
        非數字的值（例如字串、巢狀物件）會被跳過，不計入排行榜
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > MAX_LEADERBOARD_LIMIT:
        raise StorageLimitExceededError(f"leaderboard limit 必須介於 1 到 {MAX_LEADERBOARD_LIMIT}")

    db = db or get_db()
    async with db.execute(
        """
        SELECT key, json_extract(value_json, '$') AS numeric_value
        FROM plugin_kv_store
        WHERE guild_id = ?
          AND plugin_id = ?
          AND key LIKE ? ESCAPE '\\'
          AND json_type(value_json, '$') IN ('integer', 'real')
        ORDER BY numeric_value DESC, key ASC
        LIMIT ?
        """,
        (guild_id, plugin_id, _escape_like_pattern(prefix) + "%", limit),
    ) as cursor:
        rows = await cursor.fetchall()
    return [{"key": key, "value": value} for key, value in rows]


async def create_scheduled_task(
    guild_id: int,
    plugin_id: str,
    delay_seconds: float,
    task_name: str,
    payload: dict,
    recurring_interval_seconds: int | None = None,
    db: aiosqlite.Connection | None = None,
) -> str:
    """
    建立一筆排程任務，時間到由 Track D 的排程消費迴圈觸發 on_scheduled_task 事件。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        delay_seconds: 幾秒後執行
        task_name: 任務名稱，會原樣傳回 on_scheduled_task 事件的 payload
        payload: 任務資料，會原樣傳回 on_scheduled_task 事件的 payload
        recurring_interval_seconds: 週期性任務的重複間隔秒數，None 代表只執行一次
        db: 同 storage_set() 的說明

    Returns:
        新建立的任務 ID，可用於之後呼叫 cancel_scheduled_task 取消
    """
    if (
        not isinstance(delay_seconds, (int, float))
        or isinstance(delay_seconds, bool)
        or delay_seconds < MIN_SCHEDULE_DELAY_SECONDS
        or delay_seconds > MAX_SCHEDULE_DELAY_SECONDS
    ):
        raise ScheduledTaskLimitExceededError(
            f"delay_seconds 必須介於 {MIN_SCHEDULE_DELAY_SECONDS} 到 {MAX_SCHEDULE_DELAY_SECONDS} 秒"
        )
    if not isinstance(task_name, str) or not task_name or len(task_name) > MAX_SCHEDULED_TASK_NAME_LENGTH:
        raise ScheduledTaskLimitExceededError(
            f"task_name 長度必須介於 1 到 {MAX_SCHEDULED_TASK_NAME_LENGTH} 字元"
        )
    if not isinstance(payload, dict):
        raise ScheduledTaskLimitExceededError("payload 必須是 JSON 物件")
    if recurring_interval_seconds is not None and (
        not isinstance(recurring_interval_seconds, int)
        or isinstance(recurring_interval_seconds, bool)
        or recurring_interval_seconds < MIN_RECURRING_INTERVAL_SECONDS
    ):
        raise ScheduledTaskLimitExceededError(
            f"recurring_interval_seconds 必須至少 {MIN_RECURRING_INTERVAL_SECONDS} 秒"
        )

    payload_json = json.dumps({"task_name": task_name, "payload": payload})
    if len(payload_json.encode("utf-8")) > MAX_SCHEDULED_TASK_PAYLOAD_BYTES:
        raise ScheduledTaskLimitExceededError(
            f"payload 大小超過上限（{MAX_SCHEDULED_TASK_PAYLOAD_BYTES} bytes）"
        )

    db = db or get_db()

    # 同 storage_set()：數量檢查跟寫入合併成一個原子陳述式，避免併發下兩個
    # 呼叫同時通過 COUNT 檢查、實際超過上限（見 design.md 第 12.3 節）。
    task_id = str(uuid.uuid4())
    run_at = datetime.datetime.fromtimestamp(
        time.time() + delay_seconds, tz=datetime.timezone.utc
    ).isoformat()
    cursor = await db.execute(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        SELECT ?, ?, ?, ?, ?, ?
        WHERE (
            SELECT COUNT(*) FROM plugin_scheduled_tasks WHERE guild_id = ? AND plugin_id = ?
        ) < ?
        """,
        (
            task_id,
            guild_id,
            plugin_id,
            run_at,
            payload_json,
            recurring_interval_seconds,
            guild_id,
            plugin_id,
            MAX_SCHEDULED_TASKS_PER_INSTALLATION,
        ),
    )
    if cursor.rowcount == 0:
        raise ScheduledTaskLimitExceededError(
            f"這個安裝的排程任務數量已達上限（{MAX_SCHEDULED_TASKS_PER_INSTALLATION} 筆）"
        )
    return task_id


async def cancel_scheduled_task(
    guild_id: int, plugin_id: str, task_id: str, db: aiosqlite.Connection | None = None
) -> bool:
    """
    取消一筆尚未執行的排程任務，只能取消自己外掛在自己伺服器安裝底下建立的任務。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        task_id: 要取消的任務 ID
        db: 同 storage_set() 的說明

    Returns:
        True 表示確實刪除了一筆；False 表示找不到（可能已經執行過或 ID 錯誤）
    """
    db = db or get_db()
    cursor = await db.execute(
        "DELETE FROM plugin_scheduled_tasks WHERE task_id = ? AND guild_id = ? AND plugin_id = ?",
        (task_id, guild_id, plugin_id),
    )
    return cursor.rowcount > 0
