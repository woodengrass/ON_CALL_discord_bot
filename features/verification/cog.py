import asyncio
import datetime
import logging

import discord
from discord.ext import commands

from core.audit_log_repository import add_log_entry
from core.config import CONFIG
from core.guild_settings import GuildSettings
from core.i18n import i18n
from features.verification.repository import (
    claim_review_creation,
    complete_review_creation,
    create_pending,
    delete_entry,
    delete_guild_entries,
    get_entry,
    get_review_creations,
    get_stale_review_channels,
    reset_flagged_entry_by_channel,
    reset_review_creation,
    set_pending,
    set_review_channel,
    set_status,
)
from features.verification.service import calculate_risk_score

logger = logging.getLogger(__name__)

# 讀取全域設定 (預設值)
verification_config = CONFIG.get("verification", {})
DEFAULT_NEW_ACCOUNT_DAYS = verification_config.get("new_account_days", 7)
DEFAULT_RISK_THRESHOLD = verification_config.get("risk_threshold", 2)

HUMAN_CHECK_CUSTOM_ID = "verify:human_check"
REVIEW_CATEGORY_NAME = "驗證審核"
REVIEW_CHANNEL_CLOSE_DELAY_SECONDS = 10


class VerificationReviewView(discord.ui.View):
    """
    管理員審核用的通過/拒絕按鈕，custom_id 直接編碼使用者 ID，重啟後仍可透過 on_interaction 解析處理。
    """

    def __init__(self, guild_id: int, user_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.success,
            label=i18n.get_text("ui.approve", guild_id),
            custom_id=f"verify:approve:{user_id}"
        ))
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label=i18n.get_text("ui.deny", guild_id),
            custom_id=f"verify:deny:{user_id}"
        ))


class VerificationButtonView(discord.ui.View):
    """
    貼在驗證頻道的「我是人類」按鈕，無狀態設計，只需要建立一次即可套用給所有等待驗證的成員。
    """

    def __init__(self, guild_id: int = 0) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.success,
            label=i18n.get_text("ui.human_check", guild_id),
            custom_id=HUMAN_CHECK_CUSTOM_ID
        ))


class Verification(commands.Cog):
    """
    新成員加入驗證系統：被動風險評分 + 簡單按鈕驗證 + 高風險帳號轉私人頻道人工審核。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._ready_initialized = False

    async def cog_load(self) -> None:
        """
        Cog 載入時向 core.lifecycle 註冊定期清理函式與伺服器移除清理函式，
        避免清理邏輯集中寫在 core 裡。
        """
        from core import lifecycle
        lifecycle.register_cleanup_handler(self._cleanup_stale_review_channels)
        lifecycle.register_guild_remove_handler(self._cleanup_guild_entries)

    async def _cleanup_stale_review_channels(self) -> None:
        """
        定期檢查並重置指向已失效審核頻道的驗證紀錄。
        保險機制：正常情況下頻道刪除時 on_guild_channel_delete 就會即時同步，
        這裡是防止漏接事件（例如機器人當下離線）時的後備清理。
        """
        stale_review_count = 0
        for guild_id, review_channel_id in await get_stale_review_channels():
            if self.bot.get_channel(review_channel_id):
                continue
            if await reset_flagged_entry_by_channel(guild_id, review_channel_id):
                stale_review_count += 1

        if stale_review_count:
            print(f"[背景任務] 已重置 {stale_review_count} 筆指向失效審核頻道的驗證紀錄。")

    async def _cleanup_guild_entries(self, guild_id: int) -> None:
        """
        機器人被移出伺服器時，清除該伺服器所有的驗證紀錄。

        Args:
            guild_id: 被移出的伺服器 ID
        """
        deleted_verifications = await delete_guild_entries(guild_id)
        if deleted_verifications:
            print(f"[資訊] 已移出伺服器 {guild_id}，清除 {deleted_verifications} 筆驗證紀錄。")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """
        機器人啟動時註冊「我是人類」持久化按鈕，確保重啟後仍然可以使用。
        """
        if self._ready_initialized:
            return
        self.bot.add_view(VerificationButtonView())
        await self._recover_incomplete_review_creations()
        self._ready_initialized = True

    def get_config(self, guild_id: int) -> dict:
        """
        取得指定伺服器的驗證系統設定。

        Args:
            guild_id: 伺服器 ID

        Returns:
            dict，驗證系統設定內容
        """
        return GuildSettings.get_module_config(guild_id, "verification")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        新成員加入時，若已啟用驗證系統則指派待驗證身分組並計算風險分數。

        Args:
            member: 加入的成員物件
        """
        config = self.get_config(member.guild.id)
        if not config.get("enabled", False):
            return

        restricted_role_id = config.get("restricted_role_id")
        if not restricted_role_id:
            return

        restricted_role = member.guild.get_role(int(restricted_role_id))
        if not restricted_role:
            logger.warning("驗證系統設定的待驗證身分組已不存在：伺服器 ID=%s", member.guild.id)
            return

        try:
            pending_reason = i18n.get_text("messages.verification_reason_pending", member.guild.id)
            await member.add_roles(restricted_role, reason=pending_reason)
        except Exception as error:
            logger.error(f"指派待驗證身分組失敗：{error}", exc_info=True)
            return

        new_account_days = config.get("new_account_days", DEFAULT_NEW_ACCOUNT_DAYS)
        risk_score = calculate_risk_score(member, new_account_days)
        await set_pending(member.guild.id, member.id, risk_score)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """
        成員離開伺服器時，清除其待驗證紀錄。

        Args:
            member: 離開的成員物件
        """
        await delete_entry(member.guild.id, member.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """
        審核頻道被刪除時（不論是正常結案自動刪除，還是管理員手動刪除），
        把仍卡在 flagged 狀態、指向該頻道的紀錄重置回 pending，避免之後那個人點擊按鈕時
        看到指向已不存在頻道的訊息、卡死在無法被重新評估的狀態。

        Args:
            channel: 被刪除的頻道物件
        """
        if not isinstance(channel, discord.TextChannel):
            return
        reset_count = await reset_flagged_entry_by_channel(channel.guild.id, channel.id)
        if reset_count:
            logger.info(
                "驗證審核頻道已刪除，已重置 %s 筆對應的驗證紀錄（伺服器 ID=%s，頻道 ID=%s）",
                reset_count, channel.guild.id, channel.id
            )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """
        監聽「我是人類」按鈕與管理員審核按鈕的互動。

        Args:
            interaction: 互動物件
        """
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("verify:"):
            return

        if custom_id == HUMAN_CHECK_CUSTOM_ID:
            await self._handle_human_check(interaction)
            return

        parts = custom_id.split(":")
        if len(parts) != 3:
            return
        action, user_id_str = parts[1], parts[2]

        # 競爭條件檢查：按鈕已停用代表已經有其他管理員處理過了
        if interaction.message and interaction.message.components:
            first_component = interaction.message.components[0].children[0]
            if first_component.disabled:
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_already_processed", interaction.guild.id), ephemeral=True
                )
                return

        await self._handle_review_action(interaction, action, int(user_id_str))

    async def _handle_human_check(self, interaction: discord.Interaction) -> None:
        """
        處理「我是人類」按鈕點擊：風險分數低則立即放行，否則開一個私人頻道轉人工審核。

        Args:
            interaction: 觸發按鈕的互動物件
        """
        guild_id = interaction.guild.id
        user_id = interaction.user.id

        entry = await get_entry(guild_id, user_id)
        if entry is None:
            config = self.get_config(guild_id)
            if not config.get("enabled", False):
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_no_entry_found", guild_id), ephemeral=True
                )
                return

            verified_role_id = config.get("verified_role_id")
            verified_role = interaction.guild.get_role(int(verified_role_id)) if verified_role_id else None
            if verified_role is None:
                logger.error("驗證系統設定的已驗證身分組不存在：伺服器 ID=%s", guild_id)
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_role_error", guild_id), ephemeral=True
                )
                return
            if verified_role in interaction.user.roles:
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_already_approved", guild_id), ephemeral=True
                )
                return

            restricted_role_id = config.get("restricted_role_id")
            restricted_role = interaction.guild.get_role(int(restricted_role_id)) if restricted_role_id else None
            if restricted_role is None:
                logger.error("驗證系統設定的待驗證身分組不存在：伺服器 ID=%s", guild_id)
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_role_error", guild_id), ephemeral=True
                )
                return

            try:
                if restricted_role not in interaction.user.roles:
                    pending_reason = i18n.get_text("messages.verification_reason_pending", guild_id)
                    await interaction.user.add_roles(restricted_role, reason=pending_reason)
            except Exception as error:
                logger.error(f"補發待驗證身分組失敗：{error}", exc_info=True)
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_role_error", guild_id), ephemeral=True
                )
                return

            try:
                new_account_days = config.get("new_account_days", DEFAULT_NEW_ACCOUNT_DAYS)
                risk_score = calculate_risk_score(interaction.user, new_account_days)
                created_pending = await create_pending(guild_id, user_id, risk_score)
                if created_pending:
                    entry = {"risk_score": risk_score, "status": "pending", "review_channel_id": None}
                else:
                    entry = await get_entry(guild_id, user_id)
                    if entry is None:
                        raise RuntimeError("驗證紀錄建立後仍找不到資料")
            except Exception as error:
                logger.error(f"補建待驗證紀錄失敗：{error}", exc_info=True)
                await interaction.response.send_message(
                    i18n.get_text("messages.error_unknown", guild_id), ephemeral=True
                )
                return

        if entry["status"] == "approved":
            await interaction.response.send_message(
                i18n.get_text("messages.verify_already_approved", guild_id), ephemeral=True
            )
            return
        if entry["status"] == "flagged":
            review_channel_id = entry.get("review_channel_id")
            channel_mention = f"<#{review_channel_id}>" if review_channel_id else "?"
            await interaction.response.send_message(
                i18n.get_text("messages.verify_pending_review_channel", guild_id, channel=channel_mention),
                ephemeral=True
            )
            return
        if entry["status"] == "review_creating":
            await interaction.response.send_message(
                i18n.get_text("messages.verify_already_processed", guild_id), ephemeral=True
            )
            return
        if entry["status"] == "rejected":
            # 拒絕為終止狀態，不重新評估風險分數，避免被拒絕者無限重試直到自動通過
            await interaction.response.send_message(
                i18n.get_text("messages.verify_rejected", guild_id), ephemeral=True
            )
            return

        config = self.get_config(guild_id)
        risk_threshold = config.get("risk_threshold", DEFAULT_RISK_THRESHOLD)

        if entry["risk_score"] < risk_threshold:
            approved = await self._approve_member(
                interaction.guild,
                interaction.user,
                config,
                action_type="verification_auto_approved",
            )
            message_key = "messages.verify_success" if approved else "messages.verify_role_error"
            await interaction.response.send_message(i18n.get_text(message_key, guild_id), ephemeral=True)
        else:
            if not await claim_review_creation(guild_id, user_id):
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_already_processed", guild_id), ephemeral=True
                )
                return
            review_channel = await self._open_review_channel(interaction.guild, interaction.user, config)
            if review_channel:
                await interaction.response.send_message(
                    i18n.get_text("messages.verify_review_channel_opened", guild_id, channel=review_channel.mention),
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    i18n.get_text("messages.error_unknown", guild_id), ephemeral=True
                )

    def _can_review(self, member: discord.Member, config: dict) -> bool:
        """
        判斷成員是否有權限執行審核動作：擁有伺服器管理員權限，或身上有設定好的審核人員身分組。

        Args:
            member: 觸發審核動作的成員物件
            config: 驗證系統設定

        Returns:
            True 代表該成員可以執行審核動作
        """
        if member.guild_permissions.administrator:
            return True
        review_role_id = config.get("review_role_id")
        if not review_role_id:
            return False
        review_role = member.guild.get_role(int(review_role_id))
        return review_role is not None and review_role in member.roles

    async def _handle_review_action(self, interaction: discord.Interaction, action: str, user_id: int) -> None:
        """
        處理管理員審核的通過/拒絕動作，並在完成後關閉私人審核頻道。

        Args:
            interaction: 觸發審核的互動物件
            action: "approve" 或 "deny"
            user_id: 被審核的使用者 ID
        """
        config = self.get_config(interaction.guild.id)
        if not self._can_review(interaction.user, config):
            admin_only_message = i18n.get_text("messages.verify_review_admin_only", interaction.guild.id)
            await interaction.response.send_message(admin_only_message, ephemeral=True)
            return

        await interaction.response.defer()
        guild = interaction.guild

        member = guild.get_member(user_id)

        if action == "approve" and member:
            approved = await self._approve_member(
                guild, member, config, action_type="verification_manual_approved"
            )
            if not approved:
                await interaction.followup.send(
                    i18n.get_text("messages.verify_role_error", guild.id), ephemeral=True
                )
                return
            status_text = i18n.get_text("messages.verify_approved", guild.id, admin=interaction.user.mention)
        elif action == "deny":
            await set_status(guild.id, user_id, "rejected")
            await add_log_entry(guild.id, user_id, "verification_manual_denied", f"審核人：{interaction.user}")
            status_text = i18n.get_text("messages.verify_denied", guild.id, admin=interaction.user.mention)
        else:
            status_text = i18n.get_text("messages.verify_member_not_found", guild.id)

        embed = interaction.message.embeds[0]
        embed.add_field(name=i18n.get_text("labels.status", guild.id), value=status_text, inline=False)

        disabled_view = VerificationReviewView(guild.id, user_id)
        for item in disabled_view.children:
            item.disabled = True

        await interaction.message.edit(embed=embed, view=disabled_view)

        closing_message = i18n.get_text("messages.verification_review_closing", guild.id)
        await interaction.followup.send(closing_message)
        await asyncio.sleep(REVIEW_CHANNEL_CLOSE_DELAY_SECONDS)
        try:
            await interaction.channel.delete()
        except Exception as error:
            logger.error(f"關閉驗證審核頻道失敗：{error}", exc_info=True)

    async def _approve_member(
        self, guild: discord.Guild, member: discord.Member, config: dict, action_type: str
    ) -> bool:
        """
        將成員從待驗證身分組轉為已驗證身分組。只有身分組操作完整成功後，
        才會更新驗證狀態與稽核紀錄；部分成功時會嘗試回復本次變更。

        Args:
            guild: 伺服器物件
            member: 要放行的成員物件
            config: 驗證系統設定
            action_type: 寫入稽核紀錄的動作類型

        Returns:
            True 表示身分組與驗證狀態皆處理完成；稽核紀錄失敗會另外記錄錯誤
        """
        restricted_role_id = config.get("restricted_role_id")
        verified_role_id = config.get("verified_role_id")
        approved_reason = i18n.get_text("messages.verification_reason_approved", guild.id)

        verified_role = guild.get_role(int(verified_role_id)) if verified_role_id else None
        restricted_role = guild.get_role(int(restricted_role_id)) if restricted_role_id else None
        if verified_role is None or restricted_role is None:
            logger.error(
                "驗證通過所需身分組不存在：伺服器 ID=%s，使用者 ID=%s",
                guild.id,
                member.id,
            )
            return False

        added_verified_role = False
        removed_restricted_role = False
        try:
            if verified_role not in member.roles:
                await member.add_roles(verified_role, reason=approved_reason)
                added_verified_role = True
            if restricted_role in member.roles:
                await member.remove_roles(restricted_role, reason=approved_reason)
                removed_restricted_role = True
        except Exception as error:
            logger.error(f"驗證通過後身分組調整失敗：{error}", exc_info=True)
            await self._rollback_approval_roles(
                member,
                verified_role,
                restricted_role,
                added_verified_role,
                removed_restricted_role,
            )
            return False

        try:
            await set_status(guild.id, member.id, "approved")
        except Exception as error:
            logger.error(f"更新驗證通過狀態失敗：{error}", exc_info=True)
            await self._rollback_approval_roles(
                member,
                verified_role,
                restricted_role,
                added_verified_role,
                removed_restricted_role,
            )
            return False

        try:
            await add_log_entry(guild.id, member.id, action_type, approved_reason)
        except Exception as error:
            logger.error(f"寫入驗證通過稽核紀錄失敗：{error}", exc_info=True)
        return True

    async def _rollback_approval_roles(
        self,
        member: discord.Member,
        verified_role: discord.Role,
        restricted_role: discord.Role,
        added_verified_role: bool,
        removed_restricted_role: bool,
    ) -> None:
        """
        回復本次核准流程已完成的身分組變更，避免 Discord 狀態與資料庫狀態不一致。

        Args:
            member: 要回復身分組的成員
            verified_role: 已驗證身分組
            restricted_role: 待驗證身分組
            added_verified_role: 本次流程是否新增過已驗證身分組
            removed_restricted_role: 本次流程是否移除過待驗證身分組
        """
        if removed_restricted_role:
            try:
                await member.add_roles(restricted_role, reason="驗證核准失敗，回復待驗證身分組")
            except Exception as error:
                logger.error(f"回復待驗證身分組失敗：{error}", exc_info=True)
        if added_verified_role:
            try:
                await member.remove_roles(verified_role, reason="驗證核准失敗，回復已驗證身分組")
            except Exception as error:
                logger.error(f"回復已驗證身分組失敗：{error}", exc_info=True)

    async def _open_review_channel(
        self, guild: discord.Guild, member: discord.Member, config: dict
    ) -> discord.TextChannel | None:
        """
        為高風險成員建立一個私人審核頻道（只有當事人與機器人看得到），附上審核用的通過/拒絕按鈕。

        Args:
            guild: 伺服器物件
            member: 待審核的成員物件
            config: 驗證系統設定

        Returns:
            建立好的審核頻道；若建立失敗則回傳 None
        """
        category = discord.utils.get(guild.categories, name=REVIEW_CATEGORY_NAME)
        if category is None:
            try:
                category = await guild.create_category(REVIEW_CATEGORY_NAME)
            except Exception as error:
                logger.error(f"建立驗證審核分類失敗：{error}", exc_info=True)
                category = None

        channel_name = f"verify-{member.name}".replace(" ", "-").lower()
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        review_role_id = config.get("review_role_id")
        if review_role_id:
            review_role = guild.get_role(int(review_role_id))
            if review_role:
                overwrites[review_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        review_channel = None
        try:
            review_channel = await guild.create_text_channel(
                channel_name, category=category, overwrites=overwrites
            )
            if not await set_review_channel(guild.id, member.id, review_channel.id):
                raise RuntimeError("驗證紀錄已不在建立審核頻道狀態")
        except Exception as error:
            logger.error(f"建立驗證審核頻道失敗：{error}", exc_info=True)
            await self._cleanup_failed_review_creation(guild.id, member.id, review_channel)
            return None

        account_age_days = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
        embed = discord.Embed(
            title=i18n.get_text("messages.verification_review_title", guild.id),
            description=i18n.get_text(
                "messages.verification_review_desc", guild.id,
                user=member.mention, user_tag=str(member), days=account_age_days
            ),
            color=discord.Color.gold()
        )

        view = VerificationReviewView(guild.id, member.id)
        try:
            await review_channel.send(content=member.mention, embed=embed, view=view)
        except Exception as error:
            logger.error(f"發送驗證審核請求失敗：{error}", exc_info=True)
            await self._cleanup_failed_review_creation(guild.id, member.id, review_channel)
            return None

        try:
            review_creation_completed = await complete_review_creation(
                guild.id, member.id, review_channel.id
            )
        except Exception as error:
            logger.error(f"完成驗證審核頻道狀態失敗：{error}", exc_info=True)
            review_creation_completed = False

        if not review_creation_completed:
            logger.error(
                "驗證審核頻道未能轉為等待審核狀態：伺服器 ID=%s，使用者 ID=%s，頻道 ID=%s",
                guild.id,
                member.id,
                review_channel.id,
            )
            await self._cleanup_failed_review_creation(guild.id, member.id, review_channel)
            return None

        return review_channel

    async def _cleanup_failed_review_creation(
        self,
        guild_id: int,
        user_id: int,
        review_channel: discord.TextChannel | None,
    ) -> None:
        """
        清理由建立失敗流程留下的審核頻道，並將驗證紀錄回復為 pending。

        Args:
            guild_id: 伺服器 ID
            user_id: 使用者 ID
            review_channel: 已建立的審核頻道；尚未建立時為 None
        """
        if review_channel is not None:
            try:
                await review_channel.delete()
            except Exception as error:
                logger.error(f"清理建立失敗的驗證審核頻道失敗：{error}", exc_info=True)
        try:
            await reset_review_creation(guild_id, user_id)
        except Exception as error:
            logger.error(f"回復驗證審核建立狀態失敗：{error}", exc_info=True)

    async def _recover_incomplete_review_creations(self) -> None:
        """
        Bot 啟動時清理前次程序中斷所留下的建立中審核頻道，並回復驗證紀錄。
        """
        for guild_id, user_id, channel_id in await get_review_creations():
            review_channel = self.bot.get_channel(channel_id) if channel_id else None
            if isinstance(review_channel, discord.TextChannel):
                try:
                    await review_channel.delete()
                except Exception as error:
                    logger.error(f"清理中斷的驗證審核頻道失敗：{error}", exc_info=True)
            try:
                await reset_review_creation(guild_id, user_id)
            except Exception as error:
                logger.error(f"回復中斷的驗證審核狀態失敗：{error}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Verification(bot))


