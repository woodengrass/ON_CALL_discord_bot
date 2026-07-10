"""
故障注入：核心分派迴圈 core/dispatcher.py 對「執行專用連線」(design.md 第 5.4.2 節，
Fix 3) 本身壞掉的容錯——不是外掛執行例外、也不是動作驗證失敗，而是連線基礎設施
自己出問題（連線失敗、commit()/rollback() 中途失敗）。

這組測試補的缺口：tests/core/test_dispatcher_storage_rollback.py 涵蓋的是「外掛執行
crashed/rejected_invalid_action 時,已提交的 storage 寫入要回退」，前提是專用連線本身
運作正常；這裡驗證的是連線本身失敗時，dispatch_event() 是否還能維持「記錄 crashed、
不炸穿整個分派迴圈」的既定行為（見第 12.3 節第 5 點同類基礎設施例外的既有修法）。
"""

import json

import pytest

from core import database, dispatcher

GUILD_ID = 2222
PLUGIN_ID = "storage_plugin"
MANIFEST_JSON = json.dumps({"event_hooks": ["on_message"]})


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_fault_injection_dispatcher_db.db"))
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


def _patch_common(monkeypatch, installations: list[dict]):
    async def fake_get_enabled_installations_for_guild(guild_id: int) -> list[dict]:
        return installations

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

    async def fake_execute_actions(guild_id: int, actions: list[dict]) -> list[dict]:
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


async def test_execution_db_connect_failure_logs_crashed_not_unhandled(temp_db, monkeypatch):
    """
    _open_execution_db() 底層的 aiosqlite.connect() 失敗（例如磁碟/檔案系統問題），
    不應該讓例外原封不動炸穿 dispatch_event()——應該記錄一筆 crashed 並繼續。
    """
    installation = _make_installation(granted=["storage"])
    _patch_common(monkeypatch, [installation])

    logged_entries: list[dict] = []

    async def fake_log_execution(guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error=None):
        logged_entries.append({"outcome": outcome, "actions_json": actions_json, "error": error})

    async def failing_connect(*args, **kwargs):
        raise OSError("模擬磁碟 I/O 失敗")

    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.aiosqlite, "connect", failing_connect)

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert result is False
    assert logged_entries == [{"outcome": "crashed", "actions_json": "[]", "error": "模擬磁碟 I/O 失敗"}]


async def test_execution_db_connect_failure_does_not_block_other_installations(temp_db, monkeypatch):
    """
    一個安裝的專用連線開啟失敗，不應該讓同一次分派迴圈裡後面其他安裝都被跳過
    ——這正是第 12.3 節第 5 點已經修好的「基礎設施例外炸穿整個迴圈」同一類問題，
    這裡驗證的是專用連線這條路徑也有一樣的保護。
    """
    installation_a = _make_installation(plugin_id="plugin_a", granted=["storage"])
    installation_b = _make_installation(plugin_id="plugin_b", granted=["send_message"])
    _patch_common(monkeypatch, [installation_a, installation_b])

    logged_entries: list[dict] = []

    async def fake_log_execution(guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error=None):
        logged_entries.append({"plugin_id": plugin_id, "outcome": outcome})

    async def flaky_connect(*args, **kwargs):
        # plugin_a 需要 storage 能力才會走到這裡；plugin_b 沒有 storage 能力，
        # 完全不會呼叫 connect()，用真連線讓它正常執行完畢。
        raise OSError("模擬磁碟 I/O 失敗")

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        return [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}]

    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.aiosqlite, "connect", flaky_connect)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert result is True
    assert logged_entries == [
        {"plugin_id": "plugin_a", "outcome": "crashed"},
        {"plugin_id": "plugin_b", "outcome": "success"},
    ]


async def test_commit_failure_after_valid_actions_logs_crashed(temp_db, monkeypatch):
    """
    模擬連線在驗證通過、正要 commit() 的當下才真正壞掉（例如連線被外部關掉）：
    commit() 本身丟出例外時，不應該被當成 success 靜靜吞掉，應該記錄 crashed。
    """
    installation = _make_installation(granted=["storage", "send_message"])
    _patch_common(monkeypatch, [installation])

    logged_entries: list[dict] = []

    async def fake_log_execution(guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error=None):
        logged_entries.append({"outcome": outcome})

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        # 模擬連線在外掛執行完、驗證通過後,commit() 之前就已經被外部關閉。
        await kwargs["execution_db"].close()
        return [{"type": "send_message", "params": {"channel_id": 1, "content": "hi"}}]

    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert result is False
    assert logged_entries == [{"outcome": "crashed"}]


async def test_rollback_failure_during_crashed_path_still_logs_crashed(temp_db, monkeypatch):
    """
    模擬「外掛執行本身崩潰」且緊接著 rollback() 也失敗（連線已經在崩潰前就被關閉
    ——例如子行程與主行程之間的連線在极端情況下互相干擾）：驗證這種雙重失敗仍然
    收斂成一筆 crashed 記錄，不會讓例外逃出 dispatch_event()。
    """
    installation = _make_installation(granted=["storage"])
    _patch_common(monkeypatch, [installation])

    logged_entries: list[dict] = []

    async def fake_log_execution(guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error=None):
        logged_entries.append({"outcome": outcome})

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        await kwargs["execution_db"].close()
        raise RuntimeError("外掛崩潰，連線也已經先被關閉")

    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    assert result is False
    assert logged_entries == [{"outcome": "crashed"}]
