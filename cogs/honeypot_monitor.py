from discord.ext import commands
import discord

class HoneypotMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # {user_id: set(message_content)} 记录用户在蜜罐频道发过的消息内容
        self.user_messages = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return  # 跳过机器人消息

        # 遍历所有服务器配置
        for entry in getattr(self.bot, "honeypots", []):
            try:
                honeypot_id = int(entry["honeypot_channel"])
                announcement_id = int(entry["announcement_channel"])
                whitelist = set(entry.get("whitelist_ids", []))
            except Exception as e:
                print(f"[ERROR] 配置格式错误: {e}")
                continue

            author_id_str = str(message.author.id)

            # 白名单跳过
            if author_id_str in whitelist:
                # print(f"[INFO] 用户 {message.author} 在白名单，跳过检查")
                return

            # 1. 蜜罐频道内消息处理
            if message.channel.id == honeypot_id:
                # 记录内容
                user_set = self.user_messages.setdefault(message.author.id, set())
                user_set.add(message.content)

                # 删除消息
                try:
                    await message.delete()
                    print(f"[INFO] 删除蜜罐频道消息: {message.content} 来自 {message.author}")
                except Exception as e:
                    print(f"[ERROR] 删除消息失败: {e}")

                # 发送违规公告
                await self._announce_violation(announcement_id, message.author, message.content)

                # 尝试封禁用户
                try:
                    await message.guild.ban(message.author, reason="触发蜜罐频道违规")
                    print(f"[INFO] 封禁用户: {message.author} (ID: {message.author.id})")
                except Exception as e:
                    print(f"[ERROR] 封禁失败: {e}")

                return  # 不再继续检查其他配置

            # 2. 非蜜罐频道消息，检查是否重复蜜罐内容
            if message.author.id in self.user_messages and message.content in self.user_messages[message.author.id]:
                # 删除消息
                try:
                    await message.delete()
                    print(f"[INFO] 删除非蜜罐频道重复消息: {message.content} 来自 {message.author}")
                except Exception as e:
                    print(f"[ERROR] 删除消息失败: {e}")

                # 发送违规公告
                await self._announce_violation(announcement_id, message.author, message.content)

                # 尝试封禁用户
                try:
                    await message.guild.ban(message.author, reason="非蜜罐频道发送蜜罐消息")
                    print(f"[INFO] 封禁用户: {message.author} (ID: {message.author.id})")
                except Exception as e:
                    print(f"[ERROR] 封禁失败: {e}")

                return  # 不继续检查其他配置

    async def _announce_violation(self, channel_id, user, content):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"[WARN] 公告频道ID {channel_id} 未找到")
            return

        safe_content = content[:200].replace("`", "ˋ")
        try:
            await channel.send(
                f"⚠️ **Honeypot Triggered!**\n"
                f"👤 用户 {user.mention} 发送了违规信息\n"
                f"💬 消息内容：`{safe_content}`"
            )
            print(f"[INFO] 已在公告频道发送违规通知")
        except Exception as e:
            print(f"[ERROR] 发送公告失败: {e}")

async def setup(bot):
    await bot.add_cog(HoneypotMonitor(bot))
