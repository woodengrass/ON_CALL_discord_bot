import json
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from core import database, dispatcher, quota, repository
from core.dispatcher import _installation_handles_event, _validate_actions


@pytest.fixture()
async def plugin_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[aiosqlite.Connection]:
    """
    建立每個 dispatcher 整合測試專用的暫存外掛平台資料庫。
    """
    await database.close_db()
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "dispatcher_plugin_platform.db"))
    await database.init_db()
    yield database.get_db()
    await database.close_db()


def test_installation_handles_only_manifest_events() -> None:
    """
    dispatcher 應只把事件分派給 manifest 有訂閱該事件的安裝。
    """
    installation = {
        "plugin_id": "message_logger",
        "manifest_json": json.dumps({"event_hooks": ["on_message_edit"]}),
    }

    assert _installation_handles_event(installation, "on_message_edit") is True
    assert _installation_handles_event(installation, "on_voice_state_update") is False


def test_validate_actions_uses_granted_capabilities_json() -> None:
    """
    動作驗證應解析 granted_capabilities_json，而不是把 JSON 字串拆成字元。
    """
    installation = {"granted_capabilities_json": json.dumps(["send_message"])}

    assert _validate_actions(
        installation,
        [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}],
    ) is True
    assert _validate_actions(installation, [{"type": "add_role", "params": {}}]) is False


def test_validate_actions_rejects_non_deferred_and_malformed_actions() -> None:
    """
    宿主端只接受 dispatcher 支援的延後動作，且 action shape 與 params 必須符合規格。
    """
    installation = {"granted_capabilities_json": json.dumps(["send_message", "read_message_history"])}

    assert _validate_actions(
        installation,
        [{"type": "read_message_history", "params": {"channel_id": 1, "limit": 10}}],
    ) is False
    assert _validate_actions(
        installation,
        [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}, "extra": True}],
    ) is False
    assert _validate_actions(
        installation,
        [{"type": "send_message", "params": {"channel_id": 1, "content": "hi", "unknown": "x"}}],
    ) is False
    assert _validate_actions(
        installation,
        [{"type": "send_message", "params": {"channel_id": "bad", "content": "hi"}}],
    ) is False


def test_validate_actions_rejects_too_many_actions() -> None:
    """
    單次執行最多允許 20 個延後動作。
    """
    installation = {"granted_capabilities_json": json.dumps(["send_message"])}
    actions = [
        {"type": "send_message", "params": {"channel_id": index + 1, "content": "hi"}}
        for index in range(dispatcher.MAX_ACTIONS_PER_EXECUTION + 1)
    ]

    assert _validate_actions(installation, actions) is False


async def test_dispatch_event_passes_source_code_and_granted_capabilities(monkeypatch) -> None:
    """
    dispatch_event 應從 repository 讀取原始碼與授權能力後再執行外掛。
    """
    captured_execution: dict = {}
    logged_entries: list[dict] = []

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "message_logger",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "execution_quota_override": None,
                "action_quota_override": None,
                "manifest_json": json.dumps({"event_hooks": ["on_message"]}),
            }
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        assert plugin_id == "message_logger"
        assert version == "1.0.0"
        return "function on_message(payload) end"

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        captured_execution.update(kwargs)
        return []

    async def fake_resolve_resource_limits(guild_id: int, plugin_id: str) -> dict:
        return {"execution_quota": 60, "action_quota": 30}

    async def fake_check_and_consume_execution_quota(
        guild_id: int, plugin_id: str, limit_override: int | None = None
    ) -> bool:
        assert limit_override == 60
        return True

    async def fake_check_and_consume_action_quota(
        guild_id: int, plugin_id: str, action_count: int, limit_override: int | None = None
    ) -> bool:
        assert limit_override == 30
        return True

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> None:
        return None

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        logged_entries.append({"outcome": outcome, "actions_json": actions_json})

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_action_quota", fake_check_and_consume_action_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    dispatch_succeeded = await dispatcher.dispatch_event(1111, "on_message", {"content": "hello"})

    assert captured_execution["source_code"] == "function on_message(payload) end"
    assert captured_execution["granted_capabilities"] == {"send_message"}
    assert dispatch_succeeded is True
    assert logged_entries == [{"outcome": "success", "actions_json": "[]"}]


async def test_dispatch_event_recovers_when_execute_actions_raises(monkeypatch) -> None:
    """
    _execute_actions() 呼叫 bot_registry.get_bot()，如果 Cog 還沒載入完成
    （或測試忘記 set_bot()）會丟出未捕獲的 RuntimeError。這裡驗證這種
    基礎設施層級的例外只會讓「這一個安裝」記成 crashed，不會炸穿整個
    dispatch_event()、連帶讓迴圈裡後面其他安裝都沒被處理。
    """
    logged_entries: list[dict] = []

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "plugin_a",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "execution_quota_override": None,
                "action_quota_override": None,
                "manifest_json": json.dumps({"event_hooks": ["on_message"]}),
            },
            {
                "guild_id": guild_id,
                "plugin_id": "plugin_b",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "execution_quota_override": None,
                "action_quota_override": None,
                "manifest_json": json.dumps({"event_hooks": ["on_message"]}),
            },
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return "function on_message(payload) end"

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        return [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}]

    async def fake_resolve_resource_limits(guild_id: int, plugin_id: str) -> dict:
        return {"execution_quota": 60, "action_quota": 30}

    async def fake_check_and_consume_execution_quota(
        guild_id: int, plugin_id: str, limit_override: int | None = None
    ) -> bool:
        return True

    async def fake_check_and_consume_action_quota(
        guild_id: int, plugin_id: str, action_count: int, limit_override: int | None = None
    ) -> bool:
        return True

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> None:
        # 模擬 bot_registry.get_bot() 在 set_bot() 還沒被呼叫時丟出的例外，
        # 只讓第一個安裝（plugin_a）踩到，第二個安裝（plugin_b）應該正常執行。
        raise RuntimeError("尚未註冊 bot 實例，請先呼叫 set_bot()")

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        logged_entries.append({"plugin_id": plugin_id, "outcome": outcome})

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_action_quota", fake_check_and_consume_action_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    dispatch_succeeded = await dispatcher.dispatch_event(1111, "on_message", {"content": "hello"})

    # plugin_a 因為 _execute_actions() 拋例外而失敗，但沒有整批中止，
    # dispatch_event() 應該還是正常回傳、且沒有整個崩潰。
    assert dispatch_succeeded is False
    assert logged_entries == [
        {"plugin_id": "plugin_a", "outcome": "crashed"},
        {"plugin_id": "plugin_b", "outcome": "crashed"},
    ]


async def test_dispatch_event_logs_crashed_when_source_code_missing(monkeypatch) -> None:
    """
    找不到外掛原始碼時，dispatcher 應記錄 crashed 並跳過執行。
    """
    logged_errors: list[str | None] = []
    execute_called = False

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "message_logger",
                "installed_version": "missing",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "manifest_json": json.dumps({"event_hooks": ["on_message"]}),
            }
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return None

    async def fake_resolve_resource_limits(guild_id: int, plugin_id: str) -> dict:
        return {"execution_quota": 60, "action_quota": 30}

    async def fake_check_and_consume_execution_quota(
        guild_id: int, plugin_id: str, limit_override: int | None = None
    ) -> bool:
        return True

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        nonlocal execute_called
        execute_called = True
        return []

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        assert outcome == "crashed"
        logged_errors.append(error)

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    await dispatcher.dispatch_event(1111, "on_message", {"content": "hello"})

    assert execute_called is False
    assert logged_errors == ["找不到外掛原始碼"]


async def test_dispatch_event_filters_target_plugin(monkeypatch) -> None:
    """
    target_plugin_id 應只分派給指定外掛，但仍保留事件訂閱等既有檢查。
    """
    executed_plugin_ids: list[str] = []

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "first_plugin",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "manifest_json": json.dumps({"event_hooks": ["on_scheduled_task"]}),
            },
            {
                "guild_id": guild_id,
                "plugin_id": "second_plugin",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps(["send_message"]),
                "manifest_json": json.dumps({"event_hooks": ["on_scheduled_task"]}),
            },
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return "function on_scheduled_task(payload) end"

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        executed_plugin_ids.append(kwargs["plugin_id"])
        return []

    async def fake_resolve_resource_limits(guild_id: int, plugin_id: str) -> dict:
        return {"execution_quota": 60, "action_quota": 30}

    async def fake_check_and_consume_execution_quota(
        guild_id: int, plugin_id: str, limit_override: int | None = None
    ) -> bool:
        return True

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> None:
        return None

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        return None

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    dispatch_succeeded = await dispatcher.dispatch_event(
        1111,
        "on_scheduled_task",
        {"task_name": "restore", "payload": {}},
        target_plugin_id="second_plugin",
    )

    assert dispatch_succeeded is True
    assert executed_plugin_ids == ["second_plugin"]


async def test_dispatch_event_passes_resolved_resource_limits(monkeypatch) -> None:
    """
    dispatcher 應只呼叫 repository.resolve_resource_limits() 一次，並把結果傳給 worker。
    """
    captured_execution: dict = {}

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [
            {
                "guild_id": guild_id,
                "plugin_id": "message_logger",
                "installed_version": "1.0.0",
                "granted_capabilities_json": json.dumps([]),
                "execution_quota_override": None,
                "action_quota_override": None,
                "resource_overrides_json": None,
                "manifest_json": json.dumps({"event_hooks": ["on_message"]}),
            }
        ]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return "function on_message(payload) end"

    async def fake_resolve_resource_limits(guild_id: int, plugin_id: str) -> dict:
        return {"execution_quota": 7, "action_quota": 11, "instruction_limit": 1000, "memory_limit_bytes": 1024}

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        captured_execution.update(kwargs)
        return []

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> list[dict]:
        return []

    async def fake_check_and_consume_execution_quota(
        guild_id: int, plugin_id: str, limit_override: int | None = None
    ) -> bool:
        assert limit_override == 7
        return True

    async def fake_log_execution(
        guild_id: int,
        plugin_id: str,
        event_type: str,
        actions_json: str,
        execution_ms: int,
        outcome: str,
        error: str | None = None,
    ) -> None:
        return None

    monkeypatch.setattr(
        dispatcher.repository,
        "get_enabled_installations_for_guild",
        fake_get_enabled_installations_for_guild,
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    assert await dispatcher.dispatch_event(1111, "on_message", {"content": "hello"}) is True
    assert captured_execution["resource_overrides"] == {
        "execution_quota": 7,
        "action_quota": 11,
        "instruction_limit": 1000,
        "memory_limit_bytes": 1024,
    }


async def test_dispatch_event_uses_tier_execution_quota_without_installation_override(
    plugin_database: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    方案設定 execution_quota=2 且安裝沒有個別覆蓋時，第 3 次分派應被配額擋下。
    """
    plugin_id = "tier_limited_plugin"
    quota.clear_usage(1111, plugin_id)
    await repository.submit_plugin_version(
        plugin_id=plugin_id,
        author_id=1234,
        name=plugin_id,
        version="1.0.0",
        manifest_json=json.dumps({"event_hooks": ["on_message"]}),
        source_code="function on_message(payload) end",
        capability_api_version=1,
    )
    await repository.set_plugin_tier_config(
        plugin_id,
        repository.DEFAULT_RESOURCE_TIER,
        {"allowed": True, "execution_quota": 2, "action_quota": 30},
    )
    await repository.create_installation(1111, plugin_id, "1.0.0", [])

    async def fake_execute_plugin_event(**kwargs: object) -> list[dict]:
        return []

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> list[dict]:
        return []

    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    assert await dispatcher.dispatch_event(1111, "on_message", {"content": "first"}) is True
    assert await dispatcher.dispatch_event(1111, "on_message", {"content": "second"}) is True
    assert await dispatcher.dispatch_event(1111, "on_message", {"content": "third"}) is False

    async with plugin_database.execute(
        "SELECT outcome FROM plugin_execution_log WHERE plugin_id = ? ORDER BY log_id",
        (plugin_id,),
    ) as cursor:
        rows = await cursor.fetchall()
    quota.clear_usage(1111, plugin_id)

    assert [row[0] for row in rows] == ["success", "success", "quota_exceeded"]
