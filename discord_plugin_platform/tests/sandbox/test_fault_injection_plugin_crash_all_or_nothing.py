"""
故障注入：外掛程式碼在真正的 Track G 子行程沙箱裡執行到一半崩潰，驗證
design.md 第 3.2 節「執行中途崩潰...整批回退，此次執行視為完全沒有發生任何動作」
這條「all or nothing」語意在真正的子行程路徑（sandbox/worker.py 的
execute_plugin_event() 真的 spawn 子行程,不是 monkeypatch 掉的假函式）下依然成立。

tests/sandbox/test_worker.py 已經涵蓋「頂層語法錯誤」「未授權能力呼叫失敗」
「無窮迴圈被步數上限攔截」這幾種情況（都是子行程完全沒有機會把任何動作排進
佇列），這裡刻意測試「已經排了幾個動作之後才崩潰」這個更貼近 design.md 描述的
案例（"已經在動作佇列裡排了幾個項目"），以及結合 core/dispatcher.py 的真實
storage 交易，確認整條路徑（子行程 crash -> RpcBackend 回報 -> dispatcher 判定
crashed -> execution_db 回退）都是真的在跑，不是任何一層被假函式取代。
"""

import json

import pytest

from core import bot_registry, database, dispatcher, plugin_storage_repository
from sandbox.engine import SandboxExecutionError
from sandbox.worker import execute_plugin_event

GUILD_ID = 4444
PLUGIN_ID = "crashy_plugin"
MANIFEST_JSON = json.dumps({"event_hooks": ["on_message"]})


class _FakeGuild:
    def __init__(self, guild_id):
        self.id = guild_id
        self.name = "測試伺服器"
        self.member_count = 0

    def get_member(self, user_id):
        return None

    def get_channel(self, channel_id):
        return None

    def get_role(self, role_id):
        return None


class _FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild if guild_id == self._guild.id else None


@pytest.fixture
def fake_bot():
    bot = _FakeBot(_FakeGuild(GUILD_ID))
    bot_registry.set_bot(bot)
    return bot


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_fault_injection_all_or_nothing.db"))
    await database.init_db()
    yield
    await database.close_db()


async def test_runtime_error_after_queuing_action_raises_and_returns_no_partial_queue(fake_bot):
    """
    外掛已經呼叫過 api.send_message()（排進動作佇列）之後才真正崩潰（Lua error()），
    真子行程執行後應該整批視為失敗——execute_plugin_event() 拋出 SandboxExecutionError，
    不會把崩潰前已經排進去的那個動作回傳給呼叫端。
    """
    with pytest.raises(SandboxExecutionError):
        await execute_plugin_event(
            guild_id=GUILD_ID,
            plugin_id=PLUGIN_ID,
            source_code="""
            function on_message(payload)
                api.send_message(payload.channel_id, "queued before crash")
                error("外掛在排入動作之後才真正崩潰")
            end
            """,
            event_type="on_message",
            event_payload={"channel_id": 42},
            granted_capabilities={"send_message"},
        )


async def test_runtime_error_partway_through_multiple_queued_actions_loses_all_of_them(fake_bot):
    """
    崩潰前已經排了兩個動作，崩潰後兩個都不應該存活——不是「回傳崩潰前排好的部分」，
    而是完全沒有發生任何動作，呼應 design.md 舉的例子（排了移除身分組但還沒排
    給予新身分組就崩潰，不能只回傳前半段）。
    """
    with pytest.raises(SandboxExecutionError):
        await execute_plugin_event(
            guild_id=GUILD_ID,
            plugin_id=PLUGIN_ID,
            source_code="""
            function on_message(payload)
                api.send_message(payload.channel_id, "first queued action")
                api.send_message(payload.channel_id, "second queued action")
                local will_crash = nil
                return will_crash.no_such_field
            end
            """,
            event_type="on_message",
            event_payload={"channel_id": 42},
            granted_capabilities={"send_message"},
        )


async def test_dispatch_event_rolls_back_storage_when_real_subprocess_crashes_after_storage_write(
    fake_bot, temp_db, monkeypatch
):
    """
    端到端驗證：真正透過 core/dispatcher.py 分派、真正 spawn Track G 子行程、
    子行程真的呼叫 api.storage_set() 寫進專用連線，接著真的崩潰。確認
    dispatch_event() 判定 crashed 並回退 storage 寫入,不是靠任何一層的假函式
    模擬出來的結果。
    """
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
        return """
        function on_message(payload)
            api.storage_set("score", 42)
            api.send_message(payload.channel_id, "queued before crash")
            error("外掛在寫入 storage、排入動作之後才真正崩潰")
        end
        """

    async def fake_resolve_resource_limits(guild_id: int, plugin_id: str) -> dict:
        return {"execution_quota": 60, "action_quota": 30}

    async def fake_check_and_consume_execution_quota(
        guild_id: int, plugin_id: str, limit_override: int | None = None
    ) -> bool:
        return True

    logged_entries: list[dict] = []

    async def fake_log_execution(guild_id, plugin_id, event_type, actions_json, execution_ms, outcome, error=None):
        logged_entries.append({"outcome": outcome, "actions_json": actions_json})

    monkeypatch.setattr(
        dispatcher.repository, "get_enabled_installations_for_guild", fake_get_enabled_installations_for_guild
    )
    monkeypatch.setattr(dispatcher.repository, "get_plugin_source", fake_get_plugin_source)
    monkeypatch.setattr(dispatcher.repository, "resolve_resource_limits", fake_resolve_resource_limits)
    monkeypatch.setattr(dispatcher.repository, "log_execution", fake_log_execution)
    monkeypatch.setattr(dispatcher.quota, "check_and_consume_execution_quota", fake_check_and_consume_execution_quota)
    monkeypatch.setattr(dispatcher.suspension, "is_suspended", lambda plugin_id: False)

    result = await dispatcher.dispatch_event(GUILD_ID, "on_message", {"channel_id": 42})

    assert result is False
    assert logged_entries == [{"outcome": "crashed", "actions_json": "[]"}]
    assert await plugin_storage_repository.storage_get(GUILD_ID, PLUGIN_ID, "score") is None
