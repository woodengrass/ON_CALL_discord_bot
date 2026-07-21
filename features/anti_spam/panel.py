import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS
from core.ui_navigation import BackCallback

ALLOWED_CHANNEL_IDS_KEY = "allowed_channel_ids"


class AntiSpamAllowedChannelSelect(discord.ui.ChannelSelect):
    """
    防洗版允許頻道選擇器，用於設定不受洗版偵測約束的文字頻道。
    """

    def __init__(self, guild_id: int, parent_view: "AntiSpamToggleView") -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(
            placeholder=i18n.get_text("ui.spam_allowed_channel_select", guild_id),
            min_values=1,
            max_values=25,
            channel_types=[discord.ChannelType.text],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel_ids = [str(channel.id) for channel in self.values]
        await GuildSettings.set_module_config(
            self.guild_id,
            "anti_spam",
            ALLOWED_CHANNEL_IDS_KEY,
            channel_ids,
        )
        self.parent_view.update_buttons()
        await interaction.response.edit_message(
            content=None,
            embed=self.parent_view.get_embed(interaction.guild),
            view=self.parent_view,
        )
        await interaction.followup.send(
            i18n.get_text("messages.spam_allowed_channels_updated", self.guild_id, count=len(channel_ids)),
            ephemeral=True,
        )


class AntiSpamAllowedChannelView(discord.ui.View):
    """
    防洗版允許頻道管理視圖，提供頻道選擇、清空與返回主面板。
    """

    def __init__(self, guild_id: int, parent_view: "AntiSpamToggleView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.parent_view = parent_view
        self.add_item(AntiSpamAllowedChannelSelect(guild_id, parent_view))
        self._add_clear_button()
        self._add_back_button()

    def _add_clear_button(self) -> None:
        clear_button = discord.ui.Button(
            label=i18n.get_text("ui.spam_allowed_channel_clear", self.guild_id),
            style=discord.ButtonStyle.danger,
        )
        clear_button.callback = self.clear_allowed_channels
        self.add_item(clear_button)

    def _add_back_button(self) -> None:
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", self.guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_dashboard
        self.add_item(back_button)

    async def clear_allowed_channels(self, interaction: discord.Interaction) -> None:
        """
        清空目前伺服器的防洗版允許頻道設定。
        """
        await GuildSettings.set_module_config(self.guild_id, "anti_spam", ALLOWED_CHANNEL_IDS_KEY, [])
        self.parent_view.update_buttons()
        await interaction.response.edit_message(
            content=None,
            embed=self.parent_view.get_embed(interaction.guild),
            view=self.parent_view,
        )
        await interaction.followup.send(
            i18n.get_text("messages.spam_allowed_channels_cleared", self.guild_id),
            ephemeral=True,
        )

    async def back_to_dashboard(self, interaction: discord.Interaction) -> None:
        """
        返回防洗版主設定面板。
        """
        await interaction.response.edit_message(
            content=None,
            embed=self.parent_view.get_embed(interaction.guild),
            view=self.parent_view,
        )


class AntiSpamToggleView(discord.ui.View):
    """
    防洗版功能的開關儀表板，可切換偵測開關並管理允許頻道。
    """

    def __init__(self, guild_id: int, on_back: BackCallback) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.on_back = on_back
        self.update_buttons()

    def update_buttons(self) -> None:
        """
        依目前設定重建切換按鈕與允許頻道管理入口。
        """
        self.clear_items()
        config = GuildSettings.get_module_config(self.guild_id, "anti_spam")
        master_enabled = config.get("enabled", True)
        multi_channel_enabled = config.get("enable_multi_channel", True)
        same_channel_enabled = config.get("enable_same_channel", True)

        self.add_item(self._create_toggle_button("master", master_enabled))
        self.add_item(self._create_toggle_button("multi", multi_channel_enabled))
        self.add_item(self._create_toggle_button("single", same_channel_enabled))
        self._add_allowed_channel_button()
        self._add_back_button()

    def get_allowed_channel_ids(self) -> list[str]:
        """
        取得目前設定的防洗版允許頻道 ID。

        Returns:
            list[str]，已設定的頻道 ID 字串
        """
        config = GuildSettings.get_module_config(self.guild_id, "anti_spam")
        allowed_channel_ids = config.get(ALLOWED_CHANNEL_IDS_KEY, [])
        return [str(channel_id) for channel_id in allowed_channel_ids]

    def get_embed(self, guild: discord.Guild | None = None) -> discord.Embed:
        """
        建立防洗版設定面板 Embed，包含目前允許頻道狀態。

        Args:
            guild: 伺服器物件，用於解析頻道名稱

        Returns:
            discord.Embed，防洗版設定面板內容
        """
        allowed_text = self._format_allowed_channels(guild)
        description = (
            i18n.get_text("ui.spam_dashboard", self.guild_id)
            + "\n\n"
            + i18n.get_text("ui.spam_allowed_channel_current", self.guild_id, channels=allowed_text)
        )
        return discord.Embed(description=description, color=discord.Color.blue())

    def get_allowed_channel_embed(self, guild: discord.Guild | None = None) -> discord.Embed:
        """
        建立允許頻道管理面板 Embed。

        Args:
            guild: 伺服器物件，用於解析頻道名稱

        Returns:
            discord.Embed，允許頻道管理說明
        """
        allowed_text = self._format_allowed_channels(guild)
        description = i18n.get_text("ui.spam_allowed_channel_prompt", self.guild_id, channels=allowed_text)
        return discord.Embed(description=description, color=discord.Color.blue())

    def _format_allowed_channels(self, guild: discord.Guild | None) -> str:
        """
        將允許頻道 ID 轉成可讀的頻道列表文字。

        Args:
            guild: 伺服器物件，用於查詢頻道

        Returns:
            頻道列表文字；若未設定則回傳多語系空狀態文字
        """
        allowed_channel_ids = self.get_allowed_channel_ids()
        if not allowed_channel_ids:
            return i18n.get_text("ui.spam_allowed_channel_none", self.guild_id)

        channel_lines = []
        for channel_id in allowed_channel_ids:
            channel = guild.get_channel(int(channel_id)) if guild else None
            channel_lines.append(channel.mention if channel else f"`{channel_id}`")
        return "\n".join(channel_lines)

    def _create_toggle_button(self, config_key: str, current_state: bool) -> discord.ui.Button:
        """
        建立單一切換按鈕。

        Args:
            config_key: 對應的開關識別字串（master/multi/single）
            current_state: 目前的開關狀態

        Returns:
            設定好樣式與回呼的按鈕元件
        """
        style = discord.ButtonStyle.success if current_state else discord.ButtonStyle.danger
        state_key = "ui.state_on" if current_state else "ui.state_off"
        state_text = i18n.get_text(state_key, self.guild_id)

        if config_key == "master":
            label_key = "ui.spam_master"
        elif config_key == "multi":
            label_key = "ui.spam_multi"
        else:
            label_key = "ui.spam_single"

        label = f"{i18n.get_text(label_key, self.guild_id)}: {state_text}"
        button = discord.ui.Button(label=label, style=style, custom_id=config_key)

        async def callback(interaction: discord.Interaction) -> None:
            new_state = not current_state
            db_key_map = {"master": "enabled", "multi": "enable_multi_channel", "single": "enable_same_channel"}
            await GuildSettings.set_module_config(self.guild_id, "anti_spam", db_key_map[config_key], new_state)
            self.update_buttons()
            await interaction.response.edit_message(embed=self.get_embed(interaction.guild), view=self)
            feature = i18n.get_text(label_key, self.guild_id)
            status = i18n.get_text("ui.state_on" if new_state else "ui.state_off", self.guild_id)
            await interaction.followup.send(
                i18n.get_text("messages.setting_status_updated", self.guild_id, feature=feature, status=status),
                ephemeral=True,
            )

        button.callback = callback
        return button

    def _add_allowed_channel_button(self) -> None:
        allowed_button = discord.ui.Button(
            label=i18n.get_text("ui.spam_allowed_channel_manage", self.guild_id),
            style=discord.ButtonStyle.primary,
        )
        allowed_button.callback = self.manage_allowed_channels
        self.add_item(allowed_button)

    def _add_back_button(self) -> None:
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", self.guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def manage_allowed_channels(self, interaction: discord.Interaction) -> None:
        """
        開啟防洗版允許頻道管理面板。
        """
        await interaction.response.edit_message(
            content=None,
            embed=self.get_allowed_channel_embed(interaction.guild),
            view=AntiSpamAllowedChannelView(self.guild_id, self),
        )

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await self.on_back(interaction)
