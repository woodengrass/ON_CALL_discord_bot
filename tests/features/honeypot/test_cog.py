from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from features.honeypot import cog as honeypot_cog
from features.honeypot.cog import HoneypotMonitor


def _make_message(guild_id: int, channel_id: int, user_id: int, content: str) -> SimpleNamespace:
    """
    建立蜜罐測試所需的最小訊息物件，機器人權限設定為只能刪除訊息、不執行封禁，
    以便單獨驗證違規內容的追蹤與比對邏輯。

    Args:
        guild_id: 伺服器 ID
        channel_id: 訊息所在頻道 ID
        user_id: 發送訊息的使用者 ID
        content: 訊息內容

    Returns:
        SimpleNamespace，模擬 Discord 訊息
    """
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_messages=True, ban_members=False),
        top_role=SimpleNamespace(position=10),
    )
    guild = SimpleNamespace(
        id=guild_id,
        me=bot_member,
        owner=SimpleNamespace(id=999999),
        ban=AsyncMock(),
    )
    author = SimpleNamespace(id=user_id, bot=False, top_role=SimpleNamespace(position=1))
    return SimpleNamespace(
        content=content,
        author=author,
        guild=guild,
        channel=SimpleNamespace(id=channel_id),
        delete=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_same_user_across_guilds_does_not_share_honeypot_record(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    保護文件記載的 (guild_id, user_id) key 設計：違規內容追蹤字典必須以
    (guild_id, user_id) 為 key，而不能只用 user_id。若只用 user_id 為 key，
    使用者在伺服器 A 觸發蜜罐後，於伺服器 B 發送內容相同的一般訊息會被誤判為
    重複違規——這是修過的真實 bug，不得改回。
    """
    honeypot_channel_by_guild = {100: "900", 500: "950"}

    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"channel_id": honeypot_channel_by_guild[guild_id]},
        get_log_channel=lambda guild_id: None,
        get_whitelist=lambda guild_id: [],
    )
    monkeypatch.setattr(honeypot_cog, "GuildSettings", settings)

    cog = object.__new__(HoneypotMonitor)
    cog.bot = SimpleNamespace(get_channel=lambda channel_id: None)
    cog.user_messages = {}

    guild_a, guild_b, user_id, content = 100, 500, 300, "secret"

    # 使用者在伺服器 A 的蜜罐頻道發言，觸發蜜罐並記錄違規內容。
    message_a_honeypot = _make_message(guild_a, 900, user_id, content)
    await cog.on_message(message_a_honeypot)

    assert content in cog.user_messages[(guild_a, user_id)]
    message_a_honeypot.delete.assert_awaited_once()

    # 同一使用者於伺服器 B 的一般頻道發送相同內容，應視為全新訊息，不應被
    # 誤判為重複違規（因為伺服器 B 尚無任何記錄）。
    message_b_normal = _make_message(guild_b, 951, user_id, content)
    await cog.on_message(message_b_normal)

    assert (guild_b, user_id) not in cog.user_messages
    message_b_normal.delete.assert_not_awaited()

    # 同一使用者於伺服器 A 的一般頻道再次發送相同內容，才應被判定為重複違規。
    message_a_normal = _make_message(guild_a, 901, user_id, content)
    await cog.on_message(message_a_normal)

    message_a_normal.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_user_same_guild_repeat_content_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    驗證同一伺服器內，使用者於蜜罐頻道留下紀錄後，在一般頻道重複相同內容
    確實會被判定為重複違規並刪除，作為上一則跨伺服器隔離測試的對照組。
    """
    settings = SimpleNamespace(
        get_module_config=lambda guild_id, module_name: {"channel_id": "900"},
        get_log_channel=lambda guild_id: None,
        get_whitelist=lambda guild_id: [],
    )
    monkeypatch.setattr(honeypot_cog, "GuildSettings", settings)

    cog = object.__new__(HoneypotMonitor)
    cog.bot = SimpleNamespace(get_channel=lambda channel_id: None)
    cog.user_messages = {}

    guild_id, user_id, content = 100, 300, "secret"

    await cog.on_message(_make_message(guild_id, 900, user_id, content))
    assert content in cog.user_messages[(guild_id, user_id)]

    repeat_message = _make_message(guild_id, 901, user_id, content)
    await cog.on_message(repeat_message)

    repeat_message.delete.assert_awaited_once()
