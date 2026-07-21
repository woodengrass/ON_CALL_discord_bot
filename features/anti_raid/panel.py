import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS
from core.ui_navigation import BackCallback


class AntiRaidToggleView(discord.ui.View):
    """
    防炸群偵測功能的開關視圖。
    """

    def __init__(self, guild_id: int, on_back: BackCallback) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.on_back = on_back
        self.update_button()

    def update_button(self) -> None:
        """
        依目前設定重建切換按鈕。
        """
        self.clear_items()
        config = GuildSettings.get_module_config(self.guild_id, "anti_raid")
        is_enabled = config.get("enabled", False)

        style = discord.ButtonStyle.success if is_enabled else discord.ButtonStyle.danger
        state_text = i18n.get_text("ui.state_on" if is_enabled else "ui.state_off", self.guild_id)
        label = f"{i18n.get_text('ui.anti_raid_enable', self.guild_id)}: {state_text}"

        button = discord.ui.Button(label=label, style=style)

        async def callback(interaction: discord.Interaction) -> None:
            new_state = not is_enabled
            await GuildSettings.set_module_config(self.guild_id, "anti_raid", "enabled", new_state)
            self.update_button()
            await interaction.response.edit_message(view=self)
            feature = i18n.get_text("ui.anti_raid_enable", self.guild_id)
            status = i18n.get_text("ui.state_on" if new_state else "ui.state_off", self.guild_id)
            await interaction.followup.send(
                i18n.get_text("messages.setting_status_updated", self.guild_id, feature=feature, status=status),
                ephemeral=True,
            )

        button.callback = callback
        self.add_item(button)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", self.guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await self.on_back(interaction)


# ==============================================================================
#  二級選單 (Sub-Menus)
# ==============================================================================


