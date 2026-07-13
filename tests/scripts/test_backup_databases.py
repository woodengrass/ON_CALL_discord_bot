import datetime
import shutil
import sqlite3
import subprocess
from pathlib import Path

from scripts import backup_databases


def _create_sqlite_db(path: Path, value: str) -> None:
    """
    建立一個內含單一列資料的暫存 SQLite 資料庫，供測試驗證備份內容完整。

    Args:
        path: 要建立的資料庫檔案路徑
        value: 寫入資料表的內容，供之後斷言備份檔案是否保留了正確資料
    """
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE marker (value TEXT)")
    connection.execute("INSERT INTO marker (value) VALUES (?)", (value,))
    connection.commit()
    connection.close()


def _read_marker_value(path: Path) -> str:
    """
    讀取備份檔案裡 marker 資料表的內容，用於驗證備份資料正確。
    """
    connection = sqlite3.connect(str(path))
    try:
        row = connection.execute("SELECT value FROM marker").fetchone()
    finally:
        connection.close()
    return row[0]


def test_backup_one_database_creates_consistent_copy(tmp_path: Path, monkeypatch) -> None:
    """
    備份成功時，輸出檔案應包含與來源資料庫相同的資料。
    """
    monkeypatch.setattr(backup_databases, "BACKUP_ROOT", tmp_path / "backups")
    source_path = tmp_path / "source.db"
    _create_sqlite_db(source_path, "hello")
    target = backup_databases.DatabaseTarget(name="test_db", source_path=source_path)
    backup_date = datetime.date(2026, 7, 13)

    backup_path = backup_databases.backup_one_database(target, backup_date)

    assert backup_path == tmp_path / "backups" / "test_db" / "2026-07-13.db"
    assert backup_path.exists()
    assert _read_marker_value(backup_path) == "hello"


def test_backup_one_database_missing_source_returns_none(tmp_path: Path, monkeypatch) -> None:
    """
    來源資料庫不存在時應跳過，回傳 None，不當成錯誤讓整個腳本失敗。
    """
    monkeypatch.setattr(backup_databases, "BACKUP_ROOT", tmp_path / "backups")
    target = backup_databases.DatabaseTarget(name="missing_db", source_path=tmp_path / "does_not_exist.db")

    result = backup_databases.backup_one_database(target, datetime.date(2026, 7, 13))

    assert result is None


def test_rotate_old_backups_keeps_recent_and_monthly(tmp_path: Path, monkeypatch) -> None:
    """
    輪替應刪除超過保留天數的每日備份，但每月 1 號的備份不論多舊都保留。
    """
    monkeypatch.setattr(backup_databases, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(backup_databases, "DAILY_RETENTION_DAYS", 7)
    target = backup_databases.DatabaseTarget(name="rotate_db", source_path=tmp_path / "unused.db")
    destination_directory = tmp_path / "backups" / target.name
    destination_directory.mkdir(parents=True)

    today = datetime.date(2026, 7, 13)
    recent_file = destination_directory / "2026-07-10.db"
    old_file = destination_directory / "2026-06-15.db"
    old_but_first_of_month_file = destination_directory / "2026-05-01.db"
    for file_path in (recent_file, old_file, old_but_first_of_month_file):
        file_path.write_text("placeholder")

    backup_databases.rotate_old_backups(target, today)

    assert recent_file.exists()
    assert not old_file.exists()
    assert old_but_first_of_month_file.exists()


def test_sync_to_rclone_remote_skips_when_env_var_missing(monkeypatch) -> None:
    """
    未設定 rclone 遠端環境變數時，視為不需要同步，回傳 True 且不呼叫 subprocess。
    """
    monkeypatch.delenv(backup_databases.RCLONE_REMOTE_ENV_VAR, raising=False)
    subprocess_called = False

    def fake_run(*args, **kwargs):
        nonlocal subprocess_called
        subprocess_called = True

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert backup_databases.sync_to_rclone_remote() is True
    assert subprocess_called is False


def test_sync_to_rclone_remote_fails_when_rclone_not_found(monkeypatch) -> None:
    """
    設定了遠端但機器上找不到 rclone 執行檔時應回傳 False，並記錄清楚的錯誤。
    """
    monkeypatch.setenv(backup_databases.RCLONE_REMOTE_ENV_VAR, "gdrive:test-remote")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    assert backup_databases.sync_to_rclone_remote() is False


def test_sync_to_rclone_remote_success(monkeypatch) -> None:
    """
    rclone 存在且執行成功時應回傳 True，並用 copy（非 sync）避免刪除雲端既有備份。
    """
    monkeypatch.setenv(backup_databases.RCLONE_REMOTE_ENV_VAR, "gdrive:test-remote")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rclone")
    captured_command: list[str] = []

    def fake_run(command, capture_output, text):
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert backup_databases.sync_to_rclone_remote() is True
    assert "copy" in captured_command
    assert "sync" not in captured_command
    assert "gdrive:test-remote" in captured_command


def test_sync_to_rclone_remote_reports_failure(monkeypatch) -> None:
    """
    rclone 指令執行失敗（非零 exit code）時應回傳 False。
    """
    monkeypatch.setenv(backup_databases.RCLONE_REMOTE_ENV_VAR, "gdrive:test-remote")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/rclone")

    def fake_run(command, capture_output, text):
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="auth error")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert backup_databases.sync_to_rclone_remote() is False


def test_main_backs_up_and_rotates_without_rclone(tmp_path: Path, monkeypatch) -> None:
    """
    端到端：main() 應成功備份所有存在的資料庫、跳過不存在的資料庫，
    未設定 rclone 時整體視為成功。
    """
    monkeypatch.setattr(backup_databases, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(backup_databases, "LOG_DIRECTORY", tmp_path / "logs")
    monkeypatch.setattr(backup_databases, "LOG_FILE", tmp_path / "logs" / "backup.log")
    monkeypatch.delenv(backup_databases.RCLONE_REMOTE_ENV_VAR, raising=False)

    existing_db_path = tmp_path / "existing.db"
    _create_sqlite_db(existing_db_path, "present")
    monkeypatch.setattr(
        backup_databases,
        "DATABASE_TARGETS",
        [
            backup_databases.DatabaseTarget(name="existing_db", source_path=existing_db_path),
            backup_databases.DatabaseTarget(name="missing_db", source_path=tmp_path / "missing.db"),
        ],
    )

    exit_code = backup_databases.main()

    assert exit_code == 0
    assert (tmp_path / "backups" / "existing_db" / f"{datetime.date.today().isoformat()}.db").exists()
    assert not (tmp_path / "backups" / "missing_db").exists()


def test_main_reports_failure_when_backup_fails(tmp_path: Path, monkeypatch) -> None:
    """
    來源資料庫存在但備份過程失敗時（例如檔案損毀導致 sqlite3 拋例外），
    main() 應回傳非零結束碼，讓排程器能偵測到失敗。
    """
    monkeypatch.setattr(backup_databases, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(backup_databases, "LOG_DIRECTORY", tmp_path / "logs")
    monkeypatch.setattr(backup_databases, "LOG_FILE", tmp_path / "logs" / "backup.log")
    monkeypatch.delenv(backup_databases.RCLONE_REMOTE_ENV_VAR, raising=False)

    corrupted_db_path = tmp_path / "corrupted.db"
    corrupted_db_path.write_text("not a real sqlite file")
    monkeypatch.setattr(
        backup_databases,
        "DATABASE_TARGETS",
        [backup_databases.DatabaseTarget(name="corrupted_db", source_path=corrupted_db_path)],
    )

    exit_code = backup_databases.main()

    assert exit_code == 1
