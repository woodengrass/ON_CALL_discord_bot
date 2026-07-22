from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from features.verification import panel


def _make_interaction() -> SimpleNamespace:
    """建立設定選擇 callback 測試所需的最小互動物件。"""
    return SimpleNamespace(
        response=SimpleNamespace(edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_key", "placeholder_key"),
    [
        ("restricted_role_id", "ui.verification_select_restricted_role"),
        ("verified_role_id", "ui.verification_select_verified_role"),
        ("review_role_id", "ui.verification_select_review_role"),
    ],
)
async def test_role_config_select_updates_requested_config_key(
    monkeypatch: pytest.MonkeyPatch,
    config_key: str,
    placeholder_key: str,
) -> None:
    """身分組選擇器應只更新建構子指定的驗證設定欄位。"""
    parent_view = MagicMock()
    role_select = panel.VerificationRoleConfigSelect(100, parent_view, config_key, placeholder_key)
    role_select._values = [SimpleNamespace(id=200)]
    set_module_config = AsyncMock()
    monkeypatch.setattr(panel.GuildSettings, "set_module_config", set_module_config)
    interaction = _make_interaction()

    await role_select.callback(interaction)

    set_module_config.assert_awaited_once_with(100, "verification", config_key, 200)
    interaction.response.edit_message.assert_awaited_once_with(
        content=None, embed=parent_view.get_embed(), view=parent_view
    )
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_channel_config_select_keeps_channel_specific_config_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """頻道選擇器應維持獨立元件型別並更新驗證頻道設定。"""
    parent_view = MagicMock()
    channel_select = panel.VerificationChannelConfigSelect(100, parent_view)
    channel_select._values = [SimpleNamespace(id=300)]
    set_module_config = AsyncMock()
    monkeypatch.setattr(panel.GuildSettings, "set_module_config", set_module_config)
    interaction = _make_interaction()

    await channel_select.callback(interaction)

    set_module_config.assert_awaited_once_with(100, "verification", "verify_channel_id", 300)
    interaction.response.edit_message.assert_awaited_once_with(
        content=None, embed=parent_view.get_embed(), view=parent_view
    )
