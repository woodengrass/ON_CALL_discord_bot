import logging

import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS
from core.ui_navigation import BackCallback

logger = logging.getLogger(__name__)


class VerificationRoleConfigSelect(discord.ui.RoleSelect):
    """
    驗證系統身分組設定選擇器。
    """

    def __init__(
        self,
        guild_id: int,
        parent_view: "VerificationSettingView",
        config_key: str,
        placeholder_key: str,
    ) -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        self.config_key = config_key
        super().__init__(
            placeholder=i18n.get_text(placeholder_key, guild_id), min_values=1, max_values=1
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0]
        await GuildSettings.set_module_config(self.guild_id, "verification", self.config_key, role.id)
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )
        await interaction.followup.send(
            i18n.get_text("messages.verification_setting_success", self.guild_id), ephemeral=True
        )


class VerificationChannelConfigSelect(discord.ui.ChannelSelect):
    """
    驗證頻道選擇器（放置「我是人類」按鈕面板的頻道）。
    """

    def __init__(self, guild_id: int, parent_view: "VerificationSettingView") -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        super().__init__(
            placeholder=i18n.get_text("ui.verification_select_channel", guild_id),
            channel_types=[discord.ChannelType.text], min_values=1, max_values=1
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = self.values[0]
        await GuildSettings.set_module_config(self.guild_id, "verification", "verify_channel_id", channel.id)
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )
        await interaction.followup.send(
            i18n.get_text("messages.verification_setting_success", self.guild_id), ephemeral=True
        )


class VerificationSubView(discord.ui.View):
    """
    顯示單一驗證設定元件並提供返回主面板按鈕。
    """

    def __init__(self, guild_id: int, item: discord.ui.Item, parent_view: "VerificationSettingView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.parent_view = parent_view
        self.add_item(item)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=None, embed=self.parent_view.get_embed(), view=self.parent_view)


class VerificationLockdownConfirmView(discord.ui.View):
    """
    啟用驗證系統前的確認視圖，警示這是會影響整個伺服器的操作。
    """

    def __init__(self, guild_id: int, parent_view: "VerificationSettingView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.parent_view = parent_view

        confirm_button = discord.ui.Button(
            label=i18n.get_text("ui.confirm", guild_id), style=discord.ButtonStyle.danger
        )
        confirm_button.callback = self.confirm
        self.add_item(confirm_button)

        cancel_button = discord.ui.Button(
            label=i18n.get_text("ui.cancel", guild_id), style=discord.ButtonStyle.secondary
        )
        cancel_button.callback = self.cancel
        self.add_item(cancel_button)

    async def confirm(self, interaction: discord.Interaction) -> None:
        """
        確認啟用：對現有成員發放已驗證身分組，並鎖定所有頻道的發言權限。

        Args:
            interaction: 觸發確認的互動物件
        """
        from features.verification.service import lockdown_and_grandfather

        await interaction.response.defer()
        await interaction.edit_original_response(
            content=i18n.get_text("messages.verification_lockdown_processing", self.guild_id), embed=None, view=None
        )

        config = GuildSettings.get_module_config(self.guild_id, "verification")
        verified_role = interaction.guild.get_role(int(config["verified_role_id"]))
        restricted_role = interaction.guild.get_role(int(config["restricted_role_id"]))
        verify_channel = interaction.guild.get_channel(int(config["verify_channel_id"]))
        if (
            verified_role is None
            or restricted_role is None
            or not isinstance(verify_channel, discord.TextChannel)
        ):
            updated_view = VerificationSettingView(self.guild_id, self.parent_view.on_back)
            await interaction.edit_original_response(content=None, embed=updated_view.get_embed(), view=updated_view)
            await interaction.followup.send(
                i18n.get_text("messages.verification_lockdown_failed", self.guild_id), ephemeral=True
            )
            return

        honeypot_config = GuildSettings.get_module_config(self.guild_id, "honeypot")
        honeypot_channel_id_str = honeypot_config.get("channel_id")
        honeypot_channel_id = int(honeypot_channel_id_str) if honeypot_channel_id_str else None

        result = await lockdown_and_grandfather(
            interaction.guild, restricted_role, verified_role, honeypot_channel_id
        )

        if not result["success"]:
            updated_view = VerificationSettingView(self.guild_id, self.parent_view.on_back)
            await interaction.edit_original_response(content=None, embed=updated_view.get_embed(), view=updated_view)
            message_key = (
                "messages.verification_lockdown_rollback_failed"
                if result["rollback_failure_count"]
                else "messages.verification_lockdown_failed"
            )
            await interaction.followup.send(i18n.get_text(message_key, self.guild_id), ephemeral=True)
            return

        await GuildSettings.set_module_config(self.guild_id, "verification", "enabled", True)

        updated_view = VerificationSettingView(self.guild_id, self.parent_view.on_back)
        await interaction.edit_original_response(content=None, embed=updated_view.get_embed(), view=updated_view)
        await interaction.followup.send(
            i18n.get_text(
                "messages.verification_lockdown_complete", self.guild_id,
                member_count=result["member_count"],
                channel_count=result["channel_count"],
                skipped_member_count=result["skipped_member_count"],
            ),
            ephemeral=True,
        )

    async def cancel(self, interaction: discord.Interaction) -> None:
        """
        取消啟用，回到設定面板。

        Args:
            interaction: 觸發取消的互動物件
        """
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )
        await interaction.followup.send(
            i18n.get_text("messages.verification_lockdown_cancelled", self.guild_id), ephemeral=True
        )


class VerificationSettingSelect(discord.ui.Select):
    """
    驗證系統設定主選單。
    """

    def __init__(self, guild_id: int, parent_view: "VerificationSettingView") -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        options = [
            discord.SelectOption(
                label=i18n.get_text("ui.verification_restricted_role", guild_id),
                value="restricted_role",
            ),
            discord.SelectOption(label=i18n.get_text("ui.verification_verified_role", guild_id), value="verified_role"),
            discord.SelectOption(label=i18n.get_text("ui.verification_review_role", guild_id), value="review_role"),
            discord.SelectOption(label=i18n.get_text("ui.verification_channel", guild_id), value="verify_channel"),
            discord.SelectOption(label=i18n.get_text("ui.verification_publish", guild_id), value="publish"),
        ]
        super().__init__(placeholder=i18n.get_text("ui.verification_placeholder", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_value = self.values[0]

        if selected_value == "restricted_role":
            view = VerificationSubView(
                self.guild_id,
                VerificationRoleConfigSelect(
                    self.guild_id,
                    self.parent_view,
                    "restricted_role_id",
                    "ui.verification_select_restricted_role",
                ),
                self.parent_view,
            )
            await interaction.response.edit_message(
                content=i18n.get_text("ui.verification_select_restricted_role", self.guild_id), embed=None, view=view
            )

        elif selected_value == "verified_role":
            view = VerificationSubView(
                self.guild_id,
                VerificationRoleConfigSelect(
                    self.guild_id,
                    self.parent_view,
                    "verified_role_id",
                    "ui.verification_select_verified_role",
                ),
                self.parent_view,
            )
            await interaction.response.edit_message(
                content=i18n.get_text("ui.verification_select_verified_role", self.guild_id), embed=None, view=view
            )

        elif selected_value == "review_role":
            view = VerificationSubView(
                self.guild_id,
                VerificationRoleConfigSelect(
                    self.guild_id,
                    self.parent_view,
                    "review_role_id",
                    "ui.verification_select_review_role",
                ),
                self.parent_view,
            )
            await interaction.response.edit_message(
                content=i18n.get_text("ui.verification_select_review_role", self.guild_id), embed=None, view=view
            )

        elif selected_value == "verify_channel":
            view = VerificationSubView(
                self.guild_id, VerificationChannelConfigSelect(self.guild_id, self.parent_view), self.parent_view
            )
            await interaction.response.edit_message(
                content=i18n.get_text("ui.verification_select_channel", self.guild_id), embed=None, view=view
            )

        elif selected_value == "publish":
            await self._publish_panel(interaction)

    async def _publish_panel(self, interaction: discord.Interaction) -> None:
        """
        將「我是人類」驗證按鈕面板發布到設定好的驗證頻道。

        Args:
            interaction: 觸發發布的互動物件
        """
        from features.verification.cog import VerificationButtonView

        config = GuildSettings.get_module_config(self.guild_id, "verification")
        verify_channel_id = config.get("verify_channel_id")
        if not verify_channel_id:
            await interaction.response.send_message(
                i18n.get_text("messages.verification_channel_required", self.guild_id), ephemeral=True
            )
            return

        channel = interaction.guild.get_channel(int(verify_channel_id))
        if not channel:
            await interaction.response.send_message(
                i18n.get_text("messages.verification_channel_not_found", self.guild_id), ephemeral=True
            )
            return

        embed = discord.Embed(
            title=i18n.get_text("messages.verification_publish_title", self.guild_id),
            description=i18n.get_text("messages.verification_publish_description", self.guild_id),
            color=discord.Color.green()
        )
        try:
            await channel.send(embed=embed, view=VerificationButtonView(self.guild_id))
        except discord.HTTPException as error:
            logger.error(f"發布驗證面板失敗：{error}", exc_info=True)
            await interaction.response.send_message(
                i18n.get_text("messages.verification_publish_error", self.guild_id), ephemeral=True
            )
            return
        await interaction.response.edit_message(embed=self.parent_view.get_embed(), view=self.parent_view)
        await interaction.followup.send(
            i18n.get_text("messages.verification_publish_success", self.guild_id, channel=channel.mention),
            ephemeral=True,
        )


class VerificationSettingView(discord.ui.View):
    """
    驗證系統設定面板的主視圖。
    """

    def __init__(self, guild_id: int, on_back: BackCallback) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.on_back = on_back
        self.add_item(VerificationSettingSelect(guild_id, self))
        self.add_item(self._create_toggle_button())
        self.add_item(self._create_back_button())

    def _create_back_button(self) -> discord.ui.Button:
        """
        建立返回上層面板的按鈕。

        Returns:
            設定好回呼的按鈕元件
        """
        button = discord.ui.Button(
            label=i18n.get_text("ui.back", self.guild_id), style=discord.ButtonStyle.secondary
        )
        button.callback = self.back_to_main
        return button

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        """
        執行注入的上層面板返回操作。

        Args:
            interaction: 觸發返回的互動物件
        """
        await self.on_back(interaction)

    def _create_toggle_button(self) -> discord.ui.Button:
        config = GuildSettings.get_module_config(self.guild_id, "verification")
        current_state = config.get("enabled", False)
        state_text = i18n.get_text("ui.state_on" if current_state else "ui.state_off", self.guild_id)
        button = discord.ui.Button(
            label=f"{i18n.get_text('ui.verification_toggle', self.guild_id)}: {state_text}",
            style=discord.ButtonStyle.success if current_state else discord.ButtonStyle.danger,
        )
        button.callback = self.toggle_enabled
        return button

    async def toggle_enabled(self, interaction: discord.Interaction) -> None:
        """
        切換驗證系統啟用狀態。關閉可以直接切換；開啟前必須設定齊全所需項目，
        並且需要額外確認，因為啟用會對現有成員發放身分組並鎖定所有頻道的發言權限。

        Args:
            interaction: 觸發切換的互動物件
        """
        config = GuildSettings.get_module_config(self.guild_id, "verification")

        if config.get("enabled", False):
            # 關閉不需要確認，也不會自動復原頻道權限
            await GuildSettings.set_module_config(self.guild_id, "verification", "enabled", False)
            updated_view = VerificationSettingView(self.guild_id, self.on_back)
            await interaction.response.edit_message(embed=updated_view.get_embed(), view=updated_view)
            status = i18n.get_text("ui.state_off", self.guild_id)
            await interaction.followup.send(
                i18n.get_text(
                    "messages.setting_status_updated", self.guild_id,
                    feature=i18n.get_text("ui.verification_toggle", self.guild_id), status=status,
                ),
                ephemeral=True,
            )
            return

        required_keys = ["restricted_role_id", "verified_role_id", "verify_channel_id"]
        if not all(config.get(key) for key in required_keys):
            await interaction.response.send_message(
                i18n.get_text("messages.verification_lockdown_incomplete", self.guild_id), ephemeral=True
            )
            return

        warning_embed = discord.Embed(
            description=i18n.get_text("messages.verification_lockdown_warning", self.guild_id),
            color=discord.Color.red(),
        )
        confirm_view = VerificationLockdownConfirmView(self.guild_id, self)
        await interaction.response.edit_message(content=None, embed=warning_embed, view=confirm_view)

    def get_embed(self) -> discord.Embed:
        """
        依目前設定產生驗證系統總覽的 Embed。

        Returns:
            顯示目前設定狀態的 Embed
        """
        config = GuildSettings.get_module_config(self.guild_id, "verification")

        embed = discord.Embed(
            title=i18n.get_text("messages.verification_panel_title", self.guild_id),
            description=i18n.get_text("messages.verification_panel_description", self.guild_id),
            color=discord.Color.blurple()
        )

        not_set_text = i18n.get_text("messages.value_not_set", self.guild_id)

        status_text = i18n.get_text(
            "messages.status_enabled" if config.get("enabled", False) else "messages.status_disabled", self.guild_id
        )
        embed.add_field(
            name=i18n.get_text("messages.verification_status", self.guild_id),
            value=status_text,
            inline=True,
        )

        restricted_role_id = config.get("restricted_role_id")
        embed.add_field(
            name=i18n.get_text("messages.verification_restricted_role", self.guild_id),
            value=f"<@&{restricted_role_id}>" if restricted_role_id else not_set_text,
            inline=True
        )

        verified_role_id = config.get("verified_role_id")
        embed.add_field(
            name=i18n.get_text("messages.verification_verified_role", self.guild_id),
            value=f"<@&{verified_role_id}>" if verified_role_id else not_set_text,
            inline=True
        )

        verify_channel_id = config.get("verify_channel_id")
        embed.add_field(
            name=i18n.get_text("messages.verification_channel", self.guild_id),
            value=f"<#{verify_channel_id}>" if verify_channel_id else not_set_text,
            inline=True
        )

        review_role_id = config.get("review_role_id")
        embed.add_field(
            name=i18n.get_text("messages.verification_review_role", self.guild_id),
            value=f"<@&{review_role_id}>" if review_role_id else not_set_text,
            inline=True
        )

        return embed

