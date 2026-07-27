import logging
import os
import re
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "bot.db"


def resolve_db_path(configured_path: str | None) -> Path:
    """
    將資料庫設定值解析為固定的絕對路徑，避免受啟動工作目錄影響。

    Args:
        configured_path: 環境變數提供的路徑；未設定時使用專案預設路徑

    Returns:
        已解析的絕對資料庫路徑
    """
    database_path = Path(configured_path) if configured_path else DEFAULT_DB_PATH
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return database_path.resolve()


DB_PATH = resolve_db_path(os.getenv("BOT_DB_PATH"))

_connection: aiosqlite.Connection | None = None


async def init_db() -> None:
    """
    初始化資料庫連線並建立所需的資料表，機器人啟動時只需呼叫一次。
    """
    global _connection
    database_path = Path(DB_PATH)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("使用資料庫路徑：%s", database_path.resolve())
    _connection = await aiosqlite.connect(str(database_path))
    await _connection.execute("PRAGMA journal_mode=WAL")

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS triggers (
            guild_id INTEGER NOT NULL,
            trigger_word TEXT NOT NULL,
            response_json TEXT NOT NULL,
            wildcard INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, trigger_word)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS link_keywords (
            keyword TEXT PRIMARY KEY
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS moderation_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_moderation_log_guild ON moderation_log (guild_id)"
    )
    await _connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_moderation_log_user ON moderation_log (user_id)"
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_verifications (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            status TEXT NOT NULL,
            review_channel_id INTEGER,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    await _add_column_if_missing("pending_verifications", "review_channel_id", "INTEGER")

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ticket_panels (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            PRIMARY KEY (guild_id, message_id)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            user_id INTEGER NOT NULL,
            reason TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (guild_id, channel_id)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_panels (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER,
            buttons_json TEXT NOT NULL
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS warnings (
            warning_id TEXT PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER,
            role_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            schedule_json TEXT NOT NULL,
            content_json TEXT NOT NULL
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER NOT NULL,
            section TEXT NOT NULL,
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            PRIMARY KEY (guild_id, section, key)
        )
        """
    )

    await _connection.execute(
        """
        CREATE TABLE IF NOT EXISTS scam_image_hashes (
            phash TEXT PRIMARY KEY,
            label TEXT,
            added_at TEXT NOT NULL
        )
        """
    )

    await _connection.commit()


_SQL_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_COLUMN_TYPES = {"INTEGER", "TEXT", "REAL", "BLOB"}


async def _add_column_if_missing(table: str, column: str, column_type: str) -> None:
    """
    若指定資料表缺少某欄位則新增，讓舊版資料庫也能安全升級到新 schema。
    這個函式只能傳入寫死在程式碼裡的信任值，絕不能接受外部輸入，
    因為 table/column/column_type 是直接組進 SQL 字串的，這裡做的白名單檢查只是防呆，不是防注入的完整解法。

    Args:
        table: 資料表名稱
        column: 欄位名稱
        column_type: 欄位型別（SQL 型別字串）

    Raises:
        ValueError: 若 table/column 不是合法的 SQL 識別字，或 column_type 不在允許清單內
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
    關閉資料庫連線，機器人結束時呼叫。
    """
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
