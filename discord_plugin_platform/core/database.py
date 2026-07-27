import logging
import os
import re
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "plugin_platform.db"


def resolve_db_path(configured_path: str | None) -> Path:
    """
    將平台資料庫設定值解析為固定的絕對路徑，避免服務工作目錄不同而分裂資料。

    Args:
        configured_path: 環境變數提供的路徑；未設定時使用平台預設路徑

    Returns:
        已解析的絕對資料庫路徑
    """
    database_path = Path(configured_path) if configured_path else DEFAULT_DB_PATH
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return database_path.resolve()


DB_PATH = resolve_db_path(os.getenv("PLUGIN_PLATFORM_DB_PATH"))

_connection: aiosqlite.Connection | None = None


async def init_db() -> None:
    """
    初始化資料庫連線並建立所需的資料表，平台啟動時只需呼叫一次。
    """
    global _connection
    database_path = Path(DB_PATH)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("使用平台資料庫路徑：%s", database_path.resolve())
    _connection = await aiosqlite.connect(str(database_path))
    await _connection.execute("PRAGMA journal_mode=WAL")

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugins (
            plugin_id TEXT PRIMARY KEY,
            author_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            latest_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review',
            pricing_tier TEXT NOT NULL DEFAULT 'free'
        )
        """
    )
    await _add_column_if_missing("plugins", "pricing_tier", "TEXT")

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_versions (
            plugin_id TEXT NOT NULL,
            version TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            source_code TEXT NOT NULL,
            capability_api_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (plugin_id, version)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_installations (
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            installed_version TEXT NOT NULL,
            granted_capabilities_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            execution_quota_override INTEGER,
            action_quota_override INTEGER,
            resource_overrides_json TEXT,
            installed_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, plugin_id)
        )
        """
    )
    await _add_column_if_missing("plugin_installations", "resource_overrides_json", "TEXT")

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS resource_tiers (
            tier_name TEXT PRIMARY KEY,
            display_order INTEGER NOT NULL,
            description TEXT NOT NULL
        )
        """
    )
    await _connection.execute(
        """
        INSERT OR IGNORE INTO resource_tiers (tier_name, display_order, description)
        VALUES ('default', 0, '平台預設方案')
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_tier_config (
            plugin_id TEXT NOT NULL,
            tier_name TEXT NOT NULL,
            allowed INTEGER NOT NULL,
            execution_quota INTEGER,
            action_quota INTEGER,
            storage_key_length_limit INTEGER,
            storage_value_bytes_limit INTEGER,
            storage_keys_per_installation_limit INTEGER,
            instruction_limit INTEGER,
            memory_limit_bytes INTEGER,
            PRIMARY KEY (plugin_id, tier_name)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_resource_tiers (
            guild_id INTEGER PRIMARY KEY,
            tier_name TEXT NOT NULL,
            assigned_at TEXT NOT NULL
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_installation_blocks (
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            blocked_at TEXT NOT NULL,
            reason TEXT,
            PRIMARY KEY (guild_id, plugin_id)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS rejection_reason_presets (
            preset_id TEXT PRIMARY KEY,
            label TEXT NOT NULL
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_notifications (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            notification_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sent_at TEXT
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_kv_store (
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (guild_id, plugin_id, key)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_scheduled_tasks (
            task_id TEXT PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            run_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            recurring_interval_seconds INTEGER
        )
        """
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_run_at ON plugin_scheduled_tasks (run_at)"
    )
    await _connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_run_at_plugin
        ON plugin_scheduled_tasks (run_at, plugin_id)
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_execution_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            plugin_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            execution_ms INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_execution_log_guild_plugin ON plugin_execution_log (guild_id, plugin_id)"
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_review_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            plugin_id TEXT NOT NULL,
            version TEXT,
            reviewer_action TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await _add_column_if_missing("plugin_review_log", "version", "TEXT")
    await _add_column_if_missing("plugin_review_log", "reason_presets_json", "TEXT")
    await _add_column_if_missing("plugin_review_log", "flagged_capabilities_json", "TEXT")
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_plugin_review_log_plugin ON plugin_review_log (plugin_id)"
    )

    await _connection.commit()


_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_COLUMN_TYPES = {"INTEGER", "TEXT", "REAL", "BLOB"}


async def _add_column_if_missing(table: str, column: str, column_type: str) -> None:
    """
    若指定資料表缺少欄位則新增，讓既有開發資料庫能安全升級。

    Args:
        table: 資料表名稱
        column: 欄位名稱
        column_type: 欄位型別

    Raises:
        ValueError: table/column/column_type 不合法時拋出
    """
    if not _SQL_IDENTIFIER_PATTERN.match(table) or not _SQL_IDENTIFIER_PATTERN.match(column):
        raise ValueError(f"不合法的資料表或欄位名稱：table={table}, column={column}")
    if column_type not in _ALLOWED_COLUMN_TYPES:
        raise ValueError(f"不合法的欄位型別：{column_type}")

    async with _connection.execute(f"PRAGMA table_info({table})") as cursor:
        columns = {row[1] async for row in cursor}
    if column not in columns:
        await _connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def get_db() -> aiosqlite.Connection:
    """
    取得目前的資料庫連線，需先呼叫過 init_db()。

    Returns:
        已建立的 aiosqlite 連線

    Raises:
        RuntimeError: 若尚未呼叫 init_db() 初始化連線
    """
    if _connection is None:
        raise RuntimeError("資料庫尚未初始化，請先呼叫 init_db()")
    return _connection


async def close_db() -> None:
    """
    關閉資料庫連線，平台結束時呼叫。
    """
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
