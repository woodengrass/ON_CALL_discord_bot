import json
import sqlite3
from collections.abc import AsyncIterator

import aiosqlite
import pytest

from core import database, repository


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


async def _submit_example_plugin(plugin_id: str = "temp_role_punishment") -> None:
    """
    寫入一個可供測試使用的外掛版本。

    Args:
        plugin_id: 外掛 ID
    """
    await repository.submit_plugin_version(
        plugin_id=plugin_id,
        author_id=1234,
        name="temp_role_punishment",
        version="1.0.0",
        manifest_json=json.dumps({"name": "temp_role_punishment"}),
        source_code="function on_message(payload) end",
        capability_api_version=1,
    )


async def test_submit_plugin_version_creates_plugin_and_version(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()

    plugin = await repository.get_plugin("temp_role_punishment")
    source_code = await repository.get_plugin_source("temp_role_punishment", "1.0.0")
    manifest_json = await repository.get_plugin_manifest("temp_role_punishment", "1.0.0")

    assert plugin == {
        "plugin_id": "temp_role_punishment",
        "author_id": 1234,
        "name": "temp_role_punishment",
        "latest_version": "1.0.0",
        "status": "pending_review",
        "pricing_tier": "free",
    }
    assert source_code == "function on_message(payload) end"
    assert json.loads(manifest_json) == {"name": "temp_role_punishment"}


async def test_set_plugin_pricing_tier(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()

    assert await repository.set_plugin_pricing_tier("temp_role_punishment", "paid") is True
    plugin = await repository.get_plugin("temp_role_punishment")

    assert plugin["pricing_tier"] == "paid"
    with pytest.raises(ValueError, match="pricing_tier"):
        await repository.set_plugin_pricing_tier("temp_role_punishment", "enterprise")


async def test_submit_new_version_resets_plugin_to_pending_review(
    plugin_database: aiosqlite.Connection,
) -> None:
    await _submit_example_plugin()
    await repository.approve_plugin("temp_role_punishment")

    await repository.submit_plugin_version(
        plugin_id="temp_role_punishment",
        author_id=1234,
        name="temp_role_punishment",
        version="1.1.0",
        manifest_json=json.dumps({"name": "temp_role_punishment", "version": "1.1.0"}),
        source_code="function on_scheduled_task(payload) end",
        capability_api_version=1,
    )

    plugin = await repository.get_plugin("temp_role_punishment")
    source_code = await repository.get_plugin_source("temp_role_punishment", "1.1.0")

    assert plugin["latest_version"] == "1.1.0"
    assert plugin["status"] == "pending_review"
    assert source_code == "function on_scheduled_task(payload) end"


async def test_duplicate_version_rolls_back_plugin_metadata(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()

    with pytest.raises(sqlite3.IntegrityError):
        await repository.submit_plugin_version(
            plugin_id="temp_role_punishment",
            author_id=5678,
            name="changed_name",
            version="1.0.0",
            manifest_json=json.dumps({"name": "changed_name"}),
            source_code="function changed(payload) end",
            capability_api_version=1,
        )

    plugin = await repository.get_plugin("temp_role_punishment")

    assert plugin["author_id"] == 1234
    assert plugin["name"] == "temp_role_punishment"


async def test_list_plugins_filters_by_status(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin("pending_plugin")
    await _submit_example_plugin("approved_plugin")
    await repository.approve_plugin("approved_plugin")

    all_plugins = await repository.list_plugins()
    approved_plugins = await repository.list_plugins("approved")

    assert [plugin["plugin_id"] for plugin in all_plugins] == ["approved_plugin", "pending_plugin"]
    assert [plugin["plugin_id"] for plugin in approved_plugins] == ["approved_plugin"]


async def test_approve_plugin_updates_status_and_logs_review(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()

    assert await repository.approve_plugin("temp_role_punishment") is True
    plugin = await repository.get_plugin("temp_role_punishment")
    async with plugin_database.execute(
        "SELECT version, reviewer_action, reason FROM plugin_review_log WHERE plugin_id = ?",
        ("temp_role_punishment",),
    ) as cursor:
        row = await cursor.fetchone()

    assert plugin["status"] == "approved"
    assert row == ("1.0.0", "approved", None)


async def test_reject_plugin_updates_status_and_logs_reason(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()

    assert await repository.reject_plugin("temp_role_punishment", "manifest 欄位不完整") is True
    plugin = await repository.get_plugin("temp_role_punishment")
    async with plugin_database.execute(
        "SELECT version, reviewer_action, reason FROM plugin_review_log WHERE plugin_id = ?",
        ("temp_role_punishment",),
    ) as cursor:
        row = await cursor.fetchone()

    assert plugin["status"] == "rejected"
    assert row == ("1.0.0", "rejected", "manifest 欄位不完整")


async def test_review_unknown_plugin_returns_false(plugin_database: aiosqlite.Connection) -> None:
    assert await repository.approve_plugin("missing_plugin") is False
    assert await repository.reject_plugin("missing_plugin", "不存在") is False


async def test_suspend_and_unsuspend_plugin(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()

    assert await repository.suspend_plugin("temp_role_punishment") is True
    assert await repository.is_plugin_suspended("temp_role_punishment") is True

    assert await repository.unsuspend_plugin("temp_role_punishment") is True
    assert await repository.is_plugin_suspended("temp_role_punishment") is False


async def test_create_and_delete_installation(plugin_database: aiosqlite.Connection) -> None:
    await repository.create_installation(
        guild_id=1111,
        plugin_id="temp_role_punishment",
        version="1.0.0",
        granted_capabilities=["manage_roles", "schedule_task"],
    )

    installation = await repository.get_installation(1111, "temp_role_punishment")

    assert installation["installed_version"] == "1.0.0"
    assert json.loads(installation["granted_capabilities_json"]) == ["manage_roles", "schedule_task"]
    assert installation["enabled"] is True
    assert installation["resource_overrides_json"] is None
    assert await repository.delete_installation(1111, "temp_role_punishment") is True
    assert await repository.get_installation(1111, "temp_role_punishment") is None


async def test_set_resource_overrides_validates_known_positive_integer_fields(
    plugin_database: aiosqlite.Connection,
) -> None:
    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["storage"])

    assert await repository.set_resource_overrides(
        1111,
        "temp_role_punishment",
        {"storage_value_bytes_limit": 128, "instruction_limit": 1000},
    ) is True
    installation = await repository.get_installation(1111, "temp_role_punishment")

    assert json.loads(installation["resource_overrides_json"]) == {
        "storage_value_bytes_limit": 128,
        "instruction_limit": 1000,
    }
    with pytest.raises(ValueError, match="未知"):
        await repository.set_resource_overrides(1111, "temp_role_punishment", {"unknown": 1})
    with pytest.raises(ValueError, match="正整數"):
        await repository.set_resource_overrides(1111, "temp_role_punishment", {"instruction_limit": 0})


async def test_resource_tier_config_and_resolve_resource_limits(plugin_database: aiosqlite.Connection) -> None:
    await repository.create_resource_tier("large", 10, "large guild")
    await repository.set_guild_resource_tier(1111, "large")
    await repository.set_plugin_tier_config(
        "temp_role_punishment",
        "large",
        {
            "allowed": True,
            "execution_quota": 20,
            "action_quota": 30,
            "storage_key_length_limit": 300,
            "storage_value_bytes_limit": 400,
            "storage_keys_per_installation_limit": 500,
            "instruction_limit": 600,
            "memory_limit_bytes": 700,
        },
    )
    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["storage"])
    await repository.set_resource_overrides(1111, "temp_role_punishment", {"instruction_limit": 900})
    await repository.set_installation_quota_override(1111, "temp_role_punishment", None, 40)

    limits = await repository.resolve_resource_limits(1111, "temp_role_punishment")

    assert limits["execution_quota"] == 20
    assert limits["action_quota"] == 40
    assert limits["storage_key_length_limit"] == 300
    assert limits["storage_value_bytes_limit"] == 400
    assert limits["storage_keys_per_installation_limit"] == 500
    assert limits["instruction_limit"] == 900
    assert limits["memory_limit_bytes"] == 700


async def test_banned_plugin_cannot_be_resubmitted(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()
    assert await repository.ban_plugin("temp_role_punishment", "惡意行為") is True

    with pytest.raises(ValueError, match="永久封鎖"):
        await _submit_example_plugin()

    assert await repository.unban_plugin("temp_role_punishment") is True
    await repository.submit_plugin_version(
        plugin_id="temp_role_punishment",
        author_id=1234,
        name="temp_role_punishment",
        version="1.1.0",
        manifest_json=json.dumps({"name": "temp_role_punishment"}),
        source_code="function on_message(payload) end",
        capability_api_version=1,
    )
    plugin = await repository.get_plugin("temp_role_punishment")
    assert plugin["status"] == "pending_review"


async def test_reinstall_keeps_original_installed_at(plugin_database: aiosqlite.Connection, monkeypatch) -> None:
    """
    重新安裝同一外掛時不應覆蓋第一次安裝時間，避免安裝年資與審計資料失真。
    """
    timestamps = iter(["2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00+00:00"])
    monkeypatch.setattr(repository, "_now_iso", lambda: next(timestamps))

    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["storage"])
    await repository.create_installation(1111, "temp_role_punishment", "1.1.0", ["storage", "schedule_task"])

    async with plugin_database.execute(
        "SELECT installed_version, granted_capabilities_json, installed_at FROM plugin_installations"
    ) as cursor:
        row = await cursor.fetchone()

    assert row[0] == "1.1.0"
    assert json.loads(row[1]) == ["storage", "schedule_task"]
    assert row[2] == "2026-01-01T00:00:00+00:00"


async def test_delete_installation_also_deletes_its_scheduled_tasks(plugin_database: aiosqlite.Connection) -> None:
    """
    刪除安裝時要一併清掉這個安裝底下所有排程任務，否則排程消費迴圈每分鐘都會
    重新嘗試分派一個永遠找不到對應安裝的任務，變成永遠重試、永遠不會消失的殭屍任務。
    """
    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["schedule_task"])
    await plugin_database.execute(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        VALUES ('task-1', 1111, 'temp_role_punishment', '2026-01-01T00:00:00+00:00', '{}', NULL)
        """
    )
    await plugin_database.commit()

    assert await repository.delete_installation(1111, "temp_role_punishment") is True

    due_tasks = await repository.get_due_scheduled_tasks("2099-01-01T00:00:00+00:00")
    assert due_tasks == []


async def test_delete_all_installations_for_guild(plugin_database: aiosqlite.Connection) -> None:
    """
    機器人被踢出伺服器時要清掉該伺服器全部外掛安裝與排程任務，不影響其他伺服器。
    """
    await _submit_example_plugin("plugin_a")
    await _submit_example_plugin("plugin_b")
    await repository.create_installation(1111, "plugin_a", "1.0.0", ["schedule_task"])
    await repository.create_installation(1111, "plugin_b", "1.0.0", [])
    await repository.create_installation(2222, "plugin_a", "1.0.0", [])
    await plugin_database.execute(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        VALUES ('task-1', 1111, 'plugin_a', '2026-01-01T00:00:00+00:00', '{}', NULL)
        """
    )
    await plugin_database.commit()

    deleted_plugin_ids = await repository.delete_all_installations_for_guild(1111)

    assert sorted(deleted_plugin_ids) == ["plugin_a", "plugin_b"]
    assert await repository.get_installation(1111, "plugin_a") is None
    assert await repository.get_installation(1111, "plugin_b") is None
    due_tasks = await repository.get_due_scheduled_tasks("2099-01-01T00:00:00+00:00")
    assert due_tasks == []
    # 另一個伺服器的安裝不受影響
    assert await repository.get_installation(2222, "plugin_a") is not None


async def test_get_enabled_installations_includes_manifest_json(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()
    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["manage_roles"])

    installations = await repository.get_enabled_installations_for_guild(1111)

    assert len(installations) == 1
    assert json.loads(installations[0]["manifest_json"]) == {"name": "temp_role_punishment"}


async def test_get_due_scheduled_tasks(plugin_database: aiosqlite.Connection) -> None:
    """
    get_due_scheduled_tasks() 會 JOIN plugin_installations/plugins/plugin_versions，
    只回傳仍有啟用安裝、且外掛狀態是 approved 的到期任務，所以測試資料必須先建好
    一個完整的已核准外掛＋已啟用安裝，光插入 plugin_scheduled_tasks 一筆是不夠的。
    """
    await _submit_example_plugin()
    await repository.approve_plugin("temp_role_punishment")
    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["schedule_task"])

    await plugin_database.executemany(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "due_task",
                1111,
                "temp_role_punishment",
                "2026-01-01T00:00:00+00:00",
                '{"kind":"due"}',
                None,
            ),
            (
                "future_task",
                1111,
                "temp_role_punishment",
                "2026-01-01T00:01:00+00:00",
                '{"kind":"future"}',
                60,
            ),
        ],
    )
    await plugin_database.commit()

    due_tasks = await repository.get_due_scheduled_tasks("2026-01-01T00:00:30+00:00")

    assert due_tasks == [
        {
            "task_id": "due_task",
            "guild_id": 1111,
            "plugin_id": "temp_role_punishment",
            "run_at": "2026-01-01T00:00:00+00:00",
            "payload_json": '{"kind":"due"}',
            "recurring_interval_seconds": None,
            "manifest_json": json.dumps({"name": "temp_role_punishment"}),
        }
    ]


async def test_scheduled_tasks_have_run_at_plugin_index(plugin_database: aiosqlite.Connection) -> None:
    """
    到期排程查詢會用 run_at + plugin_id 複合索引，避免任務量大時 JOIN 前掃描過多資料。
    """
    async with plugin_database.execute("PRAGMA index_list(plugin_scheduled_tasks)") as cursor:
        indexes = await cursor.fetchall()

    assert "idx_scheduled_tasks_run_at_plugin" in {row[1] for row in indexes}


async def test_delete_scheduled_task(plugin_database: aiosqlite.Connection) -> None:
    await plugin_database.execute(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        VALUES ('task_to_delete', 1111, 'temp_role_punishment', '2026-01-01T00:00:00+00:00', '{}', NULL)
        """
    )
    await plugin_database.commit()

    await repository.delete_scheduled_task("task_to_delete")
    due_tasks = await repository.get_due_scheduled_tasks("2026-01-01T00:01:00+00:00")

    assert due_tasks == []


async def test_update_scheduled_task_run_at(plugin_database: aiosqlite.Connection) -> None:
    await _submit_example_plugin()
    await repository.approve_plugin("temp_role_punishment")
    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["schedule_task"])

    await plugin_database.execute(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        VALUES ('task_to_update', 1111, 'temp_role_punishment', '2026-01-01T00:00:00+00:00', '{}', 60)
        """
    )
    await plugin_database.commit()

    updated = await repository.update_scheduled_task_run_at(
        "task_to_update", "2026-01-01T00:01:00+00:00"
    )
    due_tasks = await repository.get_due_scheduled_tasks("2026-01-01T00:01:00+00:00")

    assert updated is True
    assert due_tasks[0]["run_at"] == "2026-01-01T00:01:00+00:00"


async def test_get_due_scheduled_tasks_ignores_suspended_plugins(
    plugin_database: aiosqlite.Connection,
) -> None:
    """
    到期排程只應回傳仍核准且啟用安裝的外掛，避免 suspended 外掛每分鐘重試。
    """
    await _submit_example_plugin()
    await repository.approve_plugin("temp_role_punishment")
    await repository.create_installation(1111, "temp_role_punishment", "1.0.0", ["schedule_task"])
    await repository.suspend_plugin("temp_role_punishment")
    await plugin_database.execute(
        """
        INSERT INTO plugin_scheduled_tasks
            (task_id, guild_id, plugin_id, run_at, payload_json, recurring_interval_seconds)
        VALUES ('task_to_skip', 1111, 'temp_role_punishment', '2026-01-01T00:00:00+00:00', '{}', NULL)
        """
    )
    await plugin_database.commit()

    due_tasks = await repository.get_due_scheduled_tasks("2026-01-01T00:01:00+00:00")

    assert due_tasks == []


async def test_guild_has_event_subscription(plugin_database: aiosqlite.Connection) -> None:
    manifest_json = json.dumps(
        {
            "name": "message_logger",
            "version": "1.0.0",
            "description": "記錄訊息編輯",
            "capability_api_version": 1,
            "event_hooks": ["on_message_edit"],
            "required_capabilities": ["storage"],
            "slash_commands": [],
        },
        ensure_ascii=False,
    )
    await repository.submit_plugin_version(
        plugin_id="message_logger",
        author_id=1234,
        name="message_logger",
        version="1.0.0",
        manifest_json=manifest_json,
        source_code="function on_message_edit(payload) end",
        capability_api_version=1,
    )
    await repository.create_installation(1111, "message_logger", "1.0.0", ["storage"])

    assert await repository.guild_has_event_subscription(1111, {"on_message_edit"}) is True
    assert await repository.guild_has_event_subscription(1111, {"on_voice_state_update"}) is False


async def test_guild_event_subscription_cache_is_invalidated_on_installation_change(
    plugin_database: aiosqlite.Connection,
) -> None:
    """
    事件訂閱查詢有短期快取，但安裝變更後必須立即失效，避免 on_message 快取判斷吃到舊結果。
    """
    manifest_json = json.dumps({"event_hooks": ["on_message_edit"]})
    await repository.submit_plugin_version(
        plugin_id="message_logger",
        author_id=1234,
        name="message_logger",
        version="1.0.0",
        manifest_json=manifest_json,
        source_code="function on_message_edit(payload) end",
        capability_api_version=1,
    )

    assert await repository.guild_has_event_subscription(1111, {"on_message_edit"}) is False

    await repository.create_installation(1111, "message_logger", "1.0.0", ["storage"])

    assert await repository.guild_has_event_subscription(1111, {"on_message_edit"}) is True


async def test_guild_has_event_subscription_ignores_suspended_plugins(
    plugin_database: aiosqlite.Connection,
) -> None:
    manifest_json = json.dumps(
        {
            "name": "message_logger",
            "version": "1.0.0",
            "description": "記錄訊息編輯",
            "capability_api_version": 1,
            "event_hooks": ["on_message_edit"],
            "required_capabilities": ["storage"],
            "slash_commands": [],
        },
        ensure_ascii=False,
    )
    await repository.submit_plugin_version(
        plugin_id="message_logger",
        author_id=1234,
        name="message_logger",
        version="1.0.0",
        manifest_json=manifest_json,
        source_code="function on_message_edit(payload) end",
        capability_api_version=1,
    )
    await repository.create_installation(1111, "message_logger", "1.0.0", ["storage"])
    await repository.suspend_plugin("message_logger")

    assert await repository.guild_has_event_subscription(1111, {"on_message_edit"}) is False


async def test_get_execution_stats_returns_empty_summary_when_no_logs(
    plugin_database: aiosqlite.Connection,
) -> None:
    stats = await repository.get_execution_stats("nonexistent_plugin")

    assert stats == {"total": 0, "outcome_counts": {}, "avg_execution_ms": None, "p95_execution_ms": None}


async def test_get_execution_stats_aggregates_outcomes_and_timing(
    plugin_database: aiosqlite.Connection,
) -> None:
    await repository.log_execution(1111, "temp_role_punishment", "on_message", "[]", 10, "success")
    await repository.log_execution(1111, "temp_role_punishment", "on_message", "[]", 20, "success")
    await repository.log_execution(1111, "temp_role_punishment", "on_message", "[]", 30, "crashed", "boom")
    await repository.log_execution(2222, "temp_role_punishment", "on_message", "[]", 999, "success")

    stats = await repository.get_execution_stats("temp_role_punishment", guild_id=1111)

    assert stats["total"] == 3
    assert stats["outcome_counts"] == {"success": 2, "crashed": 1}
    assert stats["avg_execution_ms"] == pytest.approx(20.0)
    assert stats["p95_execution_ms"] == 30


async def test_get_execution_stats_filters_by_since(plugin_database: aiosqlite.Connection) -> None:
    db = database.get_db()
    await db.execute(
        """
        INSERT INTO plugin_execution_log
            (guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1111, "temp_role_punishment", "on_message", "[]", 5, "success", None, "2020-01-01T00:00:00"),
    )
    await db.commit()
    await repository.log_execution(1111, "temp_role_punishment", "on_message", "[]", 50, "success")

    stats = await repository.get_execution_stats("temp_role_punishment", since="2025-01-01T00:00:00")

    assert stats["total"] == 1
    assert stats["avg_execution_ms"] == pytest.approx(50.0)


async def test_list_plugin_installation_blocks_scoped_to_guild(plugin_database: aiosqlite.Connection) -> None:
    await repository.block_plugin_installation(1111, "plugin_a", "reason a")
    await repository.block_plugin_installation(1111, "plugin_b")
    await repository.block_plugin_installation(2222, "plugin_a", "other guild")

    blocks = await repository.list_plugin_installation_blocks(1111)

    assert [block["plugin_id"] for block in blocks] == ["plugin_a", "plugin_b"]
    assert blocks[0]["reason"] == "reason a"
    assert blocks[1]["reason"] is None
    assert await repository.list_plugin_installation_blocks(3333) == []


async def test_list_known_guild_ids_unions_all_sources(plugin_database: aiosqlite.Connection) -> None:
    await repository.set_guild_resource_tier(1111, "default")
    await repository.block_plugin_installation(2222, "plugin_a")
    await repository.submit_plugin_version(
        plugin_id="plugin_a",
        author_id=1,
        name="plugin_a",
        version="1.0.0",
        manifest_json='{"name": "plugin_a"}',
        source_code="function on_message(payload) end",
        capability_api_version=1,
    )
    await repository.create_installation(3333, "plugin_a", "1.0.0", [])

    assert await repository.list_known_guild_ids() == [1111, 2222, 3333]
