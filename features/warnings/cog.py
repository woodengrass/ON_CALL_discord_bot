import datetime
import logging

import discord
import pytz  # 如果沒有請執行 pip install pytz
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands, tasks

from features.warnings.panel import WarningSettingView
from features.warnings.repository import WarningStore
from features.warnings.wizard_state import WarningDraftStore

logger = logging.getLogger(__name__)

# 設定時區為台北 (與您的位置同步)
TAIWAN_TZ = pytz.timezone('Asia/Taipei')


class WarningTask(commands.Cog):
    """
    每分鐘檢查一次是否有符合排程時間的定時提醒，並依設定發送提醒訊息。
    """

    def __init__(self, bot: commands.Bot, draft_store: WarningDraftStore) -> None:
        self.bot = bot
        self.draft_store = draft_store
        self.check_warning_task.start()

    def cog_unload(self) -> None:
        """
        卸載 Cog 時停止定時提醒檢查任務並清除未完成草稿。
        """
        self.check_warning_task.cancel()
        self.draft_store.clear()

    @tasks.loop(minutes=1)
    async def check_warning_task(self) -> None:
        """
        每分鐘檢查一次是否需要發送提醒。
        """
        now = datetime.datetime.now(TAIWAN_TZ)
        current_time = now.strftime("%H:%M")  # 例如 "22:00"
        current_weekday = now.isoweekday()  # 1(一) ~ 7(日)
        current_day = now.day  # 1 ~ 31 號

        all_warning_data = WarningStore.data
        if not all_warning_data:
            return

        for warning_id, warning_data in all_warning_data.items():
            # 1. 基本檢查：是否啟用
            if not warning_data.get("active", True):
                continue

            schedule_config = warning_data.get("schedule", {})
            target_time = schedule_config.get("time")

            # 2. 時間檢查：小時:分鐘 是否吻合
            if target_time != current_time:
                continue

            # 3. 頻率與日期檢查
            frequency_type = schedule_config.get("type")
            days = schedule_config.get("days", [])

            should_send = False
            if frequency_type == "daily":
                should_send = True
            elif frequency_type == "weekly" and current_weekday in days:
                should_send = True
            elif frequency_type == "monthly" and current_day in days:
                should_send = True

            if should_send:
                await self.send_warning_embed(warning_data)

    async def send_warning_embed(self, warning_data: dict) -> None:
        """
        組裝並發送提醒 Embed。

        Args:
            warning_data: 單筆提醒排程的設定資料
        """
        guild_id = warning_data.get("guild_id")
        channel_id = warning_data.get("channel_id")
        role_id = warning_data.get("role_id")
        content = warning_data.get("content", {})

        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return

        channel = guild.get_channel(int(channel_id))
        if not channel:
            return

        # 處理變數轉換 (複用 Welcome 的邏輯)
        def format_text(text: str) -> str:
            if not text:
                return ""
            return text.replace("[people]", str(len(guild.members))) \
                .replace("[server]", guild.name)

        # 構建 Embed
        embed = discord.Embed(
            title=format_text(content.get("title")),
            description=format_text(content.get("desc")),
            color=discord.Color.purple(),
            timestamp=datetime.datetime.now()
        )

        if content.get("footer"):
            embed.set_footer(text=format_text(content.get("footer")))

        # 設定側邊圖 (Thumbnail)
        if content.get("thumbnail") and content["thumbnail"].startswith("http"):
            embed.set_thumbnail(url=content["thumbnail"])

        # 設定下方大圖 (Image)
        if content.get("image") and content["image"].startswith("http"):
            embed.set_image(url=content["image"])

        # 處理 Tag 身分組
        ping_content = f"<@&{role_id}>" if role_id else ""

        try:
            await channel.send(content=ping_content, embed=embed)
            print(f"[資訊] 已成功發送提醒：{content.get('title')}")
        except Exception as e:
            logger.error(f"定時提醒發送失敗：{e}", exc_info=True)

    @check_warning_task.before_loop
    async def before_check_warning(self) -> None:
        """
        確保機器人準備好後再開始迴圈。
        """
        await self.bot.wait_until_ready()


class WarningCommands(commands.Cog):
    """提供定時提醒設定入口。"""

    def __init__(self, draft_store: WarningDraftStore) -> None:
        self.draft_store = draft_store

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="warning_setting", description=locale_str("warning_setting"))
    async def warning_setting(self, interaction: discord.Interaction) -> None:
        """顯示定時提醒設定面板。"""
        view = WarningSettingView(interaction.guild.id, self.draft_store)
        await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    draft_store = WarningDraftStore()
    await bot.add_cog(WarningTask(bot, draft_store))
    await bot.add_cog(WarningCommands(draft_store))

