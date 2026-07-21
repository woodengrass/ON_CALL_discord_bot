import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS
from features.anti_raid.panel import AntiRaidToggleView
from features.anti_spam.panel import AntiSpamToggleView
from features.honeypot.panel import HoneypotSettingView
from features.link_checker.panel import LinkCheckerToggleView


# ==============================================================================
#  工具函式庫 (Logic Helpers)
# ==============================================================================
class _WhitelistUtils:
    """
    白名單設定面板使用的顯示與選項建構函式。
    """

    @staticmethod
    def get_whitelist_embed(guild_id: int) -> discord.Embed:
        """
        取得目前白名單成員列表的 Embed。

        Args:
            guild_id: 伺服器 ID

        Returns:
            顯示白名單成員（或空白名單提示）的 Embed
        """
        whitelist = GuildSettings.get_whitelist(guild_id)
        if not whitelist:
            return discord.Embed(description=i18n.get_text("messages.whitelist_empty", guild_id),
                                 color=discord.Color.orange())

        content = "\n".join([f"<@{user_id}> (`{user_id}`)" for user_id in whitelist])
        if len(content) > 4000:
            content = content[:4000] + "\n" + i18n.get_text("messages.text_truncated", guild_id)
        return discord.Embed(title=i18n.get_text("labels.whitelist", guild_id), description=content,
                             color=discord.Color.gold())

    @staticmethod
    def get_whitelist_remove_options(guild_id: int, guild: discord.Guild) -> list[discord.SelectOption]:
        """
        建立白名單移除選單所需的選項列表。

        Args:
            guild_id: 伺服器 ID
            guild: 伺服器物件，用於查詢成員暱稱

        Returns:
            list of SelectOption，最多 25 筆
        """
        whitelist_ids = GuildSettings.get_whitelist(guild_id)
        if not whitelist_ids:
            return []
        options = []
        for user_id_str in whitelist_ids:
            try:
                user_id = int(user_id_str)
                member = guild.get_member(user_id)
                label = member.display_name if member else i18n.get_text(
                    "messages.label_user_left", guild_id, user_id=user_id
                )
                options.append(
                    discord.SelectOption(
                        label=label[:100],
                        value=str(user_id),
                        description=i18n.get_text("labels.identifier", guild_id, identifier=user_id),
                    ))
            except ValueError:
                continue
        return options[:25]


# ==============================================================================
#  功能組件 (Components)
# ==============================================================================

class WhitelistAddSelect(discord.ui.UserSelect):
    """
    白名單新增使用者選擇器。
    """

    def __init__(self, guild_id: int, parent_view: discord.ui.View) -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(placeholder=i18n.get_text("ui.select_user_add", guild_id), min_values=1, max_values=10)

    async def callback(self, interaction: discord.Interaction) -> None:
        result_lines = []
        for user in self.values:
            success = await GuildSettings.add_whitelist(self.guild_id, user.id)
            message_key = "messages.whitelist_added" if success else "messages.whitelist_exists"
            result_lines.append(i18n.get_text(message_key, self.guild_id, user=user.mention))
        embed = discord.Embed(
            title=i18n.get_text("messages.title_whitelist_add_result", self.guild_id),
            description="\n".join(result_lines),
            color=discord.Color.green()
        )
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)
        await interaction.followup.send(embed=embed, ephemeral=True)


class WhitelistRemoveSelect(discord.ui.Select):
    """
    白名單移除使用者選擇器。
    """

    def __init__(self, guild_id: int, options: list[discord.SelectOption], parent_view: discord.ui.View) -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(placeholder=i18n.get_text("ui.select_user_remove", guild_id), min_values=1,
                         max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        result_lines = []
        for value in self.values:
            user_id = int(value)
            success = await GuildSettings.remove_whitelist(self.guild_id, user_id)
            message_key = "messages.whitelist_removed" if success else "messages.whitelist_not_in"
            result_lines.append(i18n.get_text(message_key, self.guild_id, user=f"<@{user_id}>"))
        embed = discord.Embed(
            title=i18n.get_text("messages.title_whitelist_remove_result", self.guild_id),
            description="\n".join(result_lines),
            color=discord.Color.red()
        )
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)
        await interaction.followup.send(embed=embed, ephemeral=True)


# --- 防洗版開關儀表板 ---
# --- 連結檢查開關視圖 ---
# --- 防炸群開關視圖 ---
class AntiFraudComponentView(discord.ui.View):
    """
    顯示單一反詐騙設定元件並提供返回子選單按鈕。
    """

    def __init__(self, guild_id: int, item: discord.ui.Item, parent_view: discord.ui.View) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.parent_view = parent_view
        self.add_item(item)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_menu
        self.add_item(back_button)

    async def back_to_menu(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)

class WhitelistMenuSelect(discord.ui.Select):
    """
    白名單子選單，提供查看、新增與移除白名單成員的選項。
    """

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        options = [
            discord.SelectOption(label=i18n.get_text("ui.view_whitelist", guild_id), value="view"),
            discord.SelectOption(label=i18n.get_text("ui.add_whitelist", guild_id), value="add"),
            discord.SelectOption(label=i18n.get_text("ui.remove_whitelist", guild_id), value="remove"),
            discord.SelectOption(label=i18n.get_text("ui.back", guild_id), value="back")
        ]
        super().__init__(placeholder=i18n.get_text("ui.whitelist_menu", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_value = self.values[0]
        if selected_value == "back":
            await interaction.response.edit_message(content=None, embed=None, view=AntiFraudView(self.guild_id))
        elif selected_value == "view":
            embed = _WhitelistUtils.get_whitelist_embed(self.guild_id)
            await interaction.response.edit_message(content=None, embed=embed, view=WhitelistSettingView(self.guild_id))
        elif selected_value == "add":
            parent_view = WhitelistSettingView(self.guild_id)
            view = AntiFraudComponentView(
                self.guild_id, WhitelistAddSelect(self.guild_id, parent_view), parent_view
            )
            await interaction.response.edit_message(
                content=i18n.get_text("ui.select_user_add", self.guild_id), embed=None, view=view
            )
        elif selected_value == "remove":
            options = _WhitelistUtils.get_whitelist_remove_options(self.guild_id, interaction.guild)
            if not options:
                await interaction.response.send_message(
                    i18n.get_text("messages.whitelist_empty", self.guild_id), ephemeral=True)
                return
            parent_view = WhitelistSettingView(self.guild_id)
            view = AntiFraudComponentView(
                self.guild_id, WhitelistRemoveSelect(self.guild_id, options, parent_view), parent_view
            )
            await interaction.response.edit_message(
                content=i18n.get_text("ui.select_user_remove", self.guild_id), embed=None, view=view
            )


# ==============================================================================
#  主視圖 (Main Views)
# ==============================================================================

class WhitelistSettingView(discord.ui.View):
    """
    白名單設定的子選單容器 View。
    """

    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.add_item(WhitelistMenuSelect(guild_id))


class AntiFraudSelect(discord.ui.Select):
    """
    反詐騙設定主選單，作為蜜罐、防洗版、白名單與連結檢查的入口。
    """

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        options = [
            discord.SelectOption(label=i18n.get_text("ui.manage_honeypot", guild_id), value="honeypot",
                                 description=i18n.get_text("ui.desc_manage_honeypot", guild_id)),
            discord.SelectOption(label=i18n.get_text("ui.toggle_anti_spam", guild_id), value="spam"),
            discord.SelectOption(label=i18n.get_text("ui.manage_whitelist", guild_id), value="whitelist",
                                 description=i18n.get_text("ui.desc_manage_whitelist", guild_id)),
            discord.SelectOption(label=i18n.get_text("ui.link_checker", guild_id), value="link_checker",
                                 description=i18n.get_text("ui.desc_link_checker", guild_id)),
            discord.SelectOption(label=i18n.get_text("ui.anti_raid", guild_id), value="anti_raid",
                                 description=i18n.get_text("ui.desc_anti_raid", guild_id)),
            discord.SelectOption(label=i18n.get_text("ui.manage_verification", guild_id), value="verification",
                                 description=i18n.get_text("ui.desc_manage_verification", guild_id)),
        ]
        super().__init__(placeholder=i18n.get_text("ui.placeholder", guild_id), options=options)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        """返回反詐騙主面板。"""
        await interaction.response.edit_message(content=None, embed=None, view=AntiFraudView(self.guild_id))

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_value = self.values[0]
        if selected_value == "honeypot":
            await interaction.response.edit_message(
                content=i18n.get_text("messages.honeypot_menu_title", self.guild_id),
                view=HoneypotSettingView(self.guild_id, self.back_to_main))
        elif selected_value == "spam":
            view = AntiSpamToggleView(self.guild_id, self.back_to_main)
            await interaction.response.edit_message(content=None, embed=view.get_embed(interaction.guild), view=view)
        elif selected_value == "whitelist":
            await interaction.response.edit_message(
                content=i18n.get_text("messages.whitelist_menu_title", self.guild_id),
                view=WhitelistSettingView(self.guild_id))
        elif selected_value == "link_checker":
            view = LinkCheckerToggleView(self.guild_id, self.back_to_main)
            embed = discord.Embed(description=i18n.get_text("ui.link_checker_dashboard", self.guild_id),
                                  color=discord.Color.blue())
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        elif selected_value == "anti_raid":
            view = AntiRaidToggleView(self.guild_id, self.back_to_main)
            embed = discord.Embed(description=i18n.get_text("ui.anti_raid_dashboard", self.guild_id),
                                  color=discord.Color.blue())
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        elif selected_value == "verification":
            from features.verification.panel import VerificationSettingView
            view = VerificationSettingView(self.guild_id, self.back_to_main)
            await interaction.response.edit_message(content=None, embed=view.get_embed(), view=view)


class AntiFraudView(discord.ui.View):
    """
    反詐騙設定面板的最上層 View。
    """

    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.add_item(AntiFraudSelect(guild_id))





