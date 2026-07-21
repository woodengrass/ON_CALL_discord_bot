import logging

import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n

logger = logging.getLogger(__name__)


class PeopleCountChannelSelect(discord.ui.ChannelSelect):
    """
    人數統計頻道選擇器，選擇後立即將頻道命名為目前伺服器人數。
    """

    def __init__(self, guild_id: int, parent_view: discord.ui.View) -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(
            placeholder=i18n.get_text("ui.select_channel_count", guild_id),
            channel_types=[discord.ChannelType.text, discord.ChannelType.voice],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_channel = self.values[0]
        channel = interaction.guild.get_channel(selected_channel.id)
        if not channel:
            error_message = i18n.get_text("messages.error_channel_not_found", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        member_count = interaction.guild.member_count
        await GuildSettings.set_module_config(
            self.guild_id, "people_counting", "channel_id", str(channel.id)
        )
        await GuildSettings.set_module_config(
            self.guild_id, "people_counting", "last_count", member_count
        )

        try:
            new_name = i18n.get_text(
                "messages.count_channel_name",
                self.guild_id,
                count=member_count,
            )
            await channel.edit(name=new_name)

            success_message = i18n.get_text(
                "messages.set_count_success",
                self.guild_id,
                channel=channel.mention,
                count=member_count,
            )
            embed = discord.Embed(
                title=i18n.get_text("messages.title_setting_success", self.guild_id),
                description=success_message,
                color=discord.Color.green(),
            )
            await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f"更新人數統計頻道失敗：{e}", exc_info=True)
            error_message = i18n.get_text("messages.error_setting_failed", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)


