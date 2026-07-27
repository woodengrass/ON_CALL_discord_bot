import discord
from discord.ext import commands

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS
from core.ui_navigation import BackCallback


def get_banned_log_embed(guild_id: int, bot: commands.Bot) -> discord.Embed:
    """
    取得目前已記錄的蜜罐違規訊息內容。

    Args:
        guild_id: 伺服器 ID
        bot: 機器人實例

    Returns:
        違規訊息列表 Embed
    """
    monitor_cog = bot.get_cog("HoneypotMonitor")
    if monitor_cog is None:
        return discord.Embed(description=i18n.get_text("messages.no_module", guild_id), color=discord.Color.red())
    banned_texts = monitor_cog.get_all_banned_texts(guild_id)
    if not banned_texts:
        return discord.Embed(
            description=i18n.get_text("messages.no_banned_text", guild_id), color=discord.Color.green()
        )
    output = "\n".join(
        f"{index + 1}. {text[:150].replace('`', 'ˋ')}" for index, text in enumerate(banned_texts)
    )
    if len(output) > 1900:
        output = output[:1900] + "\n" + i18n.get_text("messages.text_truncated", guild_id)
    description = i18n.get_text("messages.banned_text_header", guild_id, text=output)
    return discord.Embed(description=description, color=discord.Color.red())


class HoneypotChannelSelect(discord.ui.ChannelSelect):
    """蜜罐頻道選擇器。"""

    def __init__(self, guild_id: int, parent_view: discord.ui.View) -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(
            placeholder=i18n.get_text("ui.select_honeypot_channel", guild_id),
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.guild.get_channel(self.values[0].id)
        if channel is None:
            message = i18n.get_text("messages.error_channel_not_found", self.guild_id)
            await interaction.response.send_message(message, ephemeral=True)
            return
        await GuildSettings.set_module_config(self.guild_id, "honeypot", "channel_id", str(channel.id))
        success_message = i18n.get_text("messages.success_honeypot", self.guild_id, channel=channel.mention)
        embed = discord.Embed(
            title=i18n.get_text("messages.title_setting_success", self.guild_id),
            description=success_message,
            color=discord.Color.green(),
        )
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)
        await interaction.followup.send(embed=embed, ephemeral=True)


class HoneypotComponentView(discord.ui.View):
    """顯示蜜罐頻道選擇元件並提供返回操作。"""

    def __init__(self, guild_id: int, item: discord.ui.Item, parent_view: discord.ui.View) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.parent_view = parent_view
        self.add_item(item)
        button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        button.callback = self.back_to_menu
        self.add_item(button)

    async def back_to_menu(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)


class HoneypotMenuSelect(discord.ui.Select):
    """蜜罐系統設定子選單。"""

    def __init__(self, guild_id: int, on_back: BackCallback) -> None:
        self.guild_id = guild_id
        self.on_back = on_back
        options = [
            discord.SelectOption(label=i18n.get_text("ui.set_honeypot", guild_id), value="set"),
            discord.SelectOption(label=i18n.get_text("ui.view_banned_texts", guild_id), value="logs"),
            discord.SelectOption(label=i18n.get_text("ui.back", guild_id), value="back"),
        ]
        super().__init__(placeholder=i18n.get_text("ui.honeypot_menu", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_value = self.values[0]
        if selected_value == "back":
            await self.on_back(interaction)
        elif selected_value == "set":
            parent_view = HoneypotSettingView(self.guild_id, self.on_back)
            view = HoneypotComponentView(
                self.guild_id, HoneypotChannelSelect(self.guild_id, parent_view), parent_view
            )
            await interaction.response.edit_message(
                content=i18n.get_text("ui.select_honeypot_channel", self.guild_id), embed=None, view=view
            )
        elif selected_value == "logs":
            embed = get_banned_log_embed(self.guild_id, interaction.client)
            await interaction.response.edit_message(
                content=None, embed=embed, view=HoneypotSettingView(self.guild_id, self.on_back)
            )


class HoneypotSettingView(discord.ui.View):
    """蜜罐系統設定子選單容器。"""

    def __init__(self, guild_id: int, on_back: BackCallback) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.add_item(HoneypotMenuSelect(guild_id, on_back))
