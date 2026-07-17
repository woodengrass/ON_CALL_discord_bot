import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS


# ==============================================================================
#  組件 1: 歡迎訊息填寫表單 (Modal)
# ==============================================================================
class WelcomeModal(discord.ui.Modal):
    """
    歡迎訊息內容編輯表單，包含標題、內文與底部文字。
    """

    def __init__(self, guild_id: int, parent_view: "WelcomeSettingView") -> None:
        title_text = i18n.get_text("ui.modal_welcome_title", guild_id)
        super().__init__(title=title_text[:45])
        self.guild_id = guild_id
        self.parent_view = parent_view  # 用於提交後刷新面板

        # 讀取現有設定
        config = GuildSettings.get_module_config(guild_id, "welcome")

        self.title_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_welcome_title", guild_id)[:45],
            placeholder=i18n.get_text("ui.welcome_title", guild_id),
            default=config.get("title", ""),
            required=False, max_length=256
        )
        self.add_item(self.title_input)

        self.desc_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_welcome_desc", guild_id)[:45],
            style=discord.TextStyle.paragraph,
            placeholder=i18n.get_text("ui.welcome_desc", guild_id),
            default=config.get("desc", config.get("message", "")),
            required=True, max_length=2000
        )
        self.add_item(self.desc_input)

        self.footer_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_welcome_footer", guild_id)[:45],
            placeholder=i18n.get_text("ui.welcome_footer", guild_id),
            default=config.get("footer", ""),
            required=False, max_length=1024
        )
        self.add_item(self.footer_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 儲存歡迎訊息設定
        await GuildSettings.set_module_config(self.guild_id, "welcome", "title", self.title_input.value.strip())
        await GuildSettings.set_module_config(self.guild_id, "welcome", "desc", self.desc_input.value.strip())
        await GuildSettings.set_module_config(self.guild_id, "welcome", "footer", self.footer_input.value.strip())

        # 提交後直接原地編輯原始訊息，刷新 Embed 預覽
        await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)
        await interaction.followup.send(
            i18n.get_text("messages.set_welcome_success", self.guild_id), ephemeral=True
        )


# ==============================================================================
#  組件 2: 頻道選擇器 (Channel Select)
# ==============================================================================
class WelcomeChannelSelect(discord.ui.ChannelSelect):
    """
    歡迎訊息發送頻道選擇器。
    """

    def __init__(self, guild_id: int, parent_view: "WelcomeSettingView") -> None:
        super().__init__(
            placeholder=i18n.get_text("ui.select_welcome_channel", guild_id),
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1
        )
        self.guild_id = guild_id
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        channel_id = self.values[0].id
        await GuildSettings.set_module_config(self.guild_id, "welcome", "channel_id", str(channel_id))

        # 選擇完成後回到主面板並刷新 Embed
        await interaction.response.edit_message(content=None, embed=self.parent_view.get_embed(), view=self.parent_view)
        await interaction.followup.send(
            i18n.get_text("messages.set_welcome_success", self.guild_id), ephemeral=True
        )


class WelcomeChannelSettingView(discord.ui.View):
    """
    顯示歡迎頻道選擇器並提供返回主面板按鈕。
    """

    def __init__(self, guild_id: int, parent_view: "WelcomeSettingView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.parent_view = parent_view
        self.add_item(WelcomeChannelSelect(guild_id, parent_view))
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )


# ==============================================================================
#  主面板視圖 (Welcome Setting View)
# ==============================================================================
class WelcomeSettingView(discord.ui.View):
    """
    歡迎訊息設定面板的主視圖，提供頻道設定、頭像切換與內文編輯選單。
    """

    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.add_item(self._create_select())
        self.add_item(self._create_avatar_toggle())

    def _create_select(self) -> discord.ui.Select:
        """
        建立主選單選項。

        Returns:
            設定好選項與回呼的 Select 元件
        """
        options = [
            discord.SelectOption(label=i18n.get_text("ui.set_welcome_channel", self.guild_id), value="set_channel"),
            discord.SelectOption(label=i18n.get_text("ui.edit_welcome_msg", self.guild_id), value="edit_msg"),
        ]

        select = discord.ui.Select(placeholder=i18n.get_text("ui.welcome_panel_placeholder", self.guild_id),
                                   options=options)
        select.callback = self._handle_select
        return select

    def _create_avatar_toggle(self) -> discord.ui.Button:
        """建立顯示頭像的單一切換按鈕。"""
        config = GuildSettings.get_module_config(self.guild_id, "welcome")
        current_state = config.get("avatar", True)
        state_text = i18n.get_text("ui.state_on" if current_state else "ui.state_off", self.guild_id)
        button = discord.ui.Button(
            label=f"{i18n.get_text('ui.toggle_welcome_avatar', self.guild_id)}: {state_text}",
            style=discord.ButtonStyle.success if current_state else discord.ButtonStyle.danger,
        )
        button.callback = self._toggle_avatar
        return button

    async def _toggle_avatar(self, interaction: discord.Interaction) -> None:
        """切換歡迎訊息是否顯示使用者頭像。"""
        config = GuildSettings.get_module_config(self.guild_id, "welcome")
        new_state = not config.get("avatar", True)
        await GuildSettings.set_module_config(self.guild_id, "welcome", "avatar", new_state)
        updated_view = WelcomeSettingView(self.guild_id)
        await interaction.response.edit_message(embed=updated_view.get_embed(), view=updated_view)
        await interaction.followup.send(
            i18n.get_text("messages.set_welcome_success", self.guild_id), ephemeral=True
        )

    def get_embed(self) -> discord.Embed:
        """
        依目前設定產生歡迎系統的預覽狀態 Embed。

        Returns:
            顯示目前發送頻道與頭像顯示狀態的 Embed
        """
        embed = discord.Embed(
            title=i18n.get_text("messages.welcome_panel_title", self.guild_id),
            description=i18n.get_text("messages.welcome_panel_desc", self.guild_id),
            color=discord.Color.brand_green()
        )

        config = GuildSettings.get_module_config(self.guild_id, "welcome")
        channel_id = config.get("channel_id")
        channel_text = f"<#{channel_id}>" if channel_id else i18n.get_text("messages.channel_not_set", self.guild_id)

        is_avatar_on = config.get("avatar", True)
        avatar_text = i18n.get_text("messages.status_enabled" if is_avatar_on else "messages.status_disabled",
                                    self.guild_id)

        embed.add_field(name=i18n.get_text("labels.send_channel", self.guild_id), value=channel_text, inline=True)
        embed.add_field(name=i18n.get_text("labels.avatar_status", self.guild_id), value=avatar_text, inline=True)

        return embed

    async def _handle_select(self, interaction: discord.Interaction) -> None:
        selected_value = interaction.data["values"][0]

        if selected_value == "set_channel":
            # 切換為頻道選擇模式
            view = WelcomeChannelSettingView(self.guild_id, self)
            await interaction.response.edit_message(
                content=i18n.get_text("ui.welcome_channel_prompt", self.guild_id), embed=None, view=view)

        elif selected_value == "edit_msg":
            await interaction.response.send_modal(WelcomeModal(self.guild_id, self))

