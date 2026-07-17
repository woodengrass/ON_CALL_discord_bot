import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from core.database import close_db, init_db
from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.logging import configure_logging
from features.custom_panels.repository import CustomPanelStore
from features.tickets.repository import TicketStore
from features.warnings.repository import WarningStore

EXTENSIONS = [
    "features.honeypot.cog",
    "hubs.anti_fraud.cog",
    "hubs.server_setting.cog",
    "features.message_tools.cog",
    "features.text_triggers.cog",
    "features.people_counting.cog",
    "features.welcome.cog",
    "core.lifecycle",
    "features.delete_log.cog",
    "features.tickets.cog",
    "features.anti_spam.cog",
    "features.voice_transcribe.cog",
    "features.chat_summary.cog",
    "features.custom_panels.cog",
    "features.link_checker.cog",
    "features.warnings.cog",
    "features.anti_raid.cog",
    "features.verification.cog",
    "admin.console",
    # "dev.verification_backdoor",  # 測試用後門，需要測試時取消註解；這一行本身可以進版控，實際檔案已被 .gitignore 排除
]
configure_logging()
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path="token.env")
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.all()


class HoneypotBot(commands.Bot):
    """
    自訂 Bot 子類別，透過 setup_hook 確保載入 Cog 與同步 Slash 指令只在啟動時執行一次。
    """

    async def setup_hook(self) -> None:
        """
        登入完成、連上 Gateway 前執行一次：初始化資料庫、載入所有 Cog 並同步全域 Slash 指令。
        discord.py 保證此方法只會被呼叫一次，不會像 on_ready 一樣在每次重新連線時重複觸發。
        """
        await init_db()

        await GuildSettings.load_cache()
        await TicketStore.load_cache()
        await CustomPanelStore.load_cache()
        await WarningStore.load_cache()

        await self.tree.set_translator(i18n)

        for extension in EXTENSIONS:
            await self.load_extension(extension)

        try:
            synced_commands = await self.tree.sync()
            print(f"成功同步了 {len(synced_commands)} 個全域 Slash 命令！")
        except Exception as error:
            logger.error(f"同步命令時發生錯誤：{error}", exc_info=True)

    async def close(self) -> None:
        """
        機器人關閉時一併關閉資料庫連線。
        """
        await close_db()
        await super().close()


bot = HoneypotBot(command_prefix="!", intents=intents)


@bot.event
async def on_ready() -> None:
    """
    機器人（重新）上線後印出狀態訊息。Cog 載入與指令同步已在 setup_hook 執行過，這裡不重複處理。
    """
    print(f"Bot 已上線：{bot.user}")


async def main() -> None:
    """
    啟動機器人。
    """
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
