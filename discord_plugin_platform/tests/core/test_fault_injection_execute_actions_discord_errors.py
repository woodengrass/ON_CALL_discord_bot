"""
故障注入：core/dispatcher.py 的 _execute_actions() 遇到真正的 discord.py 例外型別
（rate limit / 權限不足 / 資源已刪除），而不是泛用的 RuntimeError。

design.md 明確定案（第 3.2、5.4.2 節）：真正呼叫 Discord API 失敗是「操作性問題」，
跟 rejected_invalid_action／crashed 不是同一類——不應該回退同一次執行已經提交的
storage/schedule_task 寫入，也不應該讓一個動作失敗擋住佇列裡其餘動作。
tests/core/test_dispatcher_execute_actions.py 已經測過「一個失敗不擋住其他動作」，
但用的是泛用 RuntimeError；tests/core/test_dispatcher_storage_rollback.py 已經測過
storage 不回退，但整個 _execute_actions() 是被 monkeypatch 掉的假函式。這裡把兩者
結合，且改用真正的 discord.Forbidden/NotFound/HTTPException 型別注入，覆蓋這兩個
既有測試檔案都還沒踩到的組合。
"""

import json

import discord
import pytest

from core import bot_registry, database, dispatcher, plugin_storage_repository

GUILD_ID = 3333
PLUGIN_ID = "notifier_plugin"
MANIFEST_JSON = json.dumps({"event_hooks": ["on_message"]})


class _FakeHttpResponse:
    def __init__(self, status: int, reason: str):
        self.status = status
        self.reason = reason


class _FakeChannel:
    def __init__(self, channel_id, recorder, send_error=None, messages=None):
        self.id = channel_id
        self._recorder = recorder
        self._send_error = send_error
        self._messages = messages or {}

    async def send(self, **kwargs):
        if self._send_error is not None:
            raise self._send_error
        self._recorder.append(("send", self.id, kwargs))

    async def fetch_message(self, message_id):
        entry = self._messages.get(message_id)
        if isinstance(entry, Exception):
            raise entry
        return entry


class _FakeMessage:
    def __init__(self, message_id, recorder, delete_error=None):
        self.id = message_id
        self._recorder = recorder
        self._delete_error = delete_error

    async def delete(self):
        if self._delete_error is not None:
            raise self._delete_error
        self._recorder.append(("delete", self.id))


class _FakeGuild:
    def __init__(self, channels):
        self.id = GUILD_ID
        self._channels = channels

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    def get_member(self, user_id):
        return None

    def get_role(self, role_id):
        return None

    def get_thread(self, thread_id):
        return None


class _FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild if guild_id == self._guild.id else None


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_fault_injection_execute_actions.db"))
    await database.init_db()
    yield
    await database.close_db()


async def test_forbidden_error_on_one_action_does_not_block_the_rest():
    """
    discord.Forbidden（機器人被移除傳訊息權限）發生在其中一個動作時，
    其餘動作應該照常執行，且錯誤要以真實例外訊息記錄下來。
    """
    recorder: list = []
    forbidden_channel = _FakeChannel(
        42, recorder, send_error=discord.Forbidden(_FakeHttpResponse(403, "Forbidden"), "Missing Permissions")
    )
    working_channel = _FakeChannel(43, recorder)
    guild = _FakeGuild({42: forbidden_channel, 43: working_channel})
    bot_registry.set_bot(_FakeBot(guild))

    action_errors = await dispatcher._execute_actions(
        GUILD_ID,
        [
            {"type": "send_message", "params": {"channel_id": 42, "content": "blocked"}},
            {"type": "send_message", "params": {"channel_id": 43, "content": "still works"}},
        ],
    )

    assert recorder == [("send", 43, {"content": "still works", "embed": None, "view": None})]
    assert len(action_errors) == 1
    assert action_errors[0]["index"] == 0
    assert action_errors[0]["type"] == "send_message"
    assert "403" in action_errors[0]["error"] or "Forbidden" in action_errors[0]["error"]


async def test_not_found_error_on_delete_message_recorded_and_does_not_block_rest():
    """
    discord.NotFound（訊息已經被別人刪除）發生在 delete_message 時，
    應該記錄成 action-level 錯誤，且不影響佇列裡其他動作。
    """
    recorder: list = []
    missing_message_channel = _FakeChannel(
        42,
        recorder,
        messages={100: discord.NotFound(_FakeHttpResponse(404, "Not Found"), "Unknown Message")},
    )
    ok_message_channel = _FakeChannel(43, recorder, messages={200: _FakeMessage(200, recorder)})
    guild = _FakeGuild({42: missing_message_channel, 43: ok_message_channel})
    bot_registry.set_bot(_FakeBot(guild))

    action_errors = await dispatcher._execute_actions(
        GUILD_ID,
        [
            {"type": "delete_message", "params": {"channel_id": 42, "message_id": 100}},
            {"type": "delete_message", "params": {"channel_id": 43, "message_id": 200}},
        ],
    )

    assert recorder == [("delete", 200)]
    assert len(action_errors) == 1
    assert action_errors[0]["index"] == 0
    assert action_errors[0]["type"] == "delete_message"


async def test_http_exception_rate_limit_does_not_roll_back_storage_write(temp_db, monkeypatch):
    """
    真正的 _execute_actions()（不是假函式）遇到 discord.HTTPException（rate limit）時,
    這次執行早前已經提交的 storage 寫入不應該被回退——這是結合真實 discord 例外型別
    與真實 dispatcher 流程（含專用連線 commit 時機）的端到端驗證。
    """
    recorder: list = []
    rate_limited_channel = _FakeChannel(
        42,
        recorder,
        send_error=discord.HTTPException(_FakeHttpResponse(429, "Too Many Requests"), {"message": "rate limited"}),
    )
    guild = _FakeGuild({42: rate_limited_channel})
    bot_registry.set_bot(_FakeBot(guild))

    installation = {
        "guild_id": GUILD_ID,
        "plugin_id": PLUGIN_ID,
        "installed_version": "1.0.0",
        "granted_capabilities_json": json.dumps(["storage", "send_message"]),
        "execution_quota_override": None,
        "action_quota_override": None,
        "manifest_json": MANIFEST_JSON,
    }

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

    async def fake_execute_plugin_event(**kwargs) -> list[dict]:
        await plugin_storage_repository.storage_set(GUILD_ID, PLUGIN_ID, "score", 42, db=kwargs["execution_db"])
        return [{"type": "send_message", "params": {"channel_id": 42, "content": "hi"}}]

    monkeypatch.setattr(
        dispatcher.repository, "get_enabled_installations_for_guild", fake_get_enabled_installations_for_guild
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_action_quota", fake_check_and_consume_action_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)
    monkeypatch.setattr(dispatcher, "execute_plugin_event", fake_execute_plugin_event)

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {})

    # _execute_actions() 內部接住了 HTTPException,不會往外拋，所以 dispatch_event
    # 仍然記錄 outcome=success（含 action_errors 摘要），不是 crashed。
    assert result is True
    assert recorder == []
    assert await plugin_storage_repository.storage_get(GUILD_ID, PLUGIN_ID, "score") == 42
