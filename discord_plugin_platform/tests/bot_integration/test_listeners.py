import asyncio
import datetime
import json
from types import SimpleNamespace

import discord

from bot_integration import listeners
from core import message_cache


class FakeBot:
    """
    測試用 Bot，提供排程 loop 需要的 wait_until_ready。
    """

    async def wait_until_ready(self) -> None:
        """
        模擬 bot 已完成準備。
        """


async def test_on_message_dispatches_payload_and_caches_when_needed(monkeypatch) -> None:
    """
    on_message 應轉發訊息事件，且只在有 edit/delete 訂閱時快取內容。
    """
    captured_events: list[tuple[int, str, dict, str | None]] = []

    async def fake_dispatch_event(
        guild_id: int,
        event_type: str,
        event_payload: dict,
        target_plugin_id: str | None = None,
    ) -> bool:
        captured_events.append((guild_id, event_type, event_payload, target_plugin_id))
        return True

    async def fake_guild_has_event_subscription(guild_id: int, event_types: set[str]) -> bool:
        return guild_id == 1111 and event_types == listeners.MESSAGE_CACHE_EVENTS

    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)
    monkeypatch.setattr(listeners.repository, "guild_has_event_subscription", fake_guild_has_event_subscription)

    cog = listeners.PluginPlatformListeners(FakeBot())
    message = SimpleNamespace(
        id=3333,
        guild=SimpleNamespace(id=1111),
        author=SimpleNamespace(id=2222, bot=False),
        channel=SimpleNamespace(id=4444),
        content="hello",
        created_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )

    await cog.on_message(message)

    assert captured_events == [
        (
            1111,
            "on_message",
            {
                "message_id": 3333,
                "author_id": 2222,
                "channel_id": 4444,
                "content": "hello",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            None,
        )
    ]
    assert message_cache.get_cached_message(4444, 3333)["content"] == "hello"
    message_cache.purge_guild(1111)


async def test_on_interaction_dispatches_select_values(monkeypatch) -> None:
    """
    on_interaction 應只轉發 component 互動，並保留 select values。
    """
    captured_events: list[tuple[int, str, dict]] = []

    async def fake_dispatch_event(guild_id: int, event_type: str, event_payload: dict) -> None:
        captured_events.append((guild_id, event_type, event_payload))

    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1111),
        type=discord.InteractionType.component,
        data={"component_type": 3, "custom_id": "role_select", "values": ["role_a"]},
        user=SimpleNamespace(id=2222),
        message=SimpleNamespace(id=3333),
    )

    await cog.on_interaction(interaction)

    assert captured_events == [
        (
            1111,
            "on_interaction",
            {
                "interaction_type": "select_menu",
                "custom_id": "role_select",
                "values": ["role_a"],
                "invoking_user_id": 2222,
                "message_id": 3333,
            },
        )
    ]


async def test_on_interaction_dispatches_slash_command(monkeypatch) -> None:
    """
    application command interaction 應轉成 on_slash_command，讓宣告 slash_commands 的外掛能被觸發。
    """
    captured_events: list[tuple[int, str, dict]] = []

    async def fake_dispatch_event(guild_id: int, event_type: str, event_payload: dict) -> None:
        captured_events.append((guild_id, event_type, event_payload))

    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1111),
        type=discord.InteractionType.application_command,
        data={"name": "temp_role", "options": [{"name": "user", "value": "2222"}]},
        user=SimpleNamespace(id=3333),
        channel=SimpleNamespace(id=4444),
    )

    await cog.on_interaction(interaction)

    assert captured_events == [
        (
            1111,
            "on_slash_command",
            {
                "command_name": "temp_role",
                "options": [{"name": "user", "value": "2222"}],
                "invoking_user_id": 3333,
                "channel_id": 4444,
            },
        )
    ]


async def test_member_events_dispatch_expected_payloads(monkeypatch) -> None:
    """
    成員加入與離開事件應轉成設計文件定義的事件名稱。
    """
    captured_events: list[tuple[int, str, dict]] = []

    async def fake_dispatch_event(guild_id: int, event_type: str, event_payload: dict) -> None:
        captured_events.append((guild_id, event_type, event_payload))

    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())
    member = SimpleNamespace(
        id=2222,
        guild=SimpleNamespace(id=1111),
        joined_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    )

    await cog.on_member_join(member)
    await cog.on_member_remove(member)

    assert captured_events[0][1] == "on_member_join"
    assert captured_events[1][1] == "on_member_leave"
    assert captured_events[0][2]["user_id"] == 2222


async def test_raw_message_edit_and_delete_use_message_cache(monkeypatch) -> None:
    """
    raw edit/delete 應從 message_cache 補 old_content、author_id 與 content。
    """
    captured_events: list[tuple[int, str, dict]] = []

    async def fake_dispatch_event(guild_id: int, event_type: str, event_payload: dict) -> None:
        captured_events.append((guild_id, event_type, event_payload))

    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())
    message_cache.cache_message(1111, 4444, 3333, 2222, "old content")

    await cog.on_raw_message_edit(
        SimpleNamespace(
            guild_id=1111,
            channel_id=4444,
            message_id=3333,
            data={"content": "new content", "edited_timestamp": "2026-01-01T00:00:00+00:00"},
        )
    )
    await cog.on_raw_message_delete(SimpleNamespace(guild_id=1111, channel_id=4444, message_id=3333))

    assert captured_events[0][1] == "on_message_edit"
    assert captured_events[0][2]["old_content"] == "old content"
    assert captured_events[0][2]["new_content"] == "new content"
    assert captured_events[0][2]["edited_at"] == "2026-01-01T00:00:00+00:00"
    assert captured_events[1][1] == "on_message_delete"
    assert captured_events[1][2]["content"] == "new content"
    assert message_cache.get_cached_message(4444, 3333) is None
    message_cache.purge_guild(1111)


async def test_on_guild_remove_purges_message_cache(monkeypatch) -> None:
    """
    bot 被移出伺服器時應清理該伺服器所有訊息快取，也要清外掛安裝與 storage。
    """

    async def fake_delete_all_installations_for_guild(guild_id: int) -> list[str]:
        return []

    async def fake_delete_all_storage_for_guild(guild_id: int) -> None:
        return None

    cleared_guild_ids: list[int] = []

    def fake_clear_guild_usage(guild_id: int) -> None:
        cleared_guild_ids.append(guild_id)

    monkeypatch.setattr(
        listeners.repository, "delete_all_installations_for_guild", fake_delete_all_installations_for_guild
    )
    monkeypatch.setattr(
        listeners.plugin_storage_repository, "delete_all_storage_for_guild", fake_delete_all_storage_for_guild
    )
    monkeypatch.setattr(listeners.quota, "clear_guild_usage", fake_clear_guild_usage)

    cog = listeners.PluginPlatformListeners(FakeBot())
    message_cache.cache_message(1111, 4444, 3333, 2222, "content")

    await cog.on_guild_remove(SimpleNamespace(id=1111))

    assert message_cache.get_cached_message(4444, 3333) is None
    assert cleared_guild_ids == [1111]


async def test_on_guild_remove_deletes_installations_and_storage(tmp_path, monkeypatch) -> None:
    """
    確認 on_guild_remove 真的呼叫 repository/plugin_storage_repository 清掉這個
    伺服器的安裝、排程任務、KV 儲存，其他伺服器的資料不受影響。
    """
    from core import database, plugin_storage_repository, repository

    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test_guild_remove.db"))
    await database.init_db()
    try:
        manifest_json = json.dumps({"name": "x", "event_hooks": []})
        await repository.submit_plugin_version(
            plugin_id="p",
            author_id=1,
            name="p",
            version="1.0.0",
            manifest_json=manifest_json,
            source_code="function on_message(payload) end",
            capability_api_version=1,
        )
        await repository.create_installation(1111, "p", "1.0.0", ["storage"])
        await repository.create_installation(2222, "p", "1.0.0", ["storage"])
        await plugin_storage_repository.storage_set(1111, "p", "key", "value")
        await plugin_storage_repository.storage_set(2222, "p", "key", "value")

        cog = listeners.PluginPlatformListeners(FakeBot())
        await cog.on_guild_remove(SimpleNamespace(id=1111))

        assert await repository.get_installation(1111, "p") is None
        assert await plugin_storage_repository.storage_get(1111, "p", "key") is None
        # 另一個伺服器的資料不受影響
        assert await repository.get_installation(2222, "p") is not None
        assert await plugin_storage_repository.storage_get(2222, "p", "key") == "value"
    finally:
        await database.close_db()


async def test_voice_state_update_dispatches_channel_ids(monkeypatch) -> None:
    """
    語音狀態變更應轉發 before/after channel id。
    """
    captured_events: list[tuple[int, str, dict, str | None]] = []

    async def fake_dispatch_event(guild_id: int, event_type: str, event_payload: dict) -> None:
        captured_events.append((guild_id, event_type, event_payload))

    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())
    member = SimpleNamespace(id=2222, guild=SimpleNamespace(id=1111))
    before = SimpleNamespace(channel=SimpleNamespace(id=3333))
    after = SimpleNamespace(channel=SimpleNamespace(id=4444))

    await cog.on_voice_state_update(member, before, after)

    assert captured_events == [
        (
            1111,
            "on_voice_state_update",
            {"user_id": 2222, "before_channel_id": 3333, "after_channel_id": 4444},
        )
    ]


async def test_consume_due_scheduled_tasks_deletes_and_updates(monkeypatch) -> None:
    """
    到期任務應轉發成 on_scheduled_task，單次任務刪除，週期任務更新下一次 run_at。
    """
    deleted_task_ids: list[str] = []
    updated_tasks: list[tuple[str, str]] = []
    captured_events: list[tuple[int, str, dict]] = []

    async def fake_get_due_scheduled_tasks(now_iso: str) -> list[dict]:
        return [
            {
                "task_id": "single_task",
                "guild_id": 1111,
                "plugin_id": "temp_role_punishment",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload_json": '{"task_name":"single_restore","payload":{"kind":"single"}}',
                "recurring_interval_seconds": None,
                "manifest_json": '{"event_hooks":["on_scheduled_task"]}',
            },
            {
                "task_id": "recurring_task",
                "guild_id": 1111,
                "plugin_id": "temp_role_punishment",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload_json": '{"task_name":"recurring_restore","payload":{"kind":"recurring"}}',
                "recurring_interval_seconds": 60,
                "manifest_json": '{"event_hooks":["on_scheduled_task"]}',
            },
        ]

    async def fake_delete_scheduled_task(task_id: str) -> None:
        deleted_task_ids.append(task_id)

    async def fake_update_scheduled_task_run_at(task_id: str, run_at: str) -> bool:
        updated_tasks.append((task_id, run_at))
        return True

    async def fake_dispatch_event(
        guild_id: int, event_type: str, event_payload: dict, target_plugin_id: str | None = None
    ) -> bool:
        captured_events.append((guild_id, event_type, event_payload, target_plugin_id))
        return True

    monkeypatch.setattr(listeners.repository, "get_due_scheduled_tasks", fake_get_due_scheduled_tasks)
    monkeypatch.setattr(listeners.repository, "delete_scheduled_task", fake_delete_scheduled_task)
    monkeypatch.setattr(listeners.repository, "update_scheduled_task_run_at", fake_update_scheduled_task_run_at)
    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())

    await cog.consume_due_scheduled_tasks()

    assert [event[1] for event in captured_events] == ["on_scheduled_task", "on_scheduled_task"]
    assert captured_events[0][2] == {"task_name": "single_restore", "payload": {"kind": "single"}}
    assert captured_events[1][2] == {"task_name": "recurring_restore", "payload": {"kind": "recurring"}}
    assert [event[3] for event in captured_events] == ["temp_role_punishment", "temp_role_punishment"]
    assert deleted_task_ids == ["single_task"]
    assert updated_tasks == [("recurring_task", "2026-01-01T00:01:00+00:00")]


async def test_consume_due_scheduled_tasks_keeps_task_when_dispatch_fails(monkeypatch) -> None:
    """
    排程任務沒有成功分派時，不應刪除或更新 run_at，避免失敗後任務直接消失。
    """
    deleted_task_ids: list[str] = []
    updated_tasks: list[tuple[str, str]] = []

    async def fake_get_due_scheduled_tasks(now_iso: str) -> list[dict]:
        return [
            {
                "task_id": "single_task",
                "guild_id": 1111,
                "plugin_id": "temp_role_punishment",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload_json": '{"task_name":"single_restore","payload":{"kind":"single"}}',
                "recurring_interval_seconds": None,
                "manifest_json": '{"event_hooks":["on_scheduled_task"]}',
            }
        ]

    async def fake_delete_scheduled_task(task_id: str) -> None:
        deleted_task_ids.append(task_id)

    async def fake_update_scheduled_task_run_at(task_id: str, run_at: str) -> bool:
        updated_tasks.append((task_id, run_at))
        return True

    async def fake_dispatch_event(
        guild_id: int,
        event_type: str,
        event_payload: dict,
        target_plugin_id: str | None = None,
    ) -> bool:
        return False

    monkeypatch.setattr(listeners.repository, "get_due_scheduled_tasks", fake_get_due_scheduled_tasks)
    monkeypatch.setattr(listeners.repository, "delete_scheduled_task", fake_delete_scheduled_task)
    monkeypatch.setattr(listeners.repository, "update_scheduled_task_run_at", fake_update_scheduled_task_run_at)
    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())

    await cog.consume_due_scheduled_tasks()

    assert deleted_task_ids == []
    assert updated_tasks == []


async def test_consume_due_scheduled_tasks_deletes_task_without_hook(monkeypatch) -> None:
    """
    若目前版本已不再訂閱 on_scheduled_task，舊排程應清除，避免永久重試。
    """
    deleted_task_ids: list[str] = []
    dispatch_called = False

    async def fake_get_due_scheduled_tasks(now_iso: str) -> list[dict]:
        return [
            {
                "task_id": "orphan_task",
                "guild_id": 1111,
                "plugin_id": "temp_role_punishment",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload_json": '{"task_name":"single_restore","payload":{}}',
                "recurring_interval_seconds": None,
                "manifest_json": '{"event_hooks":["on_message"]}',
            }
        ]

    async def fake_delete_scheduled_task(task_id: str) -> None:
        deleted_task_ids.append(task_id)

    async def fake_dispatch_event(
        guild_id: int,
        event_type: str,
        event_payload: dict,
        target_plugin_id: str | None = None,
    ) -> bool:
        nonlocal dispatch_called
        dispatch_called = True
        return True

    monkeypatch.setattr(listeners.repository, "get_due_scheduled_tasks", fake_get_due_scheduled_tasks)
    monkeypatch.setattr(listeners.repository, "delete_scheduled_task", fake_delete_scheduled_task)
    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())

    await cog.consume_due_scheduled_tasks()

    assert deleted_task_ids == ["orphan_task"]
    assert dispatch_called is False


async def test_consume_due_scheduled_tasks_limits_concurrency(monkeypatch) -> None:
    """
    排程消費可併發處理，但同時執行數不得超過設定上限。
    """
    active_count = 0
    max_active_count = 0

    async def fake_get_due_scheduled_tasks(now_iso: str) -> list[dict]:
        return [
            {
                "task_id": f"task_{index}",
                "guild_id": 1111,
                "plugin_id": "temp_role_punishment",
                "run_at": "2026-01-01T00:00:00+00:00",
                "payload_json": '{"task_name":"restore","payload":{}}',
                "recurring_interval_seconds": None,
                "manifest_json": '{"event_hooks":["on_scheduled_task"]}',
            }
            for index in range(5)
        ]

    async def fake_dispatch_event(
        guild_id: int,
        event_type: str,
        event_payload: dict,
        target_plugin_id: str | None = None,
    ) -> bool:
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        await asyncio.sleep(0)
        active_count -= 1
        return True

    async def fake_delete_scheduled_task(task_id: str) -> None:
        return None

    monkeypatch.setattr(listeners, "MAX_SCHEDULED_TASK_DISPATCH_CONCURRENCY", 2)
    monkeypatch.setattr(listeners.repository, "get_due_scheduled_tasks", fake_get_due_scheduled_tasks)
    monkeypatch.setattr(listeners.repository, "delete_scheduled_task", fake_delete_scheduled_task)
    monkeypatch.setattr(listeners, "dispatch_event", fake_dispatch_event)

    cog = listeners.PluginPlatformListeners(FakeBot())

    await cog.consume_due_scheduled_tasks()

    assert max_active_count == 2


def test_format_guild_notification_banned_with_reason() -> None:
    text = listeners._format_guild_notification(
        "plugin_banned", {"plugin_id": "temp_role_punishment", "reason": "abuse"}
    )

    assert "temp_role_punishment" in text
    assert "永久封鎖" in text
    assert "abuse" in text


def test_format_guild_notification_banned_without_reason() -> None:
    text = listeners._format_guild_notification("plugin_banned", {"plugin_id": "temp_role_punishment"})

    assert "未提供原因" in text


def test_format_guild_notification_suspended() -> None:
    text = listeners._format_guild_notification("plugin_suspended", {"plugin_id": "temp_role_punishment"})

    assert "temp_role_punishment" in text
    assert "停權" in text


def test_format_guild_notification_unknown_type_has_fallback() -> None:
    text = listeners._format_guild_notification("something_else", {"plugin_id": "temp_role_punishment"})

    assert "something_else" in text


class FakeBotWithGuilds(FakeBot):
    """
    測試用 Bot，額外提供 get_guild()，供伺服器通知消費測試模擬找不到/找得到伺服器。
    """

    def __init__(self, guilds: dict[int, object]) -> None:
        self._guilds = guilds

    def get_guild(self, guild_id: int) -> object | None:
        return self._guilds.get(guild_id)


class FakeSystemChannel:
    """
    測試用系統頻道，記錄送出的訊息內容。
    """

    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.should_raise = False

    async def send(self, content: str) -> None:
        if self.should_raise:
            raise RuntimeError("discord API 暫時失敗")
        self.sent_messages.append(content)


async def test_consume_pending_guild_notifications_sends_and_marks_sent(monkeypatch) -> None:
    """
    找得到伺服器與 system_channel 時應真的送出格式化訊息，並標記通知已送出。
    """
    marked_sent: list[int] = []
    channel = FakeSystemChannel()
    guild = SimpleNamespace(system_channel=channel)

    async def fake_get_pending_guild_notifications(limit: int = 100) -> list[dict]:
        return [
            {
                "notification_id": 1,
                "guild_id": 1111,
                "notification_type": "plugin_suspended",
                "payload_json": '{"plugin_id": "temp_role_punishment"}',
            }
        ]

    async def fake_mark_guild_notification_sent(notification_id: int) -> bool:
        marked_sent.append(notification_id)
        return True

    monkeypatch.setattr(listeners.repository, "get_pending_guild_notifications", fake_get_pending_guild_notifications)
    monkeypatch.setattr(listeners.repository, "mark_guild_notification_sent", fake_mark_guild_notification_sent)

    cog = listeners.PluginPlatformListeners(FakeBotWithGuilds({1111: guild}))

    await cog.consume_pending_guild_notifications()

    assert len(channel.sent_messages) == 1
    assert "temp_role_punishment" in channel.sent_messages[0]
    assert marked_sent == [1]


async def test_consume_pending_guild_notifications_marks_sent_when_guild_not_found(monkeypatch) -> None:
    """
    bot 已經不在該伺服器（找不到 guild）時，不應嘗試送訊息，但仍要標記已送出，
    避免這筆通知卡在待送佇列裡永遠重試。
    """
    marked_sent: list[int] = []

    async def fake_get_pending_guild_notifications(limit: int = 100) -> list[dict]:
        return [
            {
                "notification_id": 1,
                "guild_id": 9999,
                "notification_type": "plugin_suspended",
                "payload_json": '{"plugin_id": "temp_role_punishment"}',
            }
        ]

    async def fake_mark_guild_notification_sent(notification_id: int) -> bool:
        marked_sent.append(notification_id)
        return True

    monkeypatch.setattr(listeners.repository, "get_pending_guild_notifications", fake_get_pending_guild_notifications)
    monkeypatch.setattr(listeners.repository, "mark_guild_notification_sent", fake_mark_guild_notification_sent)

    cog = listeners.PluginPlatformListeners(FakeBotWithGuilds({}))

    await cog.consume_pending_guild_notifications()

    assert marked_sent == [1]


async def test_consume_pending_guild_notifications_marks_sent_when_no_system_channel(monkeypatch) -> None:
    """
    伺服器存在但沒有系統頻道時，同樣不送訊息但標記已送出，理由跟找不到伺服器一致。
    """
    marked_sent: list[int] = []
    guild = SimpleNamespace(system_channel=None)

    async def fake_get_pending_guild_notifications(limit: int = 100) -> list[dict]:
        return [
            {
                "notification_id": 1,
                "guild_id": 1111,
                "notification_type": "plugin_banned",
                "payload_json": '{"plugin_id": "temp_role_punishment", "reason": "abuse"}',
            }
        ]

    async def fake_mark_guild_notification_sent(notification_id: int) -> bool:
        marked_sent.append(notification_id)
        return True

    monkeypatch.setattr(listeners.repository, "get_pending_guild_notifications", fake_get_pending_guild_notifications)
    monkeypatch.setattr(listeners.repository, "mark_guild_notification_sent", fake_mark_guild_notification_sent)

    cog = listeners.PluginPlatformListeners(FakeBotWithGuilds({1111: guild}))

    await cog.consume_pending_guild_notifications()

    assert marked_sent == [1]


async def test_consume_pending_guild_notifications_does_not_mark_sent_when_send_fails(monkeypatch) -> None:
    """
    Discord API 送訊息失敗時不該標記已送出，讓下一輪 loop 有機會重試，
    也不該讓這筆失敗擋住同一批裡的其他通知。
    """
    marked_sent: list[int] = []
    failing_channel = FakeSystemChannel()
    failing_channel.should_raise = True
    working_channel = FakeSystemChannel()
    failing_guild = SimpleNamespace(system_channel=failing_channel)
    working_guild = SimpleNamespace(system_channel=working_channel)

    async def fake_get_pending_guild_notifications(limit: int = 100) -> list[dict]:
        return [
            {
                "notification_id": 1,
                "guild_id": 1111,
                "notification_type": "plugin_suspended",
                "payload_json": '{"plugin_id": "plugin_a"}',
            },
            {
                "notification_id": 2,
                "guild_id": 2222,
                "notification_type": "plugin_suspended",
                "payload_json": '{"plugin_id": "plugin_b"}',
            },
        ]

    async def fake_mark_guild_notification_sent(notification_id: int) -> bool:
        marked_sent.append(notification_id)
        return True

    monkeypatch.setattr(listeners.repository, "get_pending_guild_notifications", fake_get_pending_guild_notifications)
    monkeypatch.setattr(listeners.repository, "mark_guild_notification_sent", fake_mark_guild_notification_sent)

    cog = listeners.PluginPlatformListeners(FakeBotWithGuilds({1111: failing_guild, 2222: working_guild}))

    await cog.consume_pending_guild_notifications()

    assert marked_sent == [2]
    assert working_channel.sent_messages == ["外掛 plugin_b 已被平台停權並自動解除安裝。"]
