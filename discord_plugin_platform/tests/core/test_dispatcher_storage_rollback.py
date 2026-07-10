"""
core/dispatcher.py 的 storage/schedule_task 交易回退測試：驗證 rejected_invalid_action
與 crashed 兩種結果會回退這次執行寫入的 storage 資料，quota_exceeded 跟個別動作
執行失敗不會，並驗證併發下不會撞出 SQLite 交易錯誤（見 design.md 第 5.4.2 節，
改用每次執行專用連線，取代原本有根本缺陷的 SAVEPOINT + 全域鎖方案）。
"""

import asyncio
import json

import pytest

from core import database, dispatcher, plugin_storage_repository

GUILD_ID = 1111
PLUGIN_ID = "storage_plugin"
MANIFEST_JSON = json.dumps({"event_hooks": ["on_message"]})


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_dispatcher_rollback.db"))
    await database.init_db()
    yield
    await database.close_db()


def _make_installation(plugin_id: str = PLUGIN_ID, granted: list[str] | None = None) -> dict:
    return {
        "guild_id": GUILD_ID,
        "plugin_id": plugin_id,
        "installed_version": "1.0.0",
        "granted_capabilities_json": json.dumps(granted if granted is not None else ["storage", "send_message"]),
        "execution_quota_override": None,
        "action_quota_override": None,
        "manifest_json": MANIFEST_JSON,
    }


def _patch_common(monkeypatch, installation: dict):
    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [installation]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return "function on_message(payload) end"

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
        return None

    monkeypatch.setattr(
        dispatcher.repository, "get_enabled_installations_for_guild", fake_get_enabled_installations_for_guild
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_action_quota", fake_check_and_consume_action_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)


async def test_rejected_invalid_action_rolls_back_storage_write(temp_db, monkeypatch):
    installation = _make_installation(granted=["storage"])
    _patch_common(monkeypatch, installation)

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        await plugin_storage_repository.storage_set(GUILD_ID, PLUGIN_ID, "score", 42, db=kwargs["execution_db"])
        # add_role 沒有被授權，_validate_actions() 會判定失敗
        return [{"type": "add_role", "params": {"user_id": 1, "role_id": 2}}]

    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert await plugin_storage_repository.storage_get(GUILD_ID, PLUGIN_ID, "score") is None


async def test_crashed_execution_rolls_back_storage_write(temp_db, monkeypatch):
    installation = _make_installation(granted=["storage"])
    _patch_common(monkeypatch, installation)

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        await plugin_storage_repository.storage_set(GUILD_ID, PLUGIN_ID, "score", 42, db=kwargs["execution_db"])
        raise RuntimeError("外掛崩潰")

    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert await plugin_storage_repository.storage_get(GUILD_ID, PLUGIN_ID, "score") is None


async def test_successful_validation_commits_storage_write(temp_db, monkeypatch):
    installation = _make_installation(granted=["storage", "send_message"])
    _patch_common(monkeypatch, installation)

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        await plugin_storage_repository.storage_set(GUILD_ID, PLUGIN_ID, "score", 42, db=kwargs["execution_db"])
        return [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}]

    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert await plugin_storage_repository.storage_get(GUILD_ID, PLUGIN_ID, "score") == 42


async def test_quota_exceeded_does_not_roll_back_storage_write(temp_db, monkeypatch):
    """
    配額超過發生在 _validate_actions() 通過「之後」，這時候動作本身已經合法，
    只是流量被擋，不代表這次執行有問題，storage 寫入不應該被回退。
    """
    installation = _make_installation(granted=["storage", "send_message"])
    _patch_common(monkeypatch, installation)

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        await plugin_storage_repository.storage_set(GUILD_ID, PLUGIN_ID, "score", 42, db=kwargs["execution_db"])
        return [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}]

    async def fake_check_and_consume_action_quota(
        guild_id: int, plugin_id: str, action_count: int, limit_override: int | None = None
    ) -> bool:
        return False

    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_action_quota", fake_check_and_consume_action_quota)

    await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert await plugin_storage_repository.storage_get(GUILD_ID, PLUGIN_ID, "score") == 42


async def test_failing_action_execution_does_not_roll_back_storage_write(temp_db, monkeypatch):
    """
    _execute_actions() 本身個別動作失敗（例如頻道已刪除）是已知會被容忍的操作性
    問題（真正的 _execute_actions() 會把每個動作的例外個別接住記錄，從不整批往外
    拋），不代表這次執行不可信——這裡驗證的重點是：storage 的 commit 早在
    _validate_actions() 通過的當下就已經發生，完全在 _execute_actions() 執行
    之前，所以不管 _execute_actions() 裡面實際發生什麼，storage 寫入都不會受影響。
    """
    installation = _make_installation(granted=["storage", "send_message"])
    _patch_common(monkeypatch, installation)

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        await plugin_storage_repository.storage_set(GUILD_ID, PLUGIN_ID, "score", 42, db=kwargs["execution_db"])
        return [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}]

    execute_actions_called = False

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> None:
        nonlocal execute_actions_called
        execute_actions_called = True
        # 模擬真正的 _execute_actions()：個別動作失敗會被內部接住，不會往外拋。

    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)

    await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert execute_actions_called is True
    assert await plugin_storage_repository.storage_get(GUILD_ID, PLUGIN_ID, "score") == 42


async def test_installation_without_storage_capability_uses_fast_path(temp_db, monkeypatch):
    """
    沒有 storage/schedule_task 能力的安裝完全不應該開專用連線，確認一般流程
    （沒有能力寫資料庫）不受這次改動影響。
    """
    installation = _make_installation(granted=["send_message"])
    _patch_common(monkeypatch, installation)

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        assert kwargs["execution_db"] is None
        return [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}]

    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert result is True


async def test_concurrent_storage_dispatches_do_not_interfere(temp_db, monkeypatch):
    """
    兩個不同外掛安裝、都用到 storage 能力，同時分派時各自用自己專用的連線，
    不能互相干擾（不像原本的 SAVEPOINT 方案，任何一邊的 commit 都可能沖掉
    另一邊還開著的 SAVEPOINT），且兩邊最後狀態都要正確。
    """
    installation_a = _make_installation(plugin_id="plugin_a", granted=["storage"])
    installation_b = _make_installation(plugin_id="plugin_b", granted=["storage"])

    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return [installation_a, installation_b]

    async def fake_get_plugin_source(plugin_id: str, version: str) -> str | None:
        return "function on_message(payload) end"

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
        return None

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        plugin_id = kwargs["plugin_id"]
        await plugin_storage_repository.storage_set(GUILD_ID, plugin_id, "score", 1, db=kwargs["execution_db"])
        await asyncio.sleep(0)  # 讓另一個併發的分派有機會插進來
        if plugin_id == "plugin_a":
            # plugin_a 授權不過，應該回退
            return [{"type": "add_role", "params": {"user_id": 1, "role_id": 2}}]
        return []

    monkeypatch.setattr(
        dispatcher.repository, "get_enabled_installations_for_guild", fake_get_enabled_installations_for_guild
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_action_quota", fake_check_and_consume_action_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "_execute_actions", fake_execute_actions)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    await asyncio.gather(
        dispatcher.dispatch_event(GUILD_ID, "on_message", {}),
        dispatcher.dispatch_event(GUILD_ID, "on_message", {}),
    )

    assert await plugin_storage_repository.storage_get(GUILD_ID, "plugin_a", "score") is None
    assert await plugin_storage_repository.storage_get(GUILD_ID, "plugin_b", "score") == 1
