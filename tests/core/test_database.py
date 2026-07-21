from pathlib import Path

from core import database


def test_resolve_db_path_is_independent_of_working_directory(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    """資料庫預設與相對覆寫路徑都必須以專案根目錄解析。"""
    monkeypatch.chdir(tmp_path)

    assert database.resolve_db_path(None) == database.DEFAULT_DB_PATH.resolve()
    assert database.resolve_db_path("custom/bot.db") == (database.PROJECT_ROOT / "custom/bot.db").resolve()
