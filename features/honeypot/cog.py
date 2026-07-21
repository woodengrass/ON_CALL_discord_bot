import logging

import discord
from discord.ext import commands, tasks

from core.audit_log_repository import add_log_entry
from core.guild_settings import GuildSettings
from core.i18n import i18n

logger = logging.getLogger(__name__)


class HoneypotMonitor(commands.Cog):
    """
    監控蜜罐頻道，當使用者在蜜罐頻道發言時刪除訊息並依設定進行封禁，
    並記錄違規內容，若相同內容再次出現於其他頻道也會一併處理。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.user_messages: dict[tuple[int, int], set[str]] = {}  # {(guild_id, user_id): set(message_content)}
        self.cleanup_task.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止清理背景任務。
        """
        self.cleanup_task.cancel()

    def get_all_banned_texts(self, guild_id: int) -> list[str]:
        """
        取得指定伺服器目前已記錄的所有違規訊息內容。

        Args:
            guild_id: 伺服器 ID

        Returns:
            list of str，該伺服器所有被記錄的違規訊息內容
        """
        all_texts = set()
        for (tracked_guild_id, _user_id), texts in self.user_messages.items():
            if tracked_guild_id == guild_id:
                all_texts.update(texts)
        return list(all_texts)

    @tasks.loop(hours=12)
    async def cleanup_task(self) -> None:
        """
        定期清除已離開伺服器（或伺服器已不存在）成員的違規紀錄，避免記憶體無限增長。
        """
        await self.bot.wait_until_ready()

        expired_keys = []
        for guild_id, user_id in list(self.user_messages.keys()):
            guild = self.bot.get_guild(guild_id)
            if not guild or not guild.get_member(user_id):
                expired_keys.append((guild_id, user_id))

        for key in expired_keys:
            del self.user_messages[key]

        if expired_keys:
            print(f"[背景任務] 已清理 {len(expired_keys)} 筆已離開伺服器成員的蜜罐違規紀錄。")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        監聽所有訊息，判斷是否觸發蜜罐或重複違規內容偵測。

        Args:
            message: 收到的訊息物件
        """
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id

        honeypot_config = GuildSettings.get_module_config(guild_id, "honeypot")
        honeypot_id_str = honeypot_config.get("channel_id")

        if not honeypot_id_str:
            return

        honeypot_id = int(honeypot_id_str)

        announcement_id = GuildSettings.get_log_channel(guild_id)
        if str(message.author.id) in GuildSettings.get_whitelist(guild_id):
            return

        bot_member = message.guild.me
        if not bot_member.guild_permissions.manage_messages:
            return

        history_key = (guild_id, message.author.id)

        if message.channel.id == honeypot_id:
            user_banned_texts = self.user_messages.setdefault(history_key, set())
            user_banned_texts.add(message.content)

            try:
                await message.delete()
            except Exception as e:
                logger.error(f"刪除蜜罐訊息失敗：{e}", exc_info=True)

            if announcement_id:
                await self._announce_violation(int(announcement_id), message.author, message.content, guild_id)

            if (
                bot_member.guild_permissions.ban_members
                and bot_member.top_role.position > message.author.top_role.position
            ):
                try:
                    reason = i18n.get_text("messages.ban_reason_honeypot", guild_id)
                    await message.guild.ban(message.author, reason=reason)
                    await add_log_entry(guild_id, message.author.id, "honeypot_ban", reason)
                except Exception as e:
                    logger.error(f"封禁失敗：{e}", exc_info=True)
            return

        if history_key in self.user_messages and message.content in self.user_messages[history_key]:
            try:
                await message.delete()
            except Exception as e:
                logger.error(f"刪除重複違規訊息失敗：{e}", exc_info=True)

            if announcement_id:
                await self._announce_violation(int(announcement_id), message.author, message.content, guild_id)

            if (
                bot_member.guild_permissions.ban_members
                and bot_member.top_role.position > message.author.top_role.position
            ):
                try:
                    reason = i18n.get_text("messages.ban_reason_spam", guild_id)
                    await message.guild.ban(message.author, reason=reason)
                    await add_log_entry(guild_id, message.author.id, "honeypot_repeat_ban", reason)
                except Exception as e:
                    logger.error(f"封禁失敗：{e}", exc_info=True)
            return

    async def _announce_violation(
        self,
        channel_id: int,
        user: discord.Member,
        content: str,
        guild_id: int
    ) -> None:
        """
        於公告頻道發送違規訊息通知。

        Args:
            channel_id: 公告頻道 ID
            user: 違規使用者
            content: 違規訊息內容
            guild_id: 伺服器 ID
        """
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        safe_content = content[:200].replace("`", "ˋ")
        message = i18n.get_text("messages.honeypot_warning", guild_id, user=user.mention, content=safe_content)

        try:
            await channel.send(message)
        except Exception as e:
            logger.error(f"發送違規通知失敗：{e}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HoneypotMonitor(bot))

