import discord
from discord.ext import commands
import json
import os

TRIGGER_FILE = "config/trigger.json"

def load_triggers():
    if not os.path.exists(TRIGGER_FILE):
        return {}
    try:
        with open(TRIGGER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

class TextTriggers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        data = load_triggers()
        guild_id = str(message.guild.id)
        content = message.content

        if guild_id not in data or "triggers" not in data[guild_id]:
            return

        for trigger, config in data[guild_id]["triggers"].items():
            response = config["response"]
            is_wildcard = config.get("wildcard", False)

            if (is_wildcard and trigger in content) or (not is_wildcard and content.strip() == trigger):
                await message.channel.send(response)
                break  # 只觸發第一個符合的

async def setup(bot):
    await bot.add_cog(TextTriggers(bot))
