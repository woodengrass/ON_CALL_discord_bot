import datetime
import logging

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands
from discord.ui import Button, Modal, TextInput, View

from core.i18n import i18n
from features.custom_panels.panel import CustomPanelEditorView
from features.custom_panels.repository import CustomPanelStore

logger = logging.getLogger(__name__)


async def _resolve_guild_channel(guild: discord.Guild, channel_id: int) -> discord.abc.GuildChannel | None:
    """
    優先從 guild 快取取得頻道，找不到時向 Discord API 查詢。

    Args:
        guild: 頻道所屬伺服器
        channel_id: 欲取得的頻道 ID

    Returns:
        找到的 guild 頻道；頻道不存在時回傳 None
    """
    channel = guild.get_channel(channel_id)
    if channel is not None:
        return channel
    return await guild.fetch_channel(channel_id)


# ==============================================================================
#  組件 1: 審核控制按鈕 (View)
# ==============================================================================
class VerifyControlView(View):
    """
    身分組審核用的通過/拒絕按鈕 View，將使用者、身分組與通知頻道資訊編碼於 custom_id 中。
    """

    def __init__(
        self,
        guild_id: int,
        user_id: int,
        role_id: int | None,
        notify_channel_id: int | None = None
    ) -> None:
        super().__init__(timeout=None)

        # 安全轉型
        safe_user_id = int(user_id)
        safe_role_id = int(role_id) if role_id else 0
        safe_notify_channel_id = int(notify_channel_id) if notify_channel_id else 0

        self.add_item(Button(
            style=discord.ButtonStyle.green,
            label=i18n.get_text("ui.approve", guild_id),
            custom_id=f"v:ok:{safe_user_id}:{safe_role_id}:{safe_notify_channel_id}"
        ))

        self.add_item(Button(
            style=discord.ButtonStyle.red,
            label=i18n.get_text("ui.deny", guild_id),
            custom_id=f"v:no:{safe_user_id}:{safe_role_id}:{safe_notify_channel_id}"
        ))


# ==============================================================================
#  組件 2: 輸入表單 (Modal)
# ==============================================================================
class PanelInputModal(Modal):
    """
    自訂面板的表單輸入視窗，提交後將內容交由 CustomPanelSystem 處理。
    """

    def __init__(self, bot: commands.Bot, title: str, label: str, guild_id: int, button_config: dict) -> None:
        super().__init__(title=title[:45])
        self.bot = bot
        self.guild_id = guild_id
        self.button_config = button_config

        self.input_text = TextInput(
            label=label[:45],
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.input_text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # 邏輯分離：Modal 只負責傳遞資料，實際處理交給 Cog
        await interaction.response.defer(ephemeral=True)

        cog = self.bot.get_cog("CustomPanelSystem")
        if cog:
            await cog.submit_form_log(interaction, self.button_config, self.input_text.value)
        else:
            error_message = i18n.get_text("messages.error_cog_not_loaded", self.guild_id)
            await interaction.followup.send(error_message, ephemeral=True)


# ==============================================================================
#  組件 3: 面板按鈕 (Panel Button)
# ==============================================================================
class PanelButton(Button):
    """
    自訂面板上的單一按鈕，點擊後交由 CustomPanelSystem 處理對應行為。
    """

    def __init__(self, bot: commands.Bot, button_id: str, button_config: dict, guild_id: int) -> None:
        style_map = {"blue": discord.ButtonStyle.blurple, "gray": discord.ButtonStyle.gray,
                     "green": discord.ButtonStyle.green, "red": discord.ButtonStyle.red}

        super().__init__(
            style=style_map.get(button_config.get("style"), discord.ButtonStyle.gray),
            label=button_config.get("label", i18n.get_text("messages.custom_panel_default_button", guild_id)),
            custom_id=button_id
        )
        self.bot = bot
        self.config = button_config
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("CustomPanelSystem")
        if cog:
            await cog.handle_panel_click(interaction, self.config)
        else:
            error_message = i18n.get_text("messages.error_cog_not_loaded", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)


class CustomPanelView(View):
    """
    自訂面板的整體 View，依設定動態建立各個 PanelButton。
    """

    def __init__(self, bot: commands.Bot, panel_config: dict) -> None:
        super().__init__(timeout=None)
        self.guild_id = panel_config.get("guild_id")

        buttons = panel_config.get("buttons", {})
        for button_uuid, button_data in buttons.items():
            self.add_item(PanelButton(bot, button_uuid, button_data, self.guild_id))


# ==============================================================================
#  Cog: 系統核心邏輯
# ==============================================================================
class CustomPanelSystem(commands.Cog):
    """
    處理自訂面板按鈕點擊、表單提交與身分組審核流程的核心邏輯。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """
        Cog 載入時向 core.lifecycle 註冊定期清理函式，並註冊既有自訂面板的持久化 View。
        此時尚未連上 Discord 閘道，但 add_view() 不需要即時的 guild/channel 快取，
        且 CustomPanelStore 的快取已在 bot.py 的 setup_hook() 載入完成，可以安全註冊。
        """
        from core import lifecycle
        lifecycle.register_cleanup_handler(self._cleanup_stale_panels)
        self._register_persistent_views()

    def _register_persistent_views(self) -> None:
        """
        將既有自訂面板重新註冊為持久化 View，確保機器人重啟後按鈕仍可互動。
        """
        panel_data = CustomPanelStore.data
        registered_count = 0
        all_custom_panels = panel_data.get("panels", {})

        for message_id, panel_config in all_custom_panels.items():
            try:
                view = CustomPanelView(self.bot, panel_config)
                self.bot.add_view(view)
                registered_count += 1
            except Exception as e:
                logger.error(f"註冊 CustomPanel {message_id} 失敗：{e}", exc_info=True)

        print(f"[資訊] 已註冊 {registered_count} 個自訂面板的持久化視圖。")

    async def _cleanup_stale_panels(self) -> None:
        """
        定期檢查並清除已失效的自訂面板紀錄（例如頻道或訊息已被刪除）。
        """
        panel_data = CustomPanelStore.data
        panel_changed = False
        all_custom_panels = panel_data.get("panels", {})

        for message_id_str in list(all_custom_panels.keys()):
            panel_config = all_custom_panels[message_id_str]
            channel_id = panel_config.get("channel_id")
            channel = self.bot.get_channel(channel_id)

            if not channel:
                panel_changed = await CustomPanelStore.remove_panel(int(message_id_str)) or panel_changed
                continue

            try:
                await channel.fetch_message(int(message_id_str))
            except discord.NotFound:
                panel_changed = await CustomPanelStore.remove_panel(int(message_id_str)) or panel_changed
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"檢查 Custom Panel 訊息時發生網路問題，先保留：{e}")

        if panel_changed:
            print("[背景任務] 已清理無效的 Custom Panel 紀錄。")

    # --- 1. 面板按鈕點擊處理 ---
    async def handle_panel_click(self, interaction: discord.Interaction, config: dict) -> None:
        """
        依按鈕類型執行對應行為：切換身分組、顯示隱藏訊息，或開啟表單/審核 Modal。

        Args:
            interaction: 觸發按鈕點擊的互動物件
            config: 該按鈕的設定內容
        """
        try:
            button_type = config.get("type")
            guild_id = interaction.guild.id

            # Type 1: 給予/移除身分組 (API 優化版)
            if button_type == 1:
                await interaction.response.defer(ephemeral=True)
                role_ids = config.get("role_ids", [])
                if not isinstance(role_ids, list):
                    role_ids = [role_ids]

                member = interaction.user
                roles_to_add = []
                roles_to_remove = []
                added_names = []
                removed_names = []

                # 先分類，不直接呼叫 API
                for role_id in role_ids:
                    role = interaction.guild.get_role(role_id)
                    if not role:
                        continue

                    if role in member.roles:
                        roles_to_remove.append(role)
                        removed_names.append(role.name)
                    else:
                        roles_to_add.append(role)
                        added_names.append(role.name)

                # 批次處理 API 請求 (減少等待時間與 Rate Limit 風險)
                try:
                    if roles_to_add:
                        await member.add_roles(*roles_to_add, reason="Panel Button Click")
                    if roles_to_remove:
                        await member.remove_roles(*roles_to_remove, reason="Panel Button Click")
                except discord.Forbidden:
                    await interaction.followup.send(i18n.get_text("messages.panel_role_error", guild_id),
                                                    ephemeral=True)
                    return

                result_message = ""
                if added_names:
                    result_message += i18n.get_text("messages.panel_role_added", guild_id,
                                                     role=", ".join(added_names)) + "\n"
                if removed_names:
                    result_message += i18n.get_text("messages.panel_role_removed", guild_id,
                                                     role=", ".join(removed_names))
                no_changes_message = i18n.get_text("messages.panel_no_changes", guild_id)
                await interaction.followup.send(result_message or no_changes_message, ephemeral=True)

            # Type 2: 隱藏訊息
            elif button_type == 2:
                content = config.get("content", "No content.")
                embed = discord.Embed(description=content, color=discord.Color.purple())
                await interaction.response.send_message(embed=embed, ephemeral=True)

            # Type 3 & 4: 表單 / 審核
            elif button_type in [3, 4]:
                if button_type == 4:
                    role_id = config.get("approve_role_id")
                    if role_id:
                        role = interaction.guild.get_role(role_id)
                        # 檢查：如果用戶已有身分組，直接阻止提交
                        if role and role in interaction.user.roles:
                            await interaction.response.send_message(
                                i18n.get_text("messages.verify_already_has_role", guild_id), ephemeral=True)
                            return

                modal_title = config.get("modal_title", "Form") or "Form"
                input_label = config.get("input_label", "Input:") or "Input"

                # 呼叫 Modal
                modal = PanelInputModal(self.bot, modal_title, input_label, guild_id, config)
                await interaction.response.send_modal(modal)

        except Exception as error:
            logger.error(f"處理面板按鈕點擊失敗：{error}", exc_info=True)
            if not interaction.response.is_done():
                error_message = i18n.get_text("messages.error_button_execution", interaction.guild.id)
                await interaction.response.send_message(error_message, ephemeral=True)

    # --- 2. 表單提交邏輯 (從 Modal 抽離) ---
    async def submit_form_log(self, interaction: discord.Interaction, config: dict, content: str) -> None:
        """
        將表單提交內容整理為 Embed 並發送至設定的紀錄頻道，若為身分組申請則附上審核按鈕。

        Args:
            interaction: 觸發表單提交的互動物件
            config: 該按鈕的設定內容
            content: 使用者填寫的表單內容
        """
        try:
            log_channel_id = config.get("log_channel_id")
            button_type = config.get("type")
            guild_id = interaction.guild.id

            if not log_channel_id:
                error_message = i18n.get_text("messages.error_log_channel_not_configured", guild_id)
                await interaction.followup.send(error_message, ephemeral=True)
                return

            try:
                log_channel = await _resolve_guild_channel(interaction.guild, log_channel_id)
            except Exception as e:
                logger.error(f"取得紀錄頻道失敗：{e}", exc_info=True)
                error_message = i18n.get_text("messages.error_log_channel_not_found", guild_id)
                await interaction.followup.send(error_message, ephemeral=True)
                return

            if log_channel is None:
                error_message = i18n.get_text("messages.error_log_channel_not_found", guild_id)
                await interaction.followup.send(error_message, ephemeral=True)
                return

            # 使用 Discord Timestamp (<t:timestamp:f>)，這會自動轉換為使用者的當地時間，而不是伺服器時間
            timestamp_code = f"<t:{int(datetime.datetime.now().timestamp())}:f>"

            if button_type == 4:
                title_key = "messages.log_title_apply"
                color = discord.Color.gold()
            else:
                title_key = "messages.log_title_form"
                color = discord.Color.blue()

            description = i18n.get_text("messages.verify_log_desc", guild_id,
                                         user=interaction.user.mention,
                                         time=timestamp_code,
                                         content=content)

            embed = discord.Embed(
                title=i18n.get_text(title_key, guild_id),
                description=description,
                color=color
            )

            view = None
            if button_type == 4:
                role_id = config.get("approve_role_id")
                notify_channel_id = config.get("notify_channel_id")

                role = interaction.guild.get_role(role_id) if role_id else None
                role_text = role.mention if role else i18n.get_text("labels.unknown_role", guild_id)
                embed.add_field(name=i18n.get_text("labels.target_role", guild_id), value=role_text, inline=False)

                view = VerifyControlView(guild_id, interaction.user.id, role_id, notify_channel_id)

            await log_channel.send(embed=embed, view=view)
            await interaction.followup.send(i18n.get_text("messages.panel_form_submitted", guild_id), ephemeral=True)

        except Exception as e:
            logger.error(f"處理表單提交失敗：{e}", exc_info=True)
            error_message = i18n.get_text("messages.error_unknown", interaction.guild.id)
            await interaction.followup.send(error_message, ephemeral=True)

    # --- 3. 審核按鈕監聽器 (v:ok/no) ---
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """
        監聽審核通過/拒絕按鈕的互動，處理身分組給予並通知申請人。

        Args:
            interaction: 觸發的互動物件
        """
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("v:"):
            return

        # 競爭條件檢查 (Concurrency Check)
        # 如果該訊息的按鈕已經被停用 (Disabled)，代表已經有其他管理員處理過了
        # 這可以防止兩個管理員同時點擊造成的重複操作
        if interaction.message and interaction.message.components:
            # 檢查第一個 Row 的第一個 Component 是否為 Disabled
            first_component = interaction.message.components[0].children[0]
            if first_component.disabled:
                already_processed_message = i18n.get_text("messages.verify_already_processed", interaction.guild.id)
                await interaction.response.send_message(already_processed_message, ephemeral=True)
                return

        if not interaction.user.guild_permissions.administrator:
            admin_only_message = i18n.get_text("messages.verify_review_admin_only", interaction.guild.id)
            await interaction.response.send_message(admin_only_message, ephemeral=True)
            return

        await interaction.response.defer()

        try:
            parts = custom_id.split(":")
            action = parts[1]
            user_id = int(parts[2])
            role_id = int(parts[3])
            notify_channel_id = int(parts[4])

            guild = interaction.guild
            admin = interaction.user

            # 安全地取得成員資料
            member = guild.get_member(user_id)
            if not member:
                try:
                    member = await guild.fetch_member(user_id)
                except Exception as e:
                    logger.warning(f"取得成員資料失敗，可能已離開伺服器：{e}")
                    member = None

            role = guild.get_role(role_id) if role_id > 0 else None
            notify_result = ""

            if action == "ok":
                if member and role:
                    try:
                        await member.add_roles(role, reason=f"Approved by {admin.name}")
                    except discord.Forbidden:
                        await interaction.followup.send(i18n.get_text("messages.verify_role_error", guild.id),
                                                        ephemeral=True)
                        return

                notify_result = await self._send_notification(guild, member, notify_channel_id, action="approve",
                                                                role=role, admin=admin)

                await self._update_log_message(interaction, discord.Color.green(),
                                               i18n.get_text("messages.verify_approved", guild.id, admin=admin.mention),
                                               notify_result, user_id, role_id, notify_channel_id)

            elif action == "no":
                notify_result = await self._send_notification(guild, member, notify_channel_id, action="deny",
                                                                role=role, admin=admin)

                await self._update_log_message(interaction, discord.Color.red(),
                                               i18n.get_text("messages.verify_denied", guild.id, admin=admin.mention),
                                               notify_result, user_id, role_id, notify_channel_id)

        except Exception as e:
            logger.error(f"處理審核按鈕動作失敗：{e}", exc_info=True)
            error_message = i18n.get_text("messages.error_unknown", interaction.guild.id)
            await interaction.followup.send(error_message, ephemeral=True)

    # --- 4. 面板刪除監聽器 ---
    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """
        當自訂面板訊息被刪除時，同步移除資料庫紀錄。

        Args:
            payload: 原始訊息刪除事件資料
        """
        if not payload.guild_id:
            return
        if await CustomPanelStore.remove_panel(payload.message_id):
            print(f"[資訊] Custom Panel 已從資料庫移除（訊息 ID：{payload.message_id}）")

    # --- 輔助函式 ---
    async def _update_log_message(
        self,
        interaction: discord.Interaction,
        color: discord.Color,
        status_text: str,
        notify_result: str,
        user_id: int,
        role_id: int,
        notify_channel_id: int
    ) -> None:
        """
        更新審核紀錄訊息的狀態欄位，並停用審核按鈕。

        Args:
            interaction: 觸發審核動作的互動物件
            color: 更新後 Embed 的顏色
            status_text: 審核結果文字
            notify_result: 通知申請人的結果文字
            user_id: 申請人使用者 ID
            role_id: 申請的身分組 ID
            notify_channel_id: 通知頻道 ID
        """
        embed = interaction.message.embeds[0]
        embed.color = color

        # 安全移除舊狀態，保留其他欄位 (如 Target Role)
        status_label = i18n.get_text("labels.status", interaction.guild.id)
        remaining_fields = [field for field in embed.fields if field.name != status_label]
        embed.clear_fields()
        for field in remaining_fields:
            embed.add_field(name=field.name, value=field.value, inline=field.inline)

        embed.add_field(name=status_label, value=f"{status_text}\n{notify_result}", inline=False)

        # 停用按鈕
        disabled_view = VerifyControlView(interaction.guild.id, user_id, role_id, notify_channel_id)
        for item in disabled_view.children:
            item.disabled = True

        await interaction.message.edit(embed=embed, view=disabled_view)

    async def _send_notification(
        self,
        guild: discord.Guild,
        member: discord.Member | None,
        notify_channel_id: int,
        action: str,
        role: discord.Role | None,
        admin: discord.Member
    ) -> str:
        """
        將審核結果通知申請人，優先發送至指定頻道，若無頻道則嘗試私訊。

        Args:
            guild: 伺服器物件
            member: 申請人成員物件，若已離開伺服器則為 None
            notify_channel_id: 通知頻道 ID，0 表示未設定
            action: 審核結果，"approve" 或 "deny"
            role: 申請的身分組物件
            admin: 執行審核的管理員

        Returns:
            說明通知結果的文字（已發送至頻道、已私訊或發送失敗）
        """
        if not member:
            return i18n.get_text("messages.notify_status_user_left", guild.id)

        role_name = role.name if role else i18n.get_text("labels.unknown_role", guild.id)
        # 通知訊息也使用 Discord Timestamp
        timestamp_code = f"<t:{int(datetime.datetime.now().timestamp())}:f>"

        if action == "approve":
            color = discord.Color.green()
            result_text = i18n.get_text("messages.notify_status_approved", guild.id)
        else:
            color = discord.Color.red()
            result_text = i18n.get_text("messages.notify_status_denied", guild.id)

        embed_title = i18n.get_text("messages.notify_title_result", guild.id, role=role_name)
        embed = discord.Embed(title=embed_title, color=color)

        embed.add_field(name=i18n.get_text("messages.notify_label_result", guild.id), value=result_text, inline=False)
        embed.add_field(name=i18n.get_text("messages.notify_label_time", guild.id), value=timestamp_code, inline=False)
        embed.add_field(name=i18n.get_text("messages.notify_label_reviewer", guild.id), value=admin.mention,
                        inline=False)

        sent_channel = False
        sent_dm = False

        if notify_channel_id > 0:
            try:
                notify_channel = await _resolve_guild_channel(guild, notify_channel_id)
            except Exception as e:
                logger.error(f"取得通知頻道失敗：{e}", exc_info=True)
                notify_channel = None

            if notify_channel:
                try:
                    await notify_channel.send(content=member.mention, embed=embed)
                    sent_channel = True
                except Exception as e:
                    logger.error(f"發送審核通知至頻道失敗：{e}", exc_info=True)

        if not sent_channel:
            try:
                footer_text = i18n.get_text("messages.notify_footer", guild.id, guild=guild.name)
                embed.set_footer(text=footer_text)
                await member.send(content=member.mention, embed=embed)
                sent_dm = True
            except Exception as e:
                logger.warning(f"私訊通知申請人失敗，可能已關閉私訊：{e}")

        if sent_channel:
            return i18n.get_text("messages.notify_status_sent_channel", guild.id, channel=f"<#{notify_channel_id}>")
        elif sent_dm:
            return i18n.get_text("messages.notify_status_sent_dm", guild.id)
        else:
            return i18n.get_text("messages.notify_status_failed", guild.id)


class CustomPanelCommands(commands.Cog):
    """提供自訂互動面板編輯入口。"""

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="custom_panel", description=locale_str("custom_panel"))
    async def custom_panel(self, interaction: discord.Interaction) -> None:
        """顯示自訂面板編輯器。"""
        view = CustomPanelEditorView(interaction)
        await interaction.response.send_message(embed=view.current_embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CustomPanelSystem(bot))
    await bot.add_cog(CustomPanelCommands())

