import discord
from discord.ext import commands

# 關鍵字對應回覆
keyword_triggers = {
    "蛤？": "你在说什么？",
    "喵": "喵喵喵~",
    "菜包": "谁叫我？",
    "破防": "我破防了",
    "谁是菜包作者？": "那肯定是一個真正的鸽子aka咕咕咕大神aka晚期懒癌患者",
    "木头草是：": "你爹",
    "Who is CaiBao?": "用英文装你m那？！",
}

class TextTriggers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # 忽略 bot 自己
        if message.author.bot:
            return

        content = message.content.strip()
        if content in keyword_triggers:
            await message.channel.send(keyword_triggers[content])
            print(f"[关键词触发] {message.author}：{content}")

async def setup(bot):
    await bot.add_cog(TextTriggers(bot))