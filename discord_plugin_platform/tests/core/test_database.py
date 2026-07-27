"""
core/database.py 的舊資料庫升級測試：驗證 _add_column_if_missing() 真的能讓
一個「建表時還沒有 version 欄位」的舊 plugin_review_log 資料表安全升級，
且既有資料不會被升級過程弄丟。
"""

from pathlib import Path

import aiosqlite
import pytest

from core import database


def test_resolve_db_path_is_independent_of_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """平台預設與相對覆寫路徑都必須以平台根目錄解析。"""
    monkeypatch.chdir(tmp_path)

    assert database.resolve_db_path(None) == database.DEFAULT_DB_PATH.resolve()
    assert database.resolve_db_path("custom/platform.db") == (database.PROJECT_ROOT / "custom/platform.db").resolve()


@pytest.fixture
async def old_schema_db_path(tmp_path):
    """
    建立一個模擬舊版本的資料庫檔案：plugin_review_log 資料表存在，
    但缺少後來才加入的 version 欄位，且已經有一筆既有資料。
    """
    db_path = tmp_path / "old_schema.db"
    connection = await aiosqlite.connect(str(db_path))
    await connection.execute(
        """
        CREATE TABLE plugin_review_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            plugin_id TEXT NOT NULL,
            reviewer_action TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    await connection.execute(
        "INSERT INTO plugin_review_log (plugin_id, reviewer_action, reason, created_at) VALUES (?, ?, ?, ?)",
        ("legacy_plugin", "approved", "舊資料", "2025-01-01T00:00:00"),
    )
    await connection.commit()
    await connection.close()
    return str(db_path)


async def test_init_db_adds_missing_column_to_existing_table(monkeypatch, old_schema_db_path):
    monkeypatch.setattr(database, "DB_PATH", old_schema_db_path)

    await database.init_db()
    try:
        connection = database.get_db()
        async with connection.execute("PRAGMA table_info(plugin_review_log)") as cursor:
            columns = {row[1] async for row in cursor}
        assert "version" in columns

        async with connection.execute(
            "SELECT plugin_id, reviewer_action, reason, version FROM plugin_review_log"
        ) as cursor:
            rows = await cursor.fetchall()
        assert rows == [("legacy_plugin", "approved", "舊資料", None)]
    finally:
        await database.close_db()


async def test_init_db_on_old_schema_is_idempotent(monkeypatch, old_schema_db_path):
    monkeypatch.setattr(database, "DB_PATH", old_schema_db_path)

    await database.init_db()
    await database.close_db()
    # 舊資料庫升級後再次啟動（例如機器人重啟），不能因為欄位已存在就出錯。
    await database.init_db()
    try:
        connection = database.get_db()
        async with connection.execute("PRAGMA table_info(plugin_review_log)") as cursor:
            columns = {row[1] async for row in cursor}
        assert "version" in columns
    finally:
        await database.close_db()
