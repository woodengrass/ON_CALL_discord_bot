import discord
from discord import app_commands
from discord.ext import commands
import os
import json

CONFIG_PATH = "config/honeypot_config.json"
GUILD_ID = 1399108525954957442
GUILD_OBJ = discord.Object(id=GUILD_ID)

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return []
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def get_entry(config, guild_id):
    for entry in config:
        if entry["guild_id"] == str(guild_id):
            return entry
    return None

class ConfigCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="set_honeypot", description="将当前频道设为蜜罐频道")
    async def set_honeypot(self, interaction: discord.Interaction):
        config = load_config()
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)
        entry = get_entry(config, guild_id)

        if entry:
            entry["honeypot_channel"] = channel_id
        else:
            entry = {
                "guild_id": guild_id,
                "honeypot_channel": channel_id,
                "announcement_channel": "",
                "whitelist_ids": []
            }
            config.append(entry)

        save_config(config)
        await interaction.response.send_message("✅ 当前频道已设为蜜罐频道", ephemeral=True)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="set_announcement", description="将当前频道设为公告频道")
    async def set_announcement(self, interaction: discord.Interaction):
        config = load_config()
        guild_id = str(interaction.guild.id)
        channel_id = str(interaction.channel.id)
        entry = get_entry(config, guild_id)

        if entry:
            entry["announcement_channel"] = channel_id
        else:
            entry = {
                "guild_id": guild_id,
                "honeypot_channel": "",
                "announcement_channel": channel_id,
                "whitelist_ids": []
            }
            config.append(entry)

        save_config(config)
        await interaction.response.send_message("✅ 当前频道已设为公告频道", ephemeral=True)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="add_whitelist", description="添加某人到白名单")
    async def add_whitelist(self, interaction: discord.Interaction, user: discord.Member):
        config = load_config()
        guild_id = str(interaction.guild.id)
        entry = get_entry(config, guild_id)

        if not entry:
            await interaction.response.send_message("⚠️ 请先设置蜜罐和公告频道", ephemeral=True)
            return

        if str(user.id) not in entry["whitelist_ids"]:
            entry["whitelist_ids"].append(str(user.id))
            save_config(config)
            await interaction.response.send_message(f"✅ 已将 {user.mention} 添加到白名单", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {user.mention} 已在白名单中", ephemeral=True)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="remove_whitelist", description="将某人移出白名单")
    async def remove_whitelist(self, interaction: discord.Interaction, user: discord.Member):
        config = load_config()
        guild_id = str(interaction.guild.id)
        entry = get_entry(config, guild_id)

        if not entry:
            await interaction.response.send_message("⚠️ 配置未找到", ephemeral=True)
            return

        if str(user.id) in entry["whitelist_ids"]:
            entry["whitelist_ids"].remove(str(user.id))
            save_config(config)
            await interaction.response.send_message(f"✅ 已将 {user.mention} 移出白名单", ephemeral=True)
        else:
            await interaction.response.send_message(f"ℹ️ {user.mention} 不在白名单中", ephemeral=True)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="view_config", description="查看当前服务器配置")
    async def view_config(self, interaction: discord.Interaction):
        config = load_config()
        guild_id = str(interaction.guild.id)
        entry = get_entry(config, guild_id)

        if not entry:
            await interaction.response.send_message("⚠️ 当前服务器没有配置记录", ephemeral=True)
            return

        content = (
            f"📄 **配置预览**\n"
            f"- 蜜罐频道 ID: `{entry['honeypot_channel']}`\n"
            f"- 公告频道 ID: `{entry['announcement_channel']}`\n"
            f"- 白名单: {', '.join(f'<@{uid}>' for uid in entry['whitelist_ids']) or '无'}"
        )
        await interaction.response.send_message(content, ephemeral=True)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.command(name="view_banned_texts", description="查看所有被蜜罐封禁過的訊息內容")
    async def view_banned_texts(self, interaction: discord.Interaction):
        monitor_cog = self.bot.get_cog("HoneypotMonitor")
        if monitor_cog is None:
            await interaction.response.send_message("❌ 找不到 Honeypot 模組", ephemeral=True)
            return

        texts = monitor_cog.get_all_banned_texts()
        if not texts:
            await interaction.response.send_message("✅ 目前沒有任何蜜罐封禁訊息紀錄", ephemeral=True)
            return

        # 限制 Discord 訊息長度（最大 2000 字）
        output = "\n".join(f"{i+1}. {t[:150].replace('`', 'ˋ')}" for i, t in enumerate(texts))
        if len(output) > 1900:
            output = output[:1900] + "\n...（內容過多已截斷）"

        await interaction.response.send_message(f"📄 **All Banned Texts:**\n{output}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ConfigCommands(bot))