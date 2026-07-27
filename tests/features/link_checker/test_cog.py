from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from features.link_checker import cog as link_checker_cog
from features.link_checker.cog import LinkChecker


@pytest.mark.asyncio
async def test_whitelisted_user_is_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    共用白名單中的使用者不應觸發網址或詐騙圖片檢查。
    """
    settings = SimpleNamespace(get_whitelist=lambda guild_id: ["200"])
    monkeypatch.setattr(link_checker_cog, "GuildSettings", settings)

    cog = object.__new__(LinkChecker)
    cog.is_module_enabled = Mock(return_value=True)
    cog.is_qr_code_check_enabled = Mock()
    message = SimpleNamespace(
        author=SimpleNamespace(id=200, bot=False),
        guild=SimpleNamespace(id=100),
    )

    await cog.on_message(message)

    cog.is_qr_code_check_enabled.assert_not_called()
