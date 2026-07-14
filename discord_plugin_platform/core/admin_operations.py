"""
平台操作者共用操作層。

終端機管理指令與未來 web/admin/ 都應呼叫這裡的函式，不各自重寫審核、安裝資格、
資源解析或停權連鎖邏輯。
"""

import logging

from core import message_cache, plugin_storage_repository, quota, repository
from core.database import get_db
from core.manifest import parse_manifest

logger = logging.getLogger(__name__)

MESSAGE_CACHE_EVENTS = {"on_message_edit", "on_message_delete"}


class AdminOperationError(Exception):
    """
    管理操作失敗時拋出的基底例外。
    """


class PluginInstallationBlockedError(AdminOperationError):
    """
    伺服器層級封鎖導致外掛不能安裝。
    """


class PluginTierNotAllowedError(AdminOperationError):
    """
    伺服器目前資源方案不允許安裝指定外掛。
    """


async def set_plugin_pricing_tier(plugin_id: str, pricing_tier: str) -> bool:
    """
    設定外掛計價分類。

    Args:
        plugin_id: 外掛 ID
        pricing_tier: free 或 paid

    Returns:
        True 表示成功更新；外掛不存在則回傳 False
    """
    return await repository.set_plugin_pricing_tier(plugin_id, pricing_tier)


async def create_resource_tier(tier_name: str, display_order: int, description: str) -> None:
    """
    建立資源方案。

    Args:
        tier_name: 方案名稱
        display_order: 顯示排序
        description: 操作者說明
    """
    await repository.create_resource_tier(tier_name, display_order, description)


async def update_resource_tier(tier_name: str, display_order: int, description: str) -> bool:
    """
    更新資源方案。

    Args:
        tier_name: 方案名稱
        display_order: 顯示排序
        description: 操作者說明

    Returns:
        True 表示成功更新；方案不存在則回傳 False
    """
    return await repository.update_resource_tier(tier_name, display_order, description)


async def delete_resource_tier(tier_name: str, force: bool = False) -> bool:
    """
    刪除資源方案。

    Args:
        tier_name: 方案名稱
        force: 是否強制清理引用

    Returns:
        True 表示成功刪除；方案不存在則回傳 False
    """
    return await repository.delete_resource_tier(tier_name, force=force)


async def set_guild_resource_tier(guild_id: int, tier_name: str) -> None:
    """
    指定伺服器套用的資源方案。

    Args:
        guild_id: 伺服器 ID
        tier_name: 方案名稱
    """
    await repository.set_guild_resource_tier(guild_id, tier_name)


async def set_installation_resource_overrides(guild_id: int, plugin_id: str, overrides: dict) -> bool:
    """
    設定單一安裝的資源覆蓋值。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        overrides: 資源覆蓋 dict

    Returns:
        True 表示成功更新；安裝不存在則回傳 False
    """
    return await repository.set_resource_overrides(guild_id, plugin_id, overrides)


async def set_quota_override(
    guild_id: int,
    plugin_id: str,
    execution_quota: int | None,
    action_quota: int | None,
) -> bool:
    """
    設定單一安裝的執行/動作配額覆蓋。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        execution_quota: 執行次數覆蓋；None 表示平台預設
        action_quota: 動作次數覆蓋；None 表示平台預設

    Returns:
        True 表示成功更新；安裝不存在則回傳 False
    """
    return await repository.set_installation_quota_override(guild_id, plugin_id, execution_quota, action_quota)


async def block_plugin_installation(guild_id: int, plugin_id: str, reason: str | None = None) -> None:
    """
    封鎖指定伺服器安裝某個外掛。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
        reason: 操作者備註原因
    """
    await repository.block_plugin_installation(guild_id, plugin_id, reason)


async def unblock_plugin_installation(guild_id: int, plugin_id: str) -> bool:
    """
    解除指定伺服器的外掛安裝封鎖。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        True 表示有刪除封鎖紀錄
    """
    return await repository.unblock_plugin_installation(guild_id, plugin_id)


async def approve_plugin_version(plugin_id: str, tier_configs: dict[str, dict]) -> bool:
    """
    核准外掛版本，並原子寫入每個資源方案的外掛設定。

    Args:
        plugin_id: 外掛 ID
        tier_configs: 以 tier_name 為 key 的方案設定 dict

    Returns:
        True 表示成功核准；外掛不存在則回傳 False
    """
    tiers = await repository.list_resource_tiers()
    required_tiers = {tier["tier_name"] for tier in tiers}
    if set(tier_configs) != required_tiers:
        missing = required_tiers - set(tier_configs)
        extra = set(tier_configs) - required_tiers
        raise ValueError(f"tier_configs 必須涵蓋所有方案，missing={sorted(missing)}, extra={sorted(extra)}")

    db = get_db()
    try:
        async with db.execute("SELECT latest_version FROM plugins WHERE plugin_id = ?", (plugin_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            await db.commit()
            return False
        for tier_name, config in tier_configs.items():
            repository._validate_tier_config(config)
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
        await db.execute("UPDATE plugins SET status = 'approved' WHERE plugin_id = ?", (plugin_id,))
        await db.execute(
            """
            INSERT INTO plugin_review_log (plugin_id, version, reviewer_action, reason, created_at)
            VALUES (?, ?, 'approved', NULL, ?)
            """,
            (plugin_id, row[0], repository._now_iso()),
        )
        await db.commit()
        repository.clear_event_subscription_cache()
        return True
    except Exception as error:
        await db.rollback()
        logger.error(f"核准外掛版本失敗：{error}", exc_info=True)
        raise


async def update_plugin_tier_config(plugin_id: str, tier_name: str, config: dict) -> None:
    """
    更新已核准外掛在指定方案下的設定。

    Args:
        plugin_id: 外掛 ID
        tier_name: 方案名稱
        config: 方案設定
    """
    await repository.set_plugin_tier_config(plugin_id, tier_name, config)


async def reject_plugin_version(
    plugin_id: str,
    reason_presets: list[str],
    custom_reason: str | None,
    flagged_capabilities: list[str],
) -> bool:
    """
    結構化退回外掛版本，並驗證 flagged_capabilities 屬於該版本 manifest 要求能力。

    Args:
        plugin_id: 外掛 ID
        reason_presets: 退回原因 preset_id 清單
        custom_reason: 自由文字原因
        flagged_capabilities: 有疑慮的能力旗標

    Returns:
        True 表示成功退回；外掛不存在則回傳 False
    """
    plugin = await repository.get_plugin(plugin_id)
    if plugin is None:
        return False
    manifest_json = await repository.get_plugin_manifest(plugin_id, plugin["latest_version"])
    if manifest_json is None:
        raise AdminOperationError(f"找不到外掛 manifest：{plugin_id}@{plugin['latest_version']}")
    manifest = parse_manifest(manifest_json)
    invalid_capabilities = set(flagged_capabilities) - set(manifest.required_capabilities)
    if invalid_capabilities:
        raise ValueError(f"flagged_capabilities 不屬於此外掛要求能力：{sorted(invalid_capabilities)}")
    return await repository.reject_plugin_structured(
        plugin_id,
        reason_presets,
        custom_reason,
        flagged_capabilities,
    )


async def install_plugin(guild_id: int, plugin_id: str) -> None:
    """
    安裝已核准外掛，安裝前檢查伺服器封鎖與資源方案是否允許。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID
    """
    block = await repository.get_plugin_installation_block(guild_id, plugin_id)
    if block is not None:
        raise PluginInstallationBlockedError(f"此外掛在伺服器 {guild_id} 已被封鎖：{plugin_id}")
    plugin = await repository.get_plugin(plugin_id)
    if plugin is None:
        raise AdminOperationError(f"找不到外掛：{plugin_id}")
    if plugin["status"] != "approved":
        raise AdminOperationError(f"外掛尚未核准，不能安裝：{plugin_id}")
    tier_name = await repository.get_guild_resource_tier(guild_id)
    tier_config = await repository.get_plugin_tier_config(plugin_id, tier_name)
    if tier_config is None or not tier_config["allowed"]:
        raise PluginTierNotAllowedError(f"方案 {tier_name} 不允許安裝外掛：{plugin_id}")
    manifest_json = await repository.get_plugin_manifest(plugin_id, plugin["latest_version"])
    if manifest_json is None:
        raise AdminOperationError(f"找不到外掛 manifest：{plugin_id}@{plugin['latest_version']}")
    manifest = parse_manifest(manifest_json)
    await repository.create_installation(guild_id, plugin_id, plugin["latest_version"], manifest.required_capabilities)


async def uninstall_plugin(guild_id: int, plugin_id: str) -> bool:
    """
    解除安裝外掛，清理配額、訊息快取與這個安裝的 storage，並 enqueue 伺服器通知。

    Args:
        guild_id: 伺服器 ID
        plugin_id: 外掛 ID

    Returns:
        True 表示有刪除安裝紀錄
    """
    deleted = await repository.delete_installation(guild_id, plugin_id)
    if deleted:
        quota.clear_usage(guild_id, plugin_id)
        await plugin_storage_repository.delete_storage_for_installation(guild_id, plugin_id)
        await repository.enqueue_guild_notification(
            guild_id,
            "plugin_uninstalled",
            {"plugin_id": plugin_id},
        )
    if deleted and not await repository.guild_has_event_subscription(guild_id, MESSAGE_CACHE_EVENTS):
        message_cache.purge_guild(guild_id)
    return deleted


async def request_suspend(plugin_id: str) -> bool:
    """
    停權外掛，解除既有安裝、清 storage，並 enqueue 受影響伺服器通知。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示外掛存在且已停權
    """
    updated = await repository.suspend_plugin(plugin_id)
    if not updated:
        return False
    affected_guild_ids = await repository.delete_all_installations_for_plugin(plugin_id)
    await plugin_storage_repository.delete_all_storage_for_plugin(plugin_id)
    for guild_id in affected_guild_ids:
        quota.clear_usage(guild_id, plugin_id)
        await repository.enqueue_guild_notification(
            guild_id,
            "plugin_suspended",
            {"plugin_id": plugin_id},
        )
    return True


async def request_unsuspend(plugin_id: str) -> bool:
    """
    解除外掛停權，不自動復裝任何伺服器。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示外掛存在且已解除停權
    """
    return await repository.unsuspend_plugin(plugin_id)


async def ban_plugin_version(plugin_id: str, reason: str) -> bool:
    """
    永久封鎖外掛，解除既有安裝、清 storage，並 enqueue 受影響伺服器通知。

    Args:
        plugin_id: 外掛 ID
        reason: 封鎖原因

    Returns:
        True 表示外掛存在且已封鎖
    """
    updated = await repository.ban_plugin(plugin_id, reason)
    if not updated:
        return False
    affected_guild_ids = await repository.delete_all_installations_for_plugin(plugin_id)
    await plugin_storage_repository.delete_all_storage_for_plugin(plugin_id)
    for guild_id in affected_guild_ids:
        quota.clear_usage(guild_id, plugin_id)
        await repository.enqueue_guild_notification(
            guild_id,
            "plugin_banned",
            {"plugin_id": plugin_id, "reason": reason},
        )
    return True


async def unban_plugin(plugin_id: str) -> bool:
    """
    解除 banned 狀態並改回 rejected，不自動核准、不自動復裝。

    Args:
        plugin_id: 外掛 ID

    Returns:
        True 表示外掛存在且狀態已改回 rejected
    """
    return await repository.unban_plugin(plugin_id)
