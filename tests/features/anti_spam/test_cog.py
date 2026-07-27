from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from features.anti_spam import cog as anti_spam_cog
from features.anti_spam.cog import AntiSpam


def _make_message(channel_id: int) -> SimpleNamespace:
    """
    建立防洗版測試所需的最小訊息物件。

    Args:
        channel_id: 訊息所在頻道 ID

    Returns:
        SimpleNamespace，模擬 Discord 訊息
    """
    return SimpleNamespace(
        content="repeat",
        author=SimpleNamespace(id=200, bot=False),
        guild=SimpleNamespace(id=100),
        channel=SimpleNamespace(id=channel_id),
    )


@pytest.mark.asyncio
async def test_allowed_channel_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    允許頻道中的訊息不應進入防洗版歷史紀錄。
    """
    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"enabled": True, "allowed_channel_ids": ["300"]},
        get_whitelist=lambda guild_id: [],
    )
    monkeypatch.setattr(anti_spam_cog, "GuildSettings", settings)
    cog = object.__new__(AntiSpam)
    cog.message_history = {}

    await cog.on_message(_make_message(300))

    assert cog.message_history == {}


@pytest.mark.asyncio
async def test_non_allowed_channel_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    非允許頻道仍應依原本流程被防洗版歷史紀錄追蹤。
    """
    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"enabled": True, "allowed_channel_ids": ["300"]},
        get_whitelist=lambda guild_id: [],
    )
    monkeypatch.setattr(anti_spam_cog, "GuildSettings", settings)
    cog = object.__new__(AntiSpam)
    cog.message_history = {}

    await cog.on_message(_make_message(301))

    assert (100, 200) in cog.message_history
    assert len(cog.message_history[(100, 200)]) == 1


@pytest.mark.asyncio
async def test_whitelisted_user_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    共用白名單中的使用者不應進入防洗版歷史紀錄。
    """
    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"enabled": True},
        get_whitelist=lambda guild_id: ["200"],
    )
    monkeypatch.setattr(anti_spam_cog, "GuildSettings", settings)
    cog = object.__new__(AntiSpam)
    cog.message_history = {}

    await cog.on_message(_make_message(301))

    assert cog.message_history == {}


def _make_repeat_message(guild_id: int, channel_id: int, user_id: int) -> SimpleNamespace:
    """
    建立同頻道重複洗版測試所需的最小訊息物件。

    Args:
        guild_id: 伺服器 ID
        channel_id: 訊息所在頻道 ID
        user_id: 發送訊息的使用者 ID

    Returns:
        SimpleNamespace，模擬 Discord 訊息
    """
    return SimpleNamespace(
        content="repeat",
        author=SimpleNamespace(id=user_id, bot=False),
        guild=SimpleNamespace(id=guild_id),
        channel=SimpleNamespace(id=channel_id),
    )


@pytest.mark.asyncio
async def test_same_user_across_guilds_does_not_share_spam_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    保護文件記載的 (guild_id, user_id) key 設計：訊息追蹤字典必須以
    (guild_id, user_id) 為 key，而不能只用 user_id。若只用 user_id 為 key，
    同一使用者在不同伺服器的重複訊息會被誤加總，導致在伺服器 B 才發第一則
    訊息就被誤判為洗版而觸發處置——這是修過的真實 bug，不得改回。
    """
    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"enabled": True},
        get_whitelist=lambda guild_id: [],
    )
    monkeypatch.setattr(anti_spam_cog, "GuildSettings", settings)

    cog = object.__new__(AntiSpam)
    cog.message_history = {}
    cog.take_action = AsyncMock()

    guild_a, guild_b, user_id, channel_id = 100, 500, 200, 301
    threshold = anti_spam_cog.SPAM_SAME_CHANNEL_THRESHOLD

    # 使用者在伺服器 A 累積到門檻減一，尚未觸發。
    for _ in range(threshold - 1):
        await cog.on_message(_make_repeat_message(guild_a, channel_id, user_id))

    assert len(cog.message_history[(guild_a, user_id)]) == threshold - 1
    cog.take_action.assert_not_called()

    # 同一使用者於伺服器 B 首次發言，應獨立計數，不應繼承伺服器 A 的累積量。
    await cog.on_message(_make_repeat_message(guild_b, channel_id, user_id))

    assert len(cog.message_history[(guild_b, user_id)]) == 1
    cog.take_action.assert_not_called()

    # 伺服器 A 累積達到門檻，才應觸發洗版處置。
    await cog.on_message(_make_repeat_message(guild_a, channel_id, user_id))

    cog.take_action.assert_awaited_once()
    assert len(cog.message_history[(guild_a, user_id)]) == 0
