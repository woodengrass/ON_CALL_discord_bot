"""
終端機管理指令，比照現有 honeypot-discord-bot 專案的 admin/console.py 模式。
這是 v1 唯一給平台操作者用的控制介面，跟第 3.4 節的公開市集網頁完全分開，
滿足第 3.5 節「管理功能不能跟公開網頁共用同一個應用」的安全要求。
第二、三階段開發重點（外掛安裝管理、配額調整），見 design.md 第 5.4.1 節。
"""

import logging
import re
import shlex

from core import admin_operations, plugin_storage_repository, repository, suspension
from core.database import get_db
from core.manifest import ManifestValidationError
from sandbox import engine

logger = logging.getLogger(__name__)

COMMAND_HELP: tuple[tuple[str, str], ...] = (
    ("admin plugin list", "列出所有外掛"),
    ("admin plugin show <plugin_id>", "顯示單一外掛詳細資料"),
    ("admin plugin review list", "列出待審核外掛"),
    ("admin plugin review approve <plugin_id>", "核准待審核外掛，轉為上架狀態"),
    ("admin plugin review reject <plugin_id> <reason>", "退回待審核外掛並記錄原因"),
    ("admin plugin install <guild_id> <plugin_id>", "安裝外掛到指定伺服器"),
    ("admin plugin uninstall <guild_id> <plugin_id>", "從指定伺服器移除外掛"),
    ("admin plugin suspend <plugin_id>", "停權指定外掛（跨所有安裝）"),
    ("admin plugin unsuspend <plugin_id>", "解除指定外掛停權"),
    ("admin plugin quota set <guild_id> <plugin_id> execution=<次數> action=<次數>", "調整指定安裝的動態配額"),
    (
        "admin plugin stats <plugin_id> [guild=<guild_id>] [since=<ISO時間>]",
        "彙總外掛執行紀錄：成功率、各結果比例、平均/p95 耗時",
    ),
)
HELP_TEXT = "[外掛平台管理工具] 可用指令（直接在終端機輸入後按 Enter）：\n" + "\n".join(
    f"  {usage:<68} {description}" for usage, description in COMMAND_HELP
)

QUOTA_NAMES = {"execution", "action"}
MESSAGE_CACHE_EVENTS = {"on_message_edit", "on_message_delete"}
MIN_QUOTA_OVERRIDE = 0
MAX_EXECUTION_QUOTA_OVERRIDE = 10_000
MAX_ACTION_QUOTA_OVERRIDE = 10_000
MAX_DISCORD_SNOWFLAKE = 2**64 - 1
PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _parse_guild_id(value: str) -> int:
    """
    解析並驗證 guild_id，提供比 int() 例外更清楚的錯誤訊息。

    Args:
        value: 指令中的 guild_id 文字

    Returns:
        guild_id 整數

    Raises:
        ValueError: 格式不是正整數或超出 Discord snowflake 範圍
    """
    if not value.isdecimal():
        raise ValueError("guild_id 必須是正整數")
    guild_id = int(value)
    if guild_id < 1 or guild_id > MAX_DISCORD_SNOWFLAKE:
        raise ValueError(f"guild_id 必須介於 1 到 {MAX_DISCORD_SNOWFLAKE}")
    return guild_id


def _validate_plugin_id(plugin_id: str) -> str:
    """
    驗證 plugin_id 格式，避免空字串或不可讀的控制字元進入管理指令。

    Args:
        plugin_id: 指令中的外掛 ID

    Returns:
        原始 plugin_id

    Raises:
        ValueError: plugin_id 格式不合法
    """
    if not PLUGIN_ID_PATTERN.match(plugin_id):
        raise ValueError("plugin_id 必須是 1-128 字元的英數、底線、連字號或句點")
    return plugin_id


def _parse_quota_value(value: str) -> int | None:
    """
    解析配額參數值。

    Args:
        value: 指令中 execution= 或 action= 後面的文字

    Returns:
        int 表示指定配額；None 表示恢復平台預設值
    """
    if value.lower() in {"none", "null", "default"}:
        return None
    parsed_value = int(value)
    if parsed_value < MIN_QUOTA_OVERRIDE:
        raise ValueError("配額不能是負數")
    return parsed_value


def _parse_quota_arguments(arguments: list[str]) -> tuple[int | None, int | None]:
    """
    解析 quota set 指令的 execution/action 參數。

    Args:
        arguments: 指令中剩餘的 key=value 參數

    Returns:
        execution 與 action 配額覆蓋值

    Raises:
        ValueError: 參數格式錯誤或名稱不支援
    """
    quota_values: dict[str, int | None] = {"execution": None, "action": None}
    seen_names: set[str] = set()
    for argument in arguments:
        if "=" not in argument:
            raise ValueError(f"配額參數格式錯誤：{argument}")
        name, value = argument.split("=", 1)
        if name not in QUOTA_NAMES:
            raise ValueError(f"未知的配額名稱：{name}")
        quota_values[name] = _parse_quota_value(value)
        seen_names.add(name)
    if seen_names != QUOTA_NAMES:
        raise ValueError("quota set 必須同時提供 execution= 與 action=")
    if (
        quota_values["execution"] is not None
        and quota_values["execution"] > MAX_EXECUTION_QUOTA_OVERRIDE
    ):
        raise ValueError(f"execution 配額不能超過 {MAX_EXECUTION_QUOTA_OVERRIDE}")
    if quota_values["action"] is not None and quota_values["action"] > MAX_ACTION_QUOTA_OVERRIDE:
        raise ValueError(f"action 配額不能超過 {MAX_ACTION_QUOTA_OVERRIDE}")
    return quota_values["execution"], quota_values["action"]


async def _handle_list_command() -> None:
    """
    列出目前所有外掛中繼資料。
    """
    plugins = await repository.list_plugins()
    if not plugins:
        print("目前沒有任何外掛。")
        return
    for plugin in plugins:
        print(
            f"{plugin['plugin_id']} | {plugin['name']} | "
            f"version={plugin['latest_version']} | status={plugin['status']}"
        )


async def _handle_show_command(plugin_id: str) -> None:
    """
    顯示單一外掛的中繼資料與最新版本 manifest。

    Args:
        plugin_id: 外掛 ID
    """
    plugin_id = _validate_plugin_id(plugin_id)
    plugin = await repository.get_plugin(plugin_id)
    if plugin is None:
        print(f"找不到外掛：{plugin_id}")
        return
    print(
        f"{plugin['plugin_id']} | {plugin['name']} | author={plugin['author_id']} | "
        f"version={plugin['latest_version']} | status={plugin['status']}"
    )
    manifest_json = await repository.get_plugin_manifest(plugin_id, plugin["latest_version"])
    if manifest_json is not None:
        print(f"manifest={manifest_json}")


async def _handle_install_command(guild_id_text: str, plugin_id: str) -> None:
    """
    將已核准外掛安裝到指定伺服器。

    Args:
        guild_id_text: 指令中的伺服器 ID 文字
        plugin_id: 外掛 ID
    """
    guild_id = _parse_guild_id(guild_id_text)
    plugin_id = _validate_plugin_id(plugin_id)
    await admin_operations.install_plugin(guild_id, plugin_id)
    print(f"已安裝外掛 {plugin_id} 到伺服器 {guild_id}。")


async def _build_default_tier_configs() -> dict[str, dict]:
    """
    為舊版終端機 approve 指令產生所有方案的預設允許設定。

    Returns:
        approve_plugin_version() 可接受的 tier_configs
    """
    tiers = await repository.list_resource_tiers()
    return {
        tier["tier_name"]: {
            "allowed": True,
            "execution_quota": None,
            "action_quota": None,
            "storage_key_length_limit": plugin_storage_repository.MAX_STORAGE_KEY_LENGTH,
            "storage_value_bytes_limit": plugin_storage_repository.MAX_STORAGE_VALUE_BYTES,
            "storage_keys_per_installation_limit": plugin_storage_repository.MAX_STORAGE_KEYS_PER_INSTALLATION,
            "instruction_limit": engine.INSTRUCTION_LIMIT,
            "memory_limit_bytes": engine.MEMORY_LIMIT_BYTES,
        }
        for tier in tiers
    }


async def _handle_review_command(parts: list[str]) -> None:
    """
    處理外掛審核指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 5:
        if len(parts) == 4 and parts[3] == "list":
            plugins = await repository.list_plugins("pending_review")
            if not plugins:
                print("目前沒有待審核外掛。")
                return
            for plugin in plugins:
                print(
                    f"{plugin['plugin_id']} | {plugin['name']} | "
                    f"version={plugin['latest_version']} | status={plugin['status']}"
                )
            return
        print(HELP_TEXT)
        return
    action = parts[3]
    plugin_id = _validate_plugin_id(parts[4])
    if action == "approve" and len(parts) == 5:
        updated = await admin_operations.approve_plugin_version(plugin_id, await _build_default_tier_configs())
        print("已核准外掛。" if updated else f"找不到外掛：{plugin_id}")
        return
    if action == "reject" and len(parts) >= 6:
        reason = " ".join(parts[5:])
        updated = await admin_operations.reject_plugin_version(plugin_id, [], reason, [])
        print("已退回外掛。" if updated else f"找不到外掛：{plugin_id}")
        return
    print(HELP_TEXT)


async def _handle_quota_command(parts: list[str]) -> None:
    """
    處理指定安裝的配額覆蓋指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 8 or parts[3] != "set":
        print(HELP_TEXT)
        return
    guild_id = _parse_guild_id(parts[4])
    plugin_id = _validate_plugin_id(parts[5])
    execution_quota, action_quota = _parse_quota_arguments(parts[6:])
    updated = await admin_operations.set_quota_override(
        guild_id=guild_id,
        plugin_id=plugin_id,
        execution_quota=execution_quota,
        action_quota=action_quota,
    )
    print("已更新外掛安裝配額。" if updated else f"找不到安裝紀錄：{guild_id}/{plugin_id}")


def _parse_stats_arguments(arguments: list[str]) -> tuple[int | None, str | None]:
    """
    解析 stats 指令的可選 guild=／since= 參數。

    Args:
        arguments: 指令中 plugin_id 之後的 key=value 參數

    Returns:
        (guild_id 或 None, since ISO 時間字串或 None)

    Raises:
        ValueError: 參數格式錯誤或有未知的參數名稱
    """
    guild_id: int | None = None
    since: str | None = None
    for argument in arguments:
        if "=" not in argument:
            raise ValueError(f"參數格式錯誤：{argument}")
        name, value = argument.split("=", 1)
        if name == "guild":
            guild_id = _parse_guild_id(value)
        elif name == "since":
            since = value
        else:
            raise ValueError(f"未知的參數：{name}")
    return guild_id, since


async def _handle_stats_command(parts: list[str]) -> None:
    """
    彙總指定外掛的執行紀錄並印出，見 design.md 附錄 A.6.2。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 4:
        print(HELP_TEXT)
        return
    plugin_id = _validate_plugin_id(parts[3])
    guild_id, since = _parse_stats_arguments(parts[4:])
    stats = await repository.get_execution_stats(plugin_id, guild_id=guild_id, since=since)

    if stats["total"] == 0:
        print(f"{plugin_id} 沒有任何執行紀錄。")
        return

    print(f"{plugin_id} | 總執行次數={stats['total']}")
    for outcome, count in sorted(stats["outcome_counts"].items()):
        percentage = count / stats["total"] * 100
        print(f"  {outcome:<24} {count:>6} 次（{percentage:.1f}%）")
    print(f"  平均耗時={stats['avg_execution_ms']:.1f}ms | p95 耗時={stats['p95_execution_ms']}ms")


async def handle_command(line: str) -> None:
    """
    解析單行終端機指令。

    Args:
        line: 終端機輸入的一整行文字

    """
    try:
        parts = shlex.split(line)
    except ValueError as error:
        print(f"指令解析失敗：{error}")
        return

    if not parts:
        return
    if len(parts) < 2 or parts[0] != "admin" or parts[1] != "plugin":
        return

    try:
        command = parts[2] if len(parts) >= 3 else ""
        if command == "list" and len(parts) == 3:
            await _handle_list_command()
        elif command == "show" and len(parts) == 4:
            await _handle_show_command(parts[3])
        elif command == "review":
            await _handle_review_command(parts)
        elif command == "install" and len(parts) == 5:
            await _handle_install_command(parts[3], parts[4])
        elif command == "uninstall" and len(parts) == 5:
            guild_id = _parse_guild_id(parts[3])
            plugin_id = _validate_plugin_id(parts[4])
            deleted = await admin_operations.uninstall_plugin(guild_id, plugin_id)
            print("已移除外掛安裝。" if deleted else f"找不到安裝紀錄：{guild_id}/{plugin_id}")
        elif command == "suspend" and len(parts) == 4:
            plugin_id = _validate_plugin_id(parts[3])
            updated = await admin_operations.request_suspend(plugin_id)
            if updated:
                await suspension.refresh_from_database(get_db())
                print("已停權外掛並同步停權快取。")
            else:
                print(f"找不到外掛：{plugin_id}")
        elif command == "unsuspend" and len(parts) == 4:
            plugin_id = _validate_plugin_id(parts[3])
            updated = await admin_operations.request_unsuspend(plugin_id)
            if updated:
                await suspension.refresh_from_database(get_db())
                print("已解除外掛停權並同步停權快取。")
            else:
                print(f"找不到外掛：{plugin_id}")
        elif command == "quota":
            await _handle_quota_command(parts)
        elif command == "stats":
            await _handle_stats_command(parts)
        else:
            print(HELP_TEXT)
    except (admin_operations.AdminOperationError, ManifestValidationError, ValueError) as error:
        print(f"指令執行失敗：{error}")
    except Exception as error:
        logger.error(f"外掛平台管理指令執行失敗：{error}", exc_info=True)
        print("指令執行時發生未預期錯誤，請查看日誌。")
