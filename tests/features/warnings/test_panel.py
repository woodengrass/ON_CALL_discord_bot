from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.ui_constants import PANEL_TIMEOUT_SECONDS
from features.warnings import cog, panel
from features.warnings.wizard_state import WarningDraftStore, WarningPageState


@pytest.fixture
def draft_store() -> Iterator[WarningDraftStore]:
    """提供每個測試獨立的提醒草稿 store。"""
    store = WarningDraftStore()
    yield store
    store.clear()


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


def _make_parent_view() -> MagicMock:
    """建立設定精靈所需的主面板 mock。"""
    return MagicMock(page=0)


@pytest.mark.asyncio
async def test_sessions_are_isolated_across_guilds_for_same_user(draft_store: WarningDraftStore) -> None:
    """同一使用者在不同伺服器開啟流程時應使用獨立暫存資料。"""
    first_key = panel._create_warning_session_key(100, 200)
    second_key = panel._create_warning_session_key(101, 200)
    parent_view = _make_parent_view()

    panel.WarningContentModal(100, 200, first_key, parent_view, draft_store)
    draft_store.get(first_key)["content"] = "first"
    panel.WarningContentModal(101, 200, second_key, parent_view, draft_store)
    draft_store.get(second_key)["content"] = "second"

    assert first_key != second_key
    assert draft_store.get(first_key)["content"] == "first"
    assert draft_store.get(second_key)["content"] == "second"


def test_parallel_sessions_are_isolated_for_same_guild_and_user(draft_store: WarningDraftStore) -> None:
    """同一伺服器與使用者的平行流程應由隨機 nonce 分隔。"""
    first_key = panel._create_warning_session_key(100, 200)
    second_key = panel._create_warning_session_key(100, 200)
    draft_store.create(first_key, {"channel_id": 300})
    draft_store.create(second_key, {"channel_id": 301})

    assert first_key != second_key
    assert first_key[:2] == second_key[:2] == (100, 200)
    assert draft_store.get(first_key)["channel_id"] == 300
    assert draft_store.get(second_key)["channel_id"] == 301


@pytest.mark.asyncio
async def test_add_flows_create_independent_sessions(draft_store: WarningDraftStore) -> None:
    """同一入口連續開啟新增流程時，每個 modal 應取得不同 session key。"""
    action_select = panel.WarningActionSelect(100, _make_parent_view(), draft_store)
    action_select._values = ["add"]
    first_interaction = _make_interaction(100, 200)
    second_interaction = _make_interaction(100, 200)

    await action_select.callback(first_interaction)
    await action_select.callback(second_interaction)

    first_modal = first_interaction.response.send_modal.await_args.args[0]
    second_modal = second_interaction.response.send_modal.await_args.args[0]
    assert first_modal.session_key != second_modal.session_key
    assert draft_store.get(first_modal.session_key) is not None
    assert draft_store.get(second_modal.session_key) is not None


@pytest.mark.asyncio
async def test_edit_flows_create_independent_sessions(
    monkeypatch: pytest.MonkeyPatch,
    draft_store: WarningDraftStore,
) -> None:
    """同一提醒的平行編輯流程應各自取得新的 session nonce。"""
    warning_data = {
        "guild_id": 100,
        "content": {"desc": "existing"},
        "schedule": {"type": "daily", "time": "12:00", "days": []},
    }
    monkeypatch.setattr(panel.WarningStore, "data", {"warning-1": warning_data})
    parent_view = _make_parent_view()
    first_select = panel.WarningListSelect(100, "edit", parent_view, 0, draft_store)
    second_select = panel.WarningListSelect(100, "edit", parent_view, 0, draft_store)
    first_select._values = ["warning-1"]
    second_select._values = ["warning-1"]
    first_interaction = _make_interaction(100, 200)
    second_interaction = _make_interaction(100, 200)

    await first_select.callback(first_interaction)
    await second_select.callback(second_interaction)

    first_modal = first_interaction.response.send_modal.await_args.args[0]
    second_modal = second_interaction.response.send_modal.await_args.args[0]
    assert first_modal.session_key != second_modal.session_key
    assert draft_store.get(first_modal.session_key)["id"] == "warning-1"
    assert draft_store.get(second_modal.session_key)["id"] == "warning-1"


@pytest.mark.asyncio
async def test_back_navigation_preserves_session_key(draft_store: WarningDraftStore) -> None:
    """排程步驟返回目標步驟時應傳遞原本的完整 session key。"""
    session_key = panel._create_warning_session_key(100, 200)
    draft_store.create(session_key)
    view = panel.WarningScheduleView(100, 200, session_key, _make_parent_view(), draft_store)
    interaction = _make_interaction(100, 200)

    await view.back_callback(interaction)

    target_view = interaction.response.edit_message.await_args.kwargs["view"]
    assert target_view.session_key == session_key
    assert view.is_finished()


@pytest.mark.asyncio
async def test_frequency_selection_stops_previous_view(draft_store: WarningDraftStore) -> None:
    """切換到排程時間 modal 時應停止舊 view，避免逾時誤刪草稿。"""
    session_key = panel._create_warning_session_key(100, 200)
    draft_store.create(session_key)
    view = panel.WarningScheduleView(100, 200, session_key, _make_parent_view(), draft_store)
    interaction = _make_interaction(100, 200)
    interaction.data = {"values": ["daily"]}

    await view.frequency_callback(interaction)

    assert view.is_finished()
    assert draft_store.get(session_key) is not None


@pytest.mark.asyncio
async def test_expired_draft_is_removed_and_rejected() -> None:
    """過期草稿不得繼續操作，且讀取時應立即清除。"""
    current_time = [0.0]
    draft_store = WarningDraftStore(clock=lambda: current_time[0])
    session_key = panel._create_warning_session_key(100, 200)
    draft_store.create(session_key)
    current_time[0] = PANEL_TIMEOUT_SECONDS
    interaction = _make_interaction(100, 200)

    wip_data = await panel._get_wip_warning(interaction, session_key, draft_store)

    assert wip_data is None
    assert draft_store.get(session_key) is None
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_view_timeout_removes_only_its_session(draft_store: WarningDraftStore) -> None:
    """目前精靈 view 逾時時只應清除自己的草稿。"""
    expired_key = panel._create_warning_session_key(100, 200)
    remaining_key = panel._create_warning_session_key(100, 200)
    draft_store.create(expired_key)
    draft_store.create(remaining_key)
    view = panel.WarningTargetView(100, 200, expired_key, _make_parent_view(), draft_store)

    await view.on_timeout()

    assert draft_store.get(expired_key) is None
    assert draft_store.get(remaining_key) is not None


@pytest.mark.asyncio
async def test_cancel_removes_only_its_session(draft_store: WarningDraftStore) -> None:
    """取消流程只應刪除目前 view 持有的完整 session key。"""
    cancelled_key = panel._create_warning_session_key(100, 200)
    remaining_key = panel._create_warning_session_key(100, 200)
    draft_store.create(cancelled_key)
    draft_store.create(remaining_key)
    parent_view = _make_parent_view()
    view = panel.WarningTargetView(100, 200, cancelled_key, parent_view, draft_store)
    interaction = _make_interaction(100, 200)

    await view.cancel_callback(interaction)

    assert draft_store.get(cancelled_key) is None
    assert draft_store.get(remaining_key) is not None
    assert view.is_finished()
    interaction.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_removes_only_its_session(
    monkeypatch: pytest.MonkeyPatch,
    draft_store: WarningDraftStore,
) -> None:
    """儲存成功只應刪除目前 modal 持有的完整 session key。"""
    saved_key = panel._create_warning_session_key(100, 200)
    remaining_key = panel._create_warning_session_key(100, 200)
    draft_store.create(saved_key, {"channel_id": 300, "content": {"desc": "content"}})
    draft_store.create(remaining_key)
    set_warning = AsyncMock()
    monkeypatch.setattr(panel.WarningStore, "set_warning", set_warning)
    parent_view = _make_parent_view()
    modal = panel.WarningTimeModal(100, "daily", 200, saved_key, parent_view, draft_store)
    modal.time_input._value = "12:30"
    interaction = _make_interaction(100, 200)

    await modal.on_submit(interaction)

    assert draft_store.get(saved_key) is None
    assert draft_store.get(remaining_key) is not None
    set_warning.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interaction_guild_id", "interaction_user_id"),
    [(101, 200), (100, 201)],
)
async def test_callback_rejects_mismatched_interaction_identity(
    interaction_guild_id: int,
    interaction_user_id: int,
    draft_store: WarningDraftStore,
) -> None:
    """callback 不得讓其他伺服器或使用者操作既有 session。"""
    session_key = panel._create_warning_session_key(100, 200)
    draft_store.create(session_key)
    view = panel.WarningTargetView(100, 200, session_key, _make_parent_view(), draft_store)
    interaction = _make_interaction(interaction_guild_id, interaction_user_id)

    await view.cancel_callback(interaction)

    assert draft_store.get(session_key) is not None
    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_rejects_missing_session(draft_store: WarningDraftStore) -> None:
    """callback 找不到完整 session key 時應回覆既有的暫存資料錯誤。"""
    session_key = panel._create_warning_session_key(100, 200)
    view = panel.WarningTargetView(100, 200, session_key, _make_parent_view(), draft_store)
    interaction = _make_interaction(100, 200)

    await view.cancel_callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()


def test_cog_unload_clears_draft_store(draft_store: WarningDraftStore) -> None:
    """Cog 卸載時應停止排程並清除未完成草稿。"""
    session_key = panel._create_warning_session_key(100, 200)
    draft_store.create(session_key)
    warning_task = SimpleNamespace(check_warning_task=MagicMock(), draft_store=draft_store)

    cog.WarningTask.cog_unload(warning_task)

    warning_task.check_warning_task.cancel.assert_called_once()
    assert draft_store.get(session_key) is None


@pytest.mark.asyncio
async def test_warning_loop_does_not_clean_drafts(
    monkeypatch: pytest.MonkeyPatch,
    draft_store: WarningDraftStore,
) -> None:
    """提醒排程執行時不得管理 UI 草稿的生命週期。"""
    session_key = panel._create_warning_session_key(100, 200)
    draft_store.create(session_key)
    monkeypatch.setattr(cog.WarningStore, "data", {})
    warning_task = SimpleNamespace(draft_store=draft_store)

    await cog.WarningTask.check_warning_task.coro(warning_task)

    assert draft_store.get(session_key) is not None


@pytest.mark.parametrize(
    ("item_count", "page_size", "page", "expected_page", "total_pages"),
    [(0, 10, 0, 0, 1), (11, 10, -1, 0, 2), (11, 10, 9, 1, 2)],
)
def test_warning_page_state_clamps_page(
    item_count: int,
    page_size: int,
    page: int,
    expected_page: int,
    total_pages: int,
) -> None:
    """分頁狀態應在資料數量變動後維持合法範圍。"""
    page_state = WarningPageState(item_count, page_size, page)

    assert page_state.page == expected_page
    assert page_state.total_pages == total_pages


@pytest.mark.asyncio
async def test_toggle_missing_warning_acknowledges_interaction(
    monkeypatch: pytest.MonkeyPatch,
    draft_store: WarningDraftStore,
) -> None:
    """選取後提醒遭刪除時，toggle callback 仍應回覆操作失敗。"""
    monkeypatch.setattr(panel.WarningStore, "data", {"warning-1": {"guild_id": 100}})
    toggle_warning = AsyncMock(return_value=None)
    monkeypatch.setattr(panel.WarningStore, "toggle_warning", toggle_warning)
    parent_view = MagicMock(page=0)
    warning_select = panel.WarningListSelect(100, "toggle", parent_view, 0, draft_store)
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
    [("1,2,7", "weekly", [1, 2, 7]), (" 1, 15,31 ", "monthly", [1, 15, 31])],
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
