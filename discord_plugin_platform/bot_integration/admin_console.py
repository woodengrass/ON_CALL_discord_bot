"""
終端機管理指令，比照現有 honeypot-discord-bot 專案的 admin/console.py 模式。
這是跟 web/admin/ 並存的操作介面，兩者共用 core/admin_operations.py 的業務邏輯，
不能只改網頁就不管終端機，見 design.md 第 3.5、H.0、H.3 節。
"""

import logging
import re
import shlex

from core import admin_operations, plugin_storage_repository, repository, suspension
from core.database import get_db
from core.manifest import ManifestValidationError
from core.repository import RESOURCE_LIMIT_KEYS
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
    ("admin plugin ban <plugin_id> <reason>", "永久封鎖外掛，解除既有安裝並清 storage"),
    ("admin plugin unban <plugin_id>", "解除封鎖，狀態改回 rejected（不自動核准、不自動復裝）"),
    ("admin plugin pricing <plugin_id> <free|paid>", "設定外掛計價分類（純標籤，不影響配額）"),
    ("admin plugin quota set <guild_id> <plugin_id> execution=<次數> action=<次數>", "調整指定安裝的動態配額"),
    (
        "admin plugin resource <guild_id> <plugin_id> [key=value ...]",
        "設定指定安裝的資源覆蓋值，不帶任何 key=value 表示清除覆蓋",
    ),
    (
        "admin plugin stats <plugin_id> [guild=<guild_id>] [since=<ISO時間>]",
        "彙總外掛執行紀錄：成功率、各結果比例、平均/p95 耗時",
    ),
    ("admin plugin tier list", "列出所有資源方案"),
    ("admin plugin tier create <tier_name> <display_order> <description>", "建立資源方案"),
    ("admin plugin tier update <tier_name> <display_order> <description>", "更新資源方案"),
    ("admin plugin tier delete <tier_name> [force]", "刪除資源方案，force 時強制清理引用"),
    ("admin plugin tier config show <tier_name> <plugin_id>", "查詢外掛在指定方案下的設定"),
    (
        "admin plugin tier config set <tier_name> <plugin_id> allowed=<true|false> [key=value ...]",
        "設定外掛在指定方案下的允許狀態與配額/資源限制",
    ),
    ("admin guild tier show <guild_id>", "查詢伺服器目前套用的資源方案"),
    ("admin guild tier set <guild_id> <tier_name>", "指定伺服器套用的資源方案"),
    ("admin guild block list <guild_id>", "列出伺服器封鎖的所有外掛"),
    ("admin guild block add <guild_id> <plugin_id> [reason]", "封鎖指定伺服器安裝某個外掛"),
    ("admin guild block remove <guild_id> <plugin_id>", "解除指定伺服器對外掛的安裝封鎖"),
)
HELP_TEXT = "[外掛平台管理工具] 可用指令（直接在終端機輸入後按 Enter）：\n" + "\n".join(
    f"  {usage:<68} {description}" for usage, description in COMMAND_HELP
)

QUOTA_NAMES = {"execution", "action"}
PRICING_TIERS = {"free", "paid"}
MESSAGE_CACHE_EVENTS = {"on_message_edit", "on_message_delete"}
MIN_QUOTA_OVERRIDE = 0
MAX_EXECUTION_QUOTA_OVERRIDE = 10_000
MAX_ACTION_QUOTA_OVERRIDE = 10_000
MAX_DISCORD_SNOWFLAKE = 2**64 - 1
PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
TIER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


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


def _validate_tier_name(tier_name: str) -> str:
    """
    驗證 tier_name 格式。

    Args:
        tier_name: 指令中的資源方案名稱

    Returns:
        原始 tier_name

    Raises:
        ValueError: tier_name 格式不合法
    """
    if not TIER_NAME_PATTERN.match(tier_name):
        raise ValueError("tier_name 必須是 1-64 字元的英數、底線、連字號或句點")
    return tier_name


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


def _parse_resource_override_arguments(arguments: list[str]) -> dict[str, int]:
    """
    解析資源覆蓋指令的 key=value 參數，只接受五個已知的資源限制欄位。

    Args:
        arguments: 指令中剩餘的 key=value 參數，空清單表示清除覆蓋

    Returns:
        資源覆蓋 dict

    Raises:
        ValueError: 參數格式錯誤、欄位名稱不支援或值不是正整數
    """
    overrides: dict[str, int] = {}
    for argument in arguments:
        if "=" not in argument:
            raise ValueError(f"資源覆蓋參數格式錯誤：{argument}")
        name, value = argument.split("=", 1)
        if name not in RESOURCE_LIMIT_KEYS:
            raise ValueError(f"未知的資源覆蓋欄位：{name}，可用欄位：{', '.join(sorted(RESOURCE_LIMIT_KEYS))}")
        try:
            parsed_value = int(value)
        except ValueError as error:
            raise ValueError(f"{name} 必須是正整數") from error
        if parsed_value < 1:
            raise ValueError(f"{name} 必須是正整數")
        overrides[name] = parsed_value
    return overrides


def _parse_tier_config_arguments(arguments: list[str]) -> dict:
    """
    解析 tier config set 指令的 allowed= 與資源限制 key=value 參數。

    Args:
        arguments: 指令中剩餘的 key=value 參數，必須包含 allowed=

    Returns:
        update_plugin_tier_config() 可接受的方案設定 dict

    Raises:
        ValueError: 缺少 allowed=、欄位名稱不支援或值格式錯誤
    """
    allowed_value: bool | None = None
    config: dict = {}
    known_keys = RESOURCE_LIMIT_KEYS | {"execution_quota", "action_quota"}
    for argument in arguments:
        if "=" not in argument:
            raise ValueError(f"方案設定參數格式錯誤：{argument}")
        name, value = argument.split("=", 1)
        if name == "allowed":
            if value.lower() not in {"true", "false"}:
                raise ValueError("allowed 只能是 true 或 false")
            allowed_value = value.lower() == "true"
            continue
        if name not in known_keys:
            raise ValueError(f"未知的方案設定欄位：{name}，可用欄位：{', '.join(sorted(known_keys))}")
        try:
            config[name] = int(value)
        except ValueError as error:
            raise ValueError(f"{name} 必須是正整數") from error
    if allowed_value is None:
        raise ValueError("tier config set 必須提供 allowed=true 或 allowed=false")
    config["allowed"] = allowed_value
    return config


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


async def _handle_ban_command(parts: list[str]) -> None:
    """
    處理外掛永久封鎖指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 4:
        print(HELP_TEXT)
        return
    plugin_id = _validate_plugin_id(parts[3])
    reason = " ".join(parts[4:]) if len(parts) > 4 else ""
    if not reason:
        raise ValueError("ban 指令必須提供封鎖原因")
    updated = await admin_operations.ban_plugin_version(plugin_id, reason)
    print("已永久封鎖外掛。" if updated else f"找不到外掛：{plugin_id}")


async def _handle_unban_command(plugin_id: str) -> None:
    """
    解除外掛封鎖，狀態改回 rejected。

    Args:
        plugin_id: 外掛 ID
    """
    plugin_id = _validate_plugin_id(plugin_id)
    updated = await admin_operations.unban_plugin(plugin_id)
    print("已解除封鎖，狀態改回 rejected。" if updated else f"找不到外掛：{plugin_id}")


async def _handle_pricing_command(parts: list[str]) -> None:
    """
    處理外掛計價分類指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) != 5:
        print(HELP_TEXT)
        return
    plugin_id = _validate_plugin_id(parts[3])
    pricing_tier = parts[4]
    if pricing_tier not in PRICING_TIERS:
        raise ValueError("pricing_tier 只能是 free 或 paid")
    updated = await admin_operations.set_plugin_pricing_tier(plugin_id, pricing_tier)
    print(f"已設定計價分類為 {pricing_tier}。" if updated else f"找不到外掛：{plugin_id}")


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


async def _handle_resource_command(parts: list[str]) -> None:
    """
    處理指定安裝的資源覆蓋指令，不帶任何 key=value 表示清除覆蓋。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 5:
        print(HELP_TEXT)
        return
    guild_id = _parse_guild_id(parts[3])
    plugin_id = _validate_plugin_id(parts[4])
    overrides = _parse_resource_override_arguments(parts[5:])
    updated = await admin_operations.set_installation_resource_overrides(guild_id, plugin_id, overrides)
    if not updated:
        print(f"找不到安裝紀錄：{guild_id}/{plugin_id}")
        return
    print("已清除資源覆蓋。" if not overrides else "已更新資源覆蓋。")


async def _handle_tier_command(parts: list[str]) -> None:
    """
    處理資源方案管理指令（list/create/update/delete/config）。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 4:
        print(HELP_TEXT)
        return
    action = parts[3]
    if action == "list" and len(parts) == 4:
        tiers = await repository.list_resource_tiers()
        if not tiers:
            print("目前沒有任何資源方案。")
            return
        for tier in tiers:
            print(f"{tier['tier_name']} | order={tier['display_order']} | {tier['description']}")
        return
    if action == "create" and len(parts) >= 6:
        tier_name = _validate_tier_name(parts[4])
        display_order = int(parts[5])
        description = " ".join(parts[6:])
        await admin_operations.create_resource_tier(tier_name, display_order, description)
        print(f"已建立資源方案 {tier_name}。")
        return
    if action == "update" and len(parts) >= 6:
        tier_name = _validate_tier_name(parts[4])
        display_order = int(parts[5])
        description = " ".join(parts[6:])
        updated = await admin_operations.update_resource_tier(tier_name, display_order, description)
        print(f"已更新資源方案 {tier_name}。" if updated else f"找不到資源方案：{tier_name}")
        return
    if action == "delete" and len(parts) >= 5:
        tier_name = _validate_tier_name(parts[4])
        force = len(parts) >= 6 and parts[5] == "force"
        deleted = await admin_operations.delete_resource_tier(tier_name, force=force)
        print(f"已刪除資源方案 {tier_name}。" if deleted else f"找不到資源方案：{tier_name}")
        return
    if action == "config":
        await _handle_tier_config_command(parts)
        return
    print(HELP_TEXT)


async def _handle_tier_config_command(parts: list[str]) -> None:
    """
    處理外掛在指定方案下的設定查詢/更新指令。

    Args:
        parts: shlex 解析後的完整指令片段（parts[3] 固定為 "config"）
    """
    if len(parts) < 6:
        print(HELP_TEXT)
        return
    sub_action = parts[4]
    tier_name = _validate_tier_name(parts[5])
    if sub_action == "show" and len(parts) == 7:
        plugin_id = _validate_plugin_id(parts[6])
        config = await repository.get_plugin_tier_config(plugin_id, tier_name)
        if config is None:
            print(f"找不到方案設定：{plugin_id}@{tier_name}")
            return
        print(f"{plugin_id}@{tier_name} | {config}")
        return
    if sub_action == "set" and len(parts) >= 8:
        plugin_id = _validate_plugin_id(parts[6])
        config = _parse_tier_config_arguments(parts[7:])
        await admin_operations.update_plugin_tier_config(plugin_id, tier_name, config)
        print(f"已更新 {plugin_id}@{tier_name} 的方案設定。")
        return
    print(HELP_TEXT)


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


async def _handle_guild_tier_command(parts: list[str]) -> None:
    """
    處理伺服器資源方案查詢/指定指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 4:
        print(HELP_TEXT)
        return
    action = parts[2]
    if action == "show" and len(parts) == 4:
        guild_id = _parse_guild_id(parts[3])
        tier_name = await repository.get_guild_resource_tier(guild_id)
        print(f"伺服器 {guild_id} 目前套用方案：{tier_name}")
        return
    if action == "set" and len(parts) == 5:
        guild_id = _parse_guild_id(parts[3])
        tier_name = _validate_tier_name(parts[4])
        await admin_operations.set_guild_resource_tier(guild_id, tier_name)
        print(f"已將伺服器 {guild_id} 套用方案 {tier_name}。")
        return
    print(HELP_TEXT)


async def _handle_guild_block_command(parts: list[str]) -> None:
    """
    處理伺服器封鎖名單查詢/新增/移除指令。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 4:
        print(HELP_TEXT)
        return
    action = parts[2]
    if action == "list" and len(parts) == 4:
        guild_id = _parse_guild_id(parts[3])
        blocks = await repository.list_plugin_installation_blocks(guild_id)
        if not blocks:
            print(f"伺服器 {guild_id} 沒有封鎖任何外掛。")
            return
        for block in blocks:
            print(f"{block['plugin_id']} | blocked_at={block['blocked_at']} | reason={block['reason'] or ''}")
        return
    if action == "add" and len(parts) >= 5:
        guild_id = _parse_guild_id(parts[3])
        plugin_id = _validate_plugin_id(parts[4])
        reason = " ".join(parts[5:]) if len(parts) > 5 else None
        await admin_operations.block_plugin_installation(guild_id, plugin_id, reason)
        print(f"已封鎖伺服器 {guild_id} 安裝外掛 {plugin_id}。")
        return
    if action == "remove" and len(parts) == 5:
        guild_id = _parse_guild_id(parts[3])
        plugin_id = _validate_plugin_id(parts[4])
        removed = await admin_operations.unblock_plugin_installation(guild_id, plugin_id)
        print("已解除封鎖。" if removed else f"找不到封鎖紀錄：{guild_id}/{plugin_id}")
        return
    print(HELP_TEXT)


async def _handle_guild_command(parts: list[str]) -> None:
    """
    解析 admin guild 指令的第二層子指令（tier/block）。

    Args:
        parts: shlex 解析後的完整指令片段
    """
    if len(parts) < 2:
        print(HELP_TEXT)
        return
    category = parts[1]
    if category == "tier":
        await _handle_guild_tier_command(parts)
    elif category == "block":
        await _handle_guild_block_command(parts)
    else:
        print(HELP_TEXT)


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

    if not parts or parts[0] != "admin" or len(parts) < 2:
        return

    try:
        if parts[1] == "guild":
            await _handle_guild_command(parts[1:])
            return
        if parts[1] != "plugin":
            return

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
        elif command == "ban":
            await _handle_ban_command(parts)
        elif command == "unban" and len(parts) == 4:
            await _handle_unban_command(parts[3])
        elif command == "pricing":
            await _handle_pricing_command(parts)
        elif command == "quota":
            await _handle_quota_command(parts)
        elif command == "resource":
            await _handle_resource_command(parts)
        elif command == "tier":
            await _handle_tier_command(parts)
        elif command == "stats":
            await _handle_stats_command(parts)
        else:
            print(HELP_TEXT)
    except (admin_operations.AdminOperationError, ManifestValidationError, ValueError) as error:
        print(f"指令執行失敗：{error}")
    except Exception as error:
        logger.error(f"外掛平台管理指令執行失敗：{error}", exc_info=True)
        print("指令執行時發生未預期錯誤，請查看日誌。")
