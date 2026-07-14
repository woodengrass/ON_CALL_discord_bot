"""
排程執行的資料庫備份腳本：本地輪替備份 + 選填的 rclone 異地同步（Google 雲端硬碟）。

用法：
    python scripts/backup_databases.py

排程建議：
    Linux（cron）：0 3 * * * cd /path/to/repo && /path/to/venv/bin/python scripts/backup_databases.py
    Windows（工作排程器）：每日觸發，動作為 python scripts\\backup_databases.py，工作目錄設為本專案根目錄

備份對象用 SQLite 官方 Online Backup API（Connection.backup()），不是複製檔案——
WAL 模式下直接複製 .db 檔可能拿到寫入中途的不一致狀態，backup() 保證即使有人正在
寫入也能拿到一致快照，這是唯一該用的方法。

異地同步（可選）：設定環境變數 BACKUP_RCLONE_REMOTE（例如 "gdrive:honeypot-bot-backups"），
且本機已裝好 rclone 並跑過一次 `rclone config` 授權 Google 雲端硬碟，腳本會在本地備份
完成後呼叫 `rclone copy` 把整個 backups/ 資料夾同步上去。刻意用 copy 不用 sync：
sync 會把本地輪替刪掉的舊備份也從雲端硬碟砍掉，copy 只增量上傳、絕不刪除雲端既有檔案，
異地那份的保留時間由使用者自己在雲端硬碟管理，不受本地輪替政策影響。
沒設定 BACKUP_RCLONE_REMOTE 或找不到 rclone 執行檔時，只做本地備份，不視為失敗。
"""

import datetime
import logging
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_ROOT = PROJECT_ROOT / "backups"
LOG_DIRECTORY = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIRECTORY / "backup.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

DAILY_RETENTION_DAYS = 7
RCLONE_REMOTE_ENV_VAR = "BACKUP_RCLONE_REMOTE"

logger = logging.getLogger(__name__)


@dataclass
class DatabaseTarget:
    """
    要備份的單一資料庫來源。

    Attributes:
        name: 備份輸出子資料夾名稱
        source_path: 資料庫檔案路徑
    """

    name: str
    source_path: Path


DATABASE_TARGETS: list[DatabaseTarget] = [
    DatabaseTarget(name="bot_db", source_path=PROJECT_ROOT / "data" / "bot.db"),
    # discord_plugin_platform 的資料庫等 Track L 落地、實際產生資料後再加入這裡，
    # 例如 DatabaseTarget(name="plugin_platform_db",
    #                     source_path=PROJECT_ROOT / "discord_plugin_platform" / "data" / "plugin_platform.db")
]


def configure_backup_logging() -> None:
    """
    設定備份腳本專用的終端機與輪替檔案日誌，寫進獨立的 logs/backup.log，
    不與 bot 主行程的 logs/bot.log 混在一起（避免排程執行時互相干擾輪替時機）。
    """
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


def backup_one_database(target: DatabaseTarget, backup_date: datetime.date) -> Path | None:
    """
    用 SQLite Online Backup API 備份單一資料庫檔案。

    Args:
        target: 要備份的資料庫
        backup_date: 這次備份對應的日期，用於輸出檔名

    Returns:
        成功時回傳備份檔案路徑；來源檔案不存在或備份失敗時回傳 None
    """
    if not target.source_path.exists():
        logger.warning(f"跳過 {target.name}：找不到資料庫檔案 {target.source_path}")
        return None

    destination_directory = BACKUP_ROOT / target.name
    destination_directory.mkdir(parents=True, exist_ok=True)
    destination_path = destination_directory / f"{backup_date.isoformat()}.db"

    try:
        source_connection = sqlite3.connect(str(target.source_path))
        try:
            destination_connection = sqlite3.connect(str(destination_path))
            try:
                source_connection.backup(destination_connection)
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
    except Exception as error:
        logger.error(f"備份 {target.name} 失敗：{error}", exc_info=True)
        if destination_path.exists():
            destination_path.unlink()
        return None

    logger.info(f"已備份 {target.name} -> {destination_path}")
    return destination_path


def rotate_old_backups(target: DatabaseTarget, today: datetime.date) -> None:
    """
    刪除超過保留天數的每日備份，每月 1 號的備份額外長期保留、不被輪替刪除。

    Args:
        target: 要輪替的資料庫備份目錄
        today: 執行輪替當下的日期
    """
    destination_directory = BACKUP_ROOT / target.name
    if not destination_directory.exists():
        return

    cutoff_date = today - datetime.timedelta(days=DAILY_RETENTION_DAYS)
    for backup_file in destination_directory.glob("*.db"):
        try:
            backup_date = datetime.date.fromisoformat(backup_file.stem)
        except ValueError:
            continue
        if backup_date.day == 1:
            continue
        if backup_date < cutoff_date:
            backup_file.unlink()
            logger.info(f"已刪除過期備份：{backup_file}")


def sync_to_rclone_remote() -> bool:
    """
    若有設定 rclone 遠端，把整個 backups/ 資料夾用 rclone copy 同步過去。

    Returns:
        True 表示同步成功或本來就不需要同步（未設定遠端）；False 表示設定了但同步失敗
    """
    remote = os.environ.get(RCLONE_REMOTE_ENV_VAR)
    if not remote:
        logger.info(f"未設定環境變數 {RCLONE_REMOTE_ENV_VAR}，跳過異地同步。")
        return True

    rclone_executable = shutil.which("rclone")
    if rclone_executable is None:
        logger.error(f"已設定 {RCLONE_REMOTE_ENV_VAR}={remote} 但找不到 rclone 執行檔，異地同步失敗。")
        return False

    command = [rclone_executable, "copy", str(BACKUP_ROOT), remote, "--checksum"]
    logger.info(f"開始 rclone 同步：{' '.join(command)}")
    completed_process = subprocess.run(command, capture_output=True, text=True)
    if completed_process.returncode != 0:
        logger.error(f"rclone 同步失敗（exit={completed_process.returncode}）：{completed_process.stderr}")
        return False

    logger.info("rclone 同步完成。")
    return True


def main() -> int:
    """
    執行所有資料庫的備份、輪替與異地同步。

    Returns:
        0 表示全部成功；1 表示至少一項失敗
    """
    configure_backup_logging()
    today = datetime.date.today()

    all_succeeded = True
    for target in DATABASE_TARGETS:
        backup_path = backup_one_database(target, today)
        if backup_path is None and target.source_path.exists():
            all_succeeded = False
        rotate_old_backups(target, today)

    if not sync_to_rclone_remote():
        all_succeeded = False

    if all_succeeded:
        logger.info("本次備份全部成功。")
    else:
        logger.error("本次備份有項目失敗，請查看上面的錯誤訊息。")
    return 0 if all_succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
