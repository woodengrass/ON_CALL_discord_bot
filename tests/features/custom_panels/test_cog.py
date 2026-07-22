from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from features.custom_panels.cog import _resolve_guild_channel


@pytest.mark.asyncio
async def test_resolve_guild_channel_uses_cached_channel() -> None:
    """快取存在頻道時不應額外呼叫 Discord API。"""
    cached_channel = object()
    guild = SimpleNamespace(get_channel=MagicMock(return_value=cached_channel), fetch_channel=AsyncMock())

    channel = await _resolve_guild_channel(guild, 100)

    assert channel is cached_channel
    guild.get_channel.assert_called_once_with(100)
    guild.fetch_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_guild_channel_fetches_cache_miss() -> None:
    """快取未命中時應向 Discord API 查詢相同頻道 ID。"""
    fetched_channel = object()
    guild = SimpleNamespace(
        get_channel=MagicMock(return_value=None),
        fetch_channel=AsyncMock(return_value=fetched_channel),
    )

    channel = await _resolve_guild_channel(guild, 100)

    assert channel is fetched_channel
    guild.fetch_channel.assert_awaited_once_with(100)
