import asyncio
import logging

from discord.ext import commands, tasks

from core.config import CONFIG
from core.guild_settings import GuildSettings
from core.i18n import i18n

logger = logging.getLogger(__name__)

INTERVAL = CONFIG.get("people_counting", {}).get("update_interval_minutes", 10)
EDIT_STAGGER_SECONDS = 1


class PeopleCounting(commands.Cog):
    """
    定期更新各伺服器的人數統計頻道名稱。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.count_update_task.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止人數更新背景任務。
        """
        self.count_update_task.cancel()

    @tasks.loop(minutes=INTERVAL)
    async def count_update_task(self) -> None:
        """
        巡覽所有伺服器設定，將已設定人數統計頻道的名稱更新為目前成員數。
        """
        all_settings = GuildSettings.data

        for guild_id_str, settings in all_settings.items():
            people_counting_config = settings.get("modules", {}).get("people_counting")
            if not people_counting_config:
                continue

            channel_id = people_counting_config.get("channel_id")
            if not channel_id:
                continue

            try:
                guild = self.bot.get_guild(int(guild_id_str))
                if not guild:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue
                member_count = guild.member_count
                new_name = i18n.get_text("messages.count_channel_name", int(guild_id_str), count=member_count)
                if channel.name != new_name:
                    await channel.edit(name=new_name)
                    await GuildSettings.set_module_config(
                        int(guild_id_str), "people_counting", "last_count", member_count
                    )
                    print(f"[資訊] 更新人數計算 {guild.name}：{member_count}")
                    # 分散每個伺服器的改名請求，避免同一瞬間集中打 Discord API
                    await asyncio.sleep(EDIT_STAGGER_SECONDS)

            except Exception as e:
                logger.error(f"伺服器 {guild_id_str} 人數計算更新失敗：{e}", exc_info=True)

    @count_update_task.before_loop
    async def before_count_update(self) -> None:
        """
        等待機器人完成登入後再開始執行人數更新任務。
        """
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PeopleCounting(bot))

