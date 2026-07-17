import logging
import time
from collections import deque

import discord
from discord.ext import commands, tasks

from core.audit_log_repository import add_log_entry
from core.config import CONFIG
from core.guild_settings import GuildSettings
from core.i18n import i18n

logger = logging.getLogger(__name__)

# 讀取全域設定 (預設值)
raid_config = CONFIG.get("anti_raid", {})
JOIN_WINDOW_SECONDS = raid_config.get("join_window_seconds", 60)
JOIN_THRESHOLD = raid_config.get("join_threshold", 10)


class AntiRaid(commands.Cog):
    """
    偵測短時間內大量成員加入的炸群行為，達到閾值時通知管理員（僅警示，不自動處置）。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.recent_joins: dict[int, deque[float]] = {}
        self.raid_alerted: set[int] = set()  # 已發過警示的伺服器，避免同一波加入重複通知
        self.cleanup_task.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止清理背景任務。
        """
        self.cleanup_task.cancel()

    def is_module_enabled(self, guild_id: int) -> bool:
        """
        檢查指定伺服器是否已啟用防炸群功能。

        Args:
            guild_id: 伺服器 ID

        Returns:
            True 表示已啟用
        """
        config = GuildSettings.get_module_config(guild_id, "anti_raid")
        return config.get("enabled", False)

    @tasks.loop(minutes=10)
    async def cleanup_task(self) -> None:
        """
        定期清除已經沒有近期加入紀錄的伺服器，避免記憶體無限增長。
        """
        now = time.time()
        expired_guild_ids = []
        for guild_id, join_times in self.recent_joins.items():
            if not join_times or now - join_times[-1] > JOIN_WINDOW_SECONDS:
                expired_guild_ids.append(guild_id)

        for guild_id in expired_guild_ids:
            del self.recent_joins[guild_id]
            self.raid_alerted.discard(guild_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        監聽成員加入事件，統計短時間內的加入速率並在超過閾值時警示管理員。

        Args:
            member: 加入的成員物件
        """
        guild_id = member.guild.id
        if not self.is_module_enabled(guild_id):
            return

        now = time.time()
        join_times = self.recent_joins.setdefault(guild_id, deque())
        join_times.append(now)

        while join_times and now - join_times[0] > JOIN_WINDOW_SECONDS:
            join_times.popleft()

        if len(join_times) < JOIN_THRESHOLD:
            self.raid_alerted.discard(guild_id)
            return

        if guild_id in self.raid_alerted:
            return  # 同一波加入已經警示過，避免每個新成員都重複發通知

        self.raid_alerted.add(guild_id)
        await self._alert_raid(member.guild, len(join_times))

    async def _alert_raid(self, guild: discord.Guild, join_count: int) -> None:
        """
        於公告頻道發送炸群警示通知，並記錄到稽核紀錄。

        Args:
            guild: 觸發警示的伺服器物件
            join_count: 時間窗內偵測到的加入人數
        """
        logger.warning(
            "偵測到疑似炸群：伺服器=%s (%s)，%s 秒內加入 %s 人",
            guild.name, guild.id, JOIN_WINDOW_SECONDS, join_count,
        )

        # 炸群警示是伺服器層級事件，沒有單一對應的使用者，user_id 用 0 作為佔位值
        await add_log_entry(guild.id, 0, "raid_alert", f"{JOIN_WINDOW_SECONDS} 秒內加入 {join_count} 人")

        announcement_id = GuildSettings.get_log_channel(guild.id)
        if not announcement_id:
            return

        channel = guild.get_channel(int(announcement_id))
        if not channel:
            return

        alert_message = i18n.get_text(
            "messages.raid_alert", guild.id, count=join_count, seconds=JOIN_WINDOW_SECONDS
        )
        try:
            await channel.send(alert_message)
        except Exception as e:
            logger.error(f"發送炸群警示失敗：{e}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiRaid(bot))

