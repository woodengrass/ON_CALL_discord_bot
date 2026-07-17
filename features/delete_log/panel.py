import logging
from typing import TYPE_CHECKING

import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS

if TYPE_CHECKING:
    from hubs.server_setting.panel import ServerSettingView

logger = logging.getLogger(__name__)


class DeleteLogToggleView(discord.ui.View):
    """
    刪除訊息日誌功能的開啟/關閉切換按鈕。
    """

    def __init__(self, guild_id: int, parent_view: "ServerSettingView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.parent_view = parent_view
        current_state = GuildSettings.get_module_config(guild_id, "delete_log").get("enabled", False)
        state_text = i18n.get_text("ui.state_on" if current_state else "ui.state_off", guild_id)
        toggle_button = discord.ui.Button(
            label=f"{i18n.get_text('ui.toggle_delete_log', guild_id)}: {state_text}",
            style=discord.ButtonStyle.success if current_state else discord.ButtonStyle.danger,
        )
        toggle_button.callback = self.toggle_log
        self.add_item(toggle_button)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def toggle_log(self, interaction: discord.Interaction) -> None:
        current_state = GuildSettings.get_module_config(self.guild_id, "delete_log").get("enabled", False)
        new_state = not current_state
        await GuildSettings.set_module_config(self.guild_id, "delete_log", "enabled", new_state)
        status = i18n.get_text("messages.status_enabled" if new_state else "messages.status_disabled", self.guild_id)
        toggle_message = i18n.get_text("messages.toggle_log", self.guild_id, status=status)
        await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)
        await interaction.followup.send(toggle_message, ephemeral=True)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)


