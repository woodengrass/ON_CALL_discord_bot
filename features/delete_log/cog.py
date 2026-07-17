import logging

import discord
from discord.ext import commands

from core.guild_settings import GuildSettings
from core.i18n import i18n

logger = logging.getLogger(__name__)


class DeleteListener(commands.Cog):
    """
    監聽訊息刪除事件，若伺服器已啟用刪除日誌則發送刪除紀錄。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        """
        當訊息被刪除時觸發，依伺服器設定決定是否發送刪除日誌。

        Args:
            message: 被刪除的訊息物件
        """
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id

        delete_log_config = GuildSettings.get_module_config(guild_id, "delete_log")
        if not delete_log_config.get("enabled", False):
            return

        announcement_channel_id = GuildSettings.get_log_channel(guild_id)
        if not announcement_channel_id:
            return
        try:
            channel = message.guild.get_channel(int(announcement_channel_id))
            if not channel:
                return

            timestamp = message.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

            content = message.content
            if not content:
                if message.attachments:
                    content = i18n.get_text("messages.attachment_only", guild_id)
                elif message.stickers:
                    content = i18n.get_text("messages.sticker_only", guild_id)
                else:
                    content = i18n.get_text("messages.empty", guild_id)

            log_message = i18n.get_text(
                "messages.delete_log", guild_id,
                user=message.author.mention,
                channel=message.channel.mention,
                time=timestamp,
                content=content[:1900])
            await channel.send(log_message)

        except Exception as e:
            logger.error(f"發送刪除日誌失敗：{e}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DeleteListener(bot))

