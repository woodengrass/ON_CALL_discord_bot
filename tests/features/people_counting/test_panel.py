from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from features.people_counting import panel


def _make_interaction(channel: SimpleNamespace) -> SimpleNamespace:
    """建立人數統計頻道選擇 callback 所需的最小互動物件。"""
    return SimpleNamespace(
        guild=SimpleNamespace(
            get_channel=MagicMock(return_value=channel),
            member_count=42,
        ),
        response=SimpleNamespace(
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_channel_rename_failure_keeps_retry_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首次改名失敗時應保留頻道設定，且不可更新最後成功人數。"""
    channel = SimpleNamespace(
        id=300,
        mention="<#300>",
        edit=AsyncMock(side_effect=RuntimeError("rename failed")),
    )
    interaction = _make_interaction(channel)
    parent_view = MagicMock()
    channel_select = panel.PeopleCountChannelSelect(100, parent_view)
    channel_select._values = [SimpleNamespace(id=300)]
    set_module_config = AsyncMock()
    monkeypatch.setattr(panel.GuildSettings, "set_module_config", set_module_config)

    await channel_select.callback(interaction)

    set_module_config.assert_awaited_once_with(100, "people_counting", "channel_id", "300")
    interaction.response.send_message.assert_awaited_once()
    interaction.response.edit_message.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_rename_success_updates_last_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功改名後才應記錄最後成功更新的人數。"""
    channel = SimpleNamespace(
        id=300,
        mention="<#300>",
        edit=AsyncMock(),
    )
    interaction = _make_interaction(channel)
    parent_view = MagicMock()
    channel_select = panel.PeopleCountChannelSelect(100, parent_view)
    channel_select._values = [SimpleNamespace(id=300)]
    set_module_config = AsyncMock()
    monkeypatch.setattr(panel.GuildSettings, "set_module_config", set_module_config)

    await channel_select.callback(interaction)

    assert set_module_config.await_args_list[0].args == (100, "people_counting", "channel_id", "300")
    assert set_module_config.await_args_list[1].args == (100, "people_counting", "last_count", 42)
    interaction.response.edit_message.assert_awaited_once()
    interaction.followup.send.assert_awaited_once()
