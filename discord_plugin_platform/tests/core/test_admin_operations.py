import json
from collections.abc import AsyncIterator

import aiosqlite
import pytest

from core import admin_operations, database, plugin_storage_repository, repository


@pytest.fixture()
async def plugin_database(tmp_path, monkeypatch) -> AsyncIterator[aiosqlite.Connection]:
    """
    建立每個測試專用的暫存外掛平台資料庫。
    """
    await database.close_db()
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "plugin_platform.db"))
    await database.init_db()
    yield database.get_db()
    await database.close_db()


def _manifest_json(required_capabilities: list[str] | None = None) -> str:
    """
    建立符合 manifest 驗證規則的測試 manifest。
    """
    return json.dumps(
        {
            "name": "temp_role_punishment",
            "version": "1.0.0",
            "description": "測試外掛",
            "capability_api_version": 1,
            "event_hooks": ["on_message"],
            "required_capabilities": required_capabilities or [],
        },
        ensure_ascii=False,
    )


async def _submit_plugin(required_capabilities: list[str] | None = None) -> None:
    """
    提交一個可供 admin operation 測試使用的外掛。
    """
    await repository.submit_plugin_version(
        plugin_id="temp_role_punishment",
        author_id=1234,
        name="temp_role_punishment",
        version="1.0.0",
        manifest_json=_manifest_json(required_capabilities),
        source_code="function on_message(payload) end",
        capability_api_version=1,
    )


def _tier_config(allowed: bool = True) -> dict:
    """
    建立完整的方案設定。
    """
    return {
        "allowed": allowed,
        "storage_key_length_limit": 256,
        "storage_value_bytes_limit": 65536,
        "storage_keys_per_installation_limit": 1000,
        "instruction_limit": 500000,
        "memory_limit_bytes": 16777216,
    }


async def test_approve_plugin_version_requires_every_resource_tier(
    plugin_database: aiosqlite.Connection,
) -> None:
    await _submit_plugin()
    await repository.create_resource_tier("large", 10, "large guild")

    with pytest.raises(ValueError, match="missing"):
        await admin_operations.approve_plugin_version(
            "temp_role_punishment",
            {"default": _tier_config()},
        )

    assert await admin_operations.approve_plugin_version(
        "temp_role_punishment",
        {"default": _tier_config(), "large": _tier_config(False)},
    ) is True
    assert (await repository.get_plugin("temp_role_punishment"))["status"] == "approved"


async def test_install_plugin_checks_block_before_tier(plugin_database: aiosqlite.Connection) -> None:
    await _submit_plugin(["storage"])
    await admin_operations.approve_plugin_version("temp_role_punishment", {"default": _tier_config(False)})
    await admin_operations.block_plugin_installation(1111, "temp_role_punishment", "blocked")

    with pytest.raises(admin_operations.PluginInstallationBlockedError):
        await admin_operations.install_plugin(1111, "temp_role_punishment")

    await admin_operations.unblock_plugin_installation(1111, "temp_role_punishment")
    with pytest.raises(admin_operations.PluginTierNotAllowedError):
        await admin_operations.install_plugin(1111, "temp_role_punishment")


async def test_install_plugin_uses_approved_tier_config(plugin_database: aiosqlite.Connection) -> None:
    await _submit_plugin(["storage"])
    await admin_operations.approve_plugin_version("temp_role_punishment", {"default": _tier_config(True)})

    await admin_operations.install_plugin(1111, "temp_role_punishment")
    installation = await repository.get_installation(1111, "temp_role_punishment")

    assert json.loads(installation["granted_capabilities_json"]) == ["storage"]


async def test_reject_plugin_version_validates_flagged_capabilities(
    plugin_database: aiosqlite.Connection,
) -> None:
    await _submit_plugin(["storage"])

    with pytest.raises(ValueError, match="flagged_capabilities"):
        await admin_operations.reject_plugin_version("temp_role_punishment", [], None, ["manage_roles"])

    assert await admin_operations.reject_plugin_version("temp_role_punishment", [], "too broad", ["storage"]) is True


async def test_uninstall_plugin_clears_storage_and_enqueues_notification(
    plugin_database: aiosqlite.Connection,
) -> None:
    await _submit_plugin(["storage"])
    await admin_operations.approve_plugin_version("temp_role_punishment", {"default": _tier_config(True)})
    await admin_operations.install_plugin(1111, "temp_role_punishment")
    await plugin_storage_repository.storage_set(1111, "temp_role_punishment", "score", 42)
    await plugin_storage_repository.create_scheduled_task(1111, "temp_role_punishment", 60, "task", {})

    assert await admin_operations.uninstall_plugin(1111, "temp_role_punishment") is True

    assert await plugin_storage_repository.storage_get(1111, "temp_role_punishment", "score") is None
    assert await repository.get_installation(1111, "temp_role_punishment") is None
    async with plugin_database.execute(
        "SELECT COUNT(*) FROM plugin_scheduled_tasks WHERE guild_id = ? AND plugin_id = ?",
        (1111, "temp_role_punishment"),
    ) as cursor:
        remaining_task_count = (await cursor.fetchone())[0]
    assert remaining_task_count == 0

    notifications = await repository.get_pending_guild_notifications()
    assert len(notifications) == 1
    assert notifications[0]["guild_id"] == 1111
    assert notifications[0]["notification_type"] == "plugin_uninstalled"
    assert json.loads(notifications[0]["payload_json"]) == {"plugin_id": "temp_role_punishment"}


async def test_uninstall_plugin_missing_installation_does_not_enqueue_notification(
    plugin_database: aiosqlite.Connection,
) -> None:
    assert await admin_operations.uninstall_plugin(1111, "does_not_exist") is False

    assert await repository.get_pending_guild_notifications() == []
