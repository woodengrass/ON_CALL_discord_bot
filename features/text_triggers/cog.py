import logging
import random

import discord
from discord import app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from core.i18n import i18n
from features.text_triggers.panel import TriggerSettingView
from features.text_triggers.repository import get_all_triggers

logger = logging.getLogger(__name__)


class TextTriggers(commands.Cog):
    """
    監聽訊息內容，若符合伺服器設定的觸發詞則自動回覆對應內容。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cache: dict[int, dict] = {}

    async def cog_load(self) -> None:
        """
        Cog 載入時（此時資料庫已由 bot 的 setup_hook 初始化完成），從資料庫建立觸發詞快取。
        """
        await self.reload_triggers()

    async def reload_triggers(self) -> None:
        """
        從資料庫重新載入快取，區分精確比對與模糊比對兩種類型。
        """
        raw_data = await get_all_triggers()
        new_cache = {}

        for guild_id, guild_triggers in raw_data.items():
            new_cache[guild_id] = {"exact": {}, "wildcard": []}

            for trigger, trigger_config in guild_triggers.items():
                response = trigger_config["response"]
                is_wildcard = trigger_config.get("wildcard", False)

                if is_wildcard:
                    new_cache[guild_id]["wildcard"].append((trigger, response))
                else:
                    new_cache[guild_id]["exact"][trigger] = response

        self.cache = new_cache
        print("[資訊] 觸發詞快取已更新")

    def _format_response(self, text: str, message: discord.Message) -> str:
        """
        將回覆文字中的自訂變數替換為實際內容。

        Args:
            text: 含有變數的原始回覆文字
            message: 觸發訊息物件

        Returns:
            替換完成後的文字
        """
        replacements = {
            "[user]": message.author.mention,
            "[username]": message.author.display_name,
            "[server]": message.guild.name,
            "[channel]": message.channel.mention
        }

        for key, value in replacements.items():
            text = text.replace(key, str(value))

        return text

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        監聽訊息，若內容符合觸發詞則發送對應回覆。

        Args:
            message: 收到的訊息物件
        """
        if message.author.bot or not message.guild:
            return
        guild_id = message.guild.id
        if guild_id not in self.cache:
            return

        content = message.content.strip()
        if not content:
            return

        triggers = self.cache[guild_id]
        response = None

        # 1. 精確比對
        if content in triggers["exact"]:
            response = triggers["exact"][content]

        # 2. 模糊比對 (Wildcard)
        if not response:
            for trigger_word, wildcard_response in triggers["wildcard"]:
                if trigger_word in content:
                    response = wildcard_response
                    break

        if response:
            final_response = response
            # 隨機回覆處理
            if isinstance(response, list) and len(response) > 0:
                final_response = random.choice(response)

            # 確保是字串
            if not isinstance(final_response, str):
                return

            # 執行變數替換
            final_response = self._format_response(final_response, message)

            if not message.channel.permissions_for(message.guild.me).send_messages:
                return

            try:
                await message.channel.send(final_response)
            except discord.HTTPException as error:
                logger.error(f"發送觸發詞回覆失敗：{error}", exc_info=True)


class TextTriggerCommands(commands.Cog):
    """提供文字觸發詞設定入口。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="trigger_setting", description=locale_str("trigger_setting"))
    async def trigger_setting(self, interaction: discord.Interaction) -> None:
        """顯示文字觸發詞設定面板。"""
        view = TriggerSettingView(interaction.guild.id, self.bot)
        embed = discord.Embed(
            title=i18n.get_text("messages.trigger_setting_title", interaction.guild.id),
            description=i18n.get_text("messages.trigger_setting_desc", interaction.guild.id),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TextTriggers(bot))
    await bot.add_cog(TextTriggerCommands(bot))

