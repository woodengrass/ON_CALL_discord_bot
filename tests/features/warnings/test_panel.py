import time
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from features.warnings import panel


@pytest.fixture(autouse=True)
def clear_wip_warnings() -> Iterator[None]:
    """在每個測試前後清除提醒設定流程的暫存資料。"""
    panel.WIP_WARNINGS.clear()
    yield
    panel.WIP_WARNINGS.clear()


def _make_interaction(guild_id: int, user_id: int) -> SimpleNamespace:
    """
    建立設定精靈 callback 測試所需的最小互動物件。

    Args:
        guild_id: 互動所在伺服器 ID
        user_id: 互動使用者 ID

    Returns:
        SimpleNamespace，模擬 Discord 互動
    """
    return SimpleNamespace(
        guild_id=guild_id,
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            defer=AsyncMock(),
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_sessions_are_isolated_across_guilds_for_same_user() -> None:
    """同一使用者在不同伺服器開啟流程時應使用獨立暫存資料。"""
    first_key = panel._create_warning_session_key(100, 200)
    second_key = panel._create_warning_session_key(101, 200)
    parent_view = MagicMock()

    panel.WarningContentModal(100, 200, first_key, parent_view)
    panel.WIP_WARNINGS[first_key]["content"] = "first"
    panel.WarningContentModal(101, 200, second_key, parent_view)
    panel.WIP_WARNINGS[second_key]["content"] = "second"

    assert first_key != second_key
    assert panel.WIP_WARNINGS[first_key]["content"] == "first"
    assert panel.WIP_WARNINGS[second_key]["content"] == "second"


def test_parallel_sessions_are_isolated_for_same_guild_and_user() -> None:
    """同一伺服器與使用者的平行流程應由隨機 nonce 分隔。"""
    first_key = panel._create_warning_session_key(100, 200)
    second_key = panel._create_warning_session_key(100, 200)

    panel.WIP_WARNINGS[first_key] = {"channel_id": 300}
    panel.WIP_WARNINGS[second_key] = {"channel_id": 301}

    assert first_key != second_key
    assert first_key[:2] == second_key[:2] == (100, 200)
    assert panel.WIP_WARNINGS[first_key]["channel_id"] == 300
    assert panel.WIP_WARNINGS[second_key]["channel_id"] == 301


@pytest.mark.asyncio
async def test_add_flows_create_independent_sessions() -> None:
    """同一入口連續開啟新增流程時，每個 modal 應取得不同 session key。"""
    action_select = panel.WarningActionSelect(100, MagicMock())
    action_select._values = ["add"]
    first_interaction = _make_interaction(100, 200)
    second_interaction = _make_interaction(100, 200)

    await action_select.callback(first_interaction)
    await action_select.callback(second_interaction)

    first_modal = first_interaction.response.send_modal.await_args.args[0]
    second_modal = second_interaction.response.send_modal.await_args.args[0]
    assert first_modal.session_key != second_modal.session_key
    assert first_modal.session_key in panel.WIP_WARNINGS
    assert second_modal.session_key in panel.WIP_WARNINGS


@pytest.mark.asyncio
async def test_edit_flows_create_independent_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一提醒的平行編輯流程應各自取得新的 session nonce。"""
    warning_data = {
        "guild_id": 100,
        "content": {"desc": "existing"},
        "schedule": {"type": "daily", "time": "12:00", "days": []},
    }
    monkeypatch.setattr(panel.WarningStore, "data", {"warning-1": warning_data})
    parent_view = MagicMock()
    first_select = panel.WarningListSelect(100, "edit", parent_view, 0)
    second_select = panel.WarningListSelect(100, "edit", parent_view, 0)
    first_select._values = ["warning-1"]
    second_select._values = ["warning-1"]
    first_interaction = _make_interaction(100, 200)
    second_interaction = _make_interaction(100, 200)

    await first_select.callback(first_interaction)
    await second_select.callback(second_interaction)

    first_modal = first_interaction.response.send_modal.await_args.args[0]
    second_modal = second_interaction.response.send_modal.await_args.args[0]
    assert first_modal.session_key != second_modal.session_key
    assert panel.WIP_WARNINGS[first_modal.session_key]["id"] == "warning-1"
    assert panel.WIP_WARNINGS[second_modal.session_key]["id"] == "warning-1"


@pytest.mark.asyncio
async def test_back_navigation_preserves_session_key() -> None:
    """排程步驟返回目標步驟時應傳遞原本的完整 session key。"""
    session_key = panel._create_warning_session_key(100, 200)
    panel.WIP_WARNINGS[session_key] = {"_created_at": time.time()}
    view = panel.WarningScheduleView(100, 200, session_key, MagicMock())
    interaction = _make_interaction(100, 200)

    await view.back_callback(interaction)

    target_view = interaction.response.edit_message.await_args.kwargs["view"]
    assert target_view.session_key == session_key


def test_cleanup_removes_only_stale_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """逾時清理只應移除過期 session，不影響同一使用者的其他流程。"""
    stale_key = panel._create_warning_session_key(100, 200)
    active_key = panel._create_warning_session_key(100, 200)
    now = time.time()
    panel.WIP_WARNINGS[stale_key] = {
        "_created_at": now - panel.WIP_WARNING_TIMEOUT_SECONDS - 1,
    }
    panel.WIP_WARNINGS[active_key] = {"_created_at": now}
    monkeypatch.setattr(panel.time, "time", lambda: now)

    panel.cleanup_stale_wip_warnings()

    assert stale_key not in panel.WIP_WARNINGS
    assert active_key in panel.WIP_WARNINGS


@pytest.mark.asyncio
async def test_cancel_removes_only_its_session() -> None:
    """取消流程只應刪除目前 view 持有的完整 session key。"""
    cancelled_key = panel._create_warning_session_key(100, 200)
    remaining_key = panel._create_warning_session_key(100, 200)
    panel.WIP_WARNINGS[cancelled_key] = {"_created_at": time.time()}
    panel.WIP_WARNINGS[remaining_key] = {"_created_at": time.time()}
    parent_view = MagicMock()
    view = panel.WarningTargetView(100, 200, cancelled_key, parent_view)
    interaction = _make_interaction(100, 200)

    await view.cancel_callback(interaction)

    assert cancelled_key not in panel.WIP_WARNINGS
    assert remaining_key in panel.WIP_WARNINGS
    interaction.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_removes_only_its_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """儲存成功只應刪除目前 modal 持有的完整 session key。"""
    saved_key = panel._create_warning_session_key(100, 200)
    remaining_key = panel._create_warning_session_key(100, 200)
    panel.WIP_WARNINGS[saved_key] = {
        "_created_at": time.time(),
        "channel_id": 300,
        "content": {"desc": "content"},
    }
    panel.WIP_WARNINGS[remaining_key] = {"_created_at": time.time()}
    set_warning = AsyncMock()
    monkeypatch.setattr(panel.WarningStore, "set_warning", set_warning)
    parent_view = MagicMock(page=0)
    modal = panel.WarningTimeModal(100, "daily", 200, saved_key, parent_view)
    modal.time_input._value = "12:30"
    interaction = _make_interaction(100, 200)

    await modal.on_submit(interaction)

    assert saved_key not in panel.WIP_WARNINGS
    assert remaining_key in panel.WIP_WARNINGS
    set_warning.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interaction_guild_id", "interaction_user_id"),
    [(101, 200), (100, 201)],
)
async def test_callback_rejects_mismatched_interaction_identity(
    interaction_guild_id: int,
    interaction_user_id: int,
) -> None:
    """callback 不得讓其他伺服器或使用者操作既有 session。"""
    session_key = panel._create_warning_session_key(100, 200)
    panel.WIP_WARNINGS[session_key] = {"_created_at": time.time()}
    view = panel.WarningTargetView(100, 200, session_key, MagicMock())
    interaction = _make_interaction(interaction_guild_id, interaction_user_id)

    await view.cancel_callback(interaction)

    assert session_key in panel.WIP_WARNINGS
    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_rejects_missing_session() -> None:
    """callback 找不到完整 session key 時應回覆既有的暫存資料錯誤。"""
    session_key = panel._create_warning_session_key(100, 200)
    view = panel.WarningTargetView(100, 200, session_key, MagicMock())
    interaction = _make_interaction(100, 200)

    await view.cancel_callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_toggle_missing_warning_acknowledges_interaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """選取後提醒遭刪除時，toggle callback 仍應回覆操作失敗。"""
    monkeypatch.setattr(panel.WarningStore, "data", {"warning-1": {"guild_id": 100}})
    toggle_warning = AsyncMock(return_value=None)
    monkeypatch.setattr(panel.WarningStore, "toggle_warning", toggle_warning)
    parent_view = MagicMock(page=0)
    warning_select = panel.WarningListSelect(100, "toggle", parent_view, 0)
    warning_select._values = ["warning-1"]
    interaction = _make_interaction(100, 200)

    await warning_select.callback(interaction)

    toggle_warning.assert_awaited_once_with("warning-1")
    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("value", ["00:00", "09:05", "23:59", " 12:30 "])
def test_parse_schedule_time_accepts_valid_24_hour_time(value: str) -> None:
    """合法的 24 小時制時間應通過驗證。"""
    assert panel._parse_schedule_time(value) == value.strip()


@pytest.mark.parametrize(
    "value",
    ["", "9:05", "24:00", "23:60", "12:3", "12-30", "12:30 extra"],
)
def test_parse_schedule_time_rejects_invalid_time(value: str) -> None:
    """格式不完整或超出範圍的時間應被拒絕。"""
    assert panel._parse_schedule_time(value) is None


@pytest.mark.parametrize(
    ("value", "frequency_type", "expected"),
    [
        ("1,2,7", "weekly", [1, 2, 7]),
        (" 1, 15,31 ", "monthly", [1, 15, 31]),
    ],
)
def test_parse_schedule_days_accepts_valid_days(
    value: str,
    frequency_type: str,
    expected: list[int],
) -> None:
    """合法的週與月日期列表應完整轉成整數。"""
    assert panel._parse_schedule_days(value, frequency_type) == expected


@pytest.mark.parametrize(
    ("value", "frequency_type"),
    [
        ("", "weekly"),
        ("1,,2", "weekly"),
        ("1,day", "weekly"),
        ("0,1", "weekly"),
        ("1,8", "weekly"),
        ("1.5,2", "weekly"),
        ("0,1", "monthly"),
        ("31,32", "monthly"),
        ("1,", "monthly"),
        ("1", "daily"),
    ],
)
def test_parse_schedule_days_rejects_malformed_or_out_of_range_days(
    value: str,
    frequency_type: str,
) -> None:
    """空值、畸形 token 與超出頻率範圍的日期皆應被拒絕。"""
    assert panel._parse_schedule_days(value, frequency_type) is None
