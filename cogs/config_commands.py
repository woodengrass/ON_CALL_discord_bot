import discord
from discord import app_commands
from discord.ext import commands
import os
import json

CONFIG_PATH = "config/honeypot_config.json"
GUILD_ID = 1399108525954957442  # 替换为你的测试服务器ID
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
    @app_commands.command(name="设置蜜罐", description="将当前频道设为蜜罐频道")
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
    @app_commands.command(name="设置公告", description="将当前频道设为公告频道")
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
    @app_commands.command(name="加入白名单", description="添加某人到白名单")
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
    @app_commands.command(name="移除白名单", description="将某人移出白名单")
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
    @app_commands.command(name="查看配置", description="查看当前服务器配置")
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

async def setup(bot):
    await bot.add_cog(ConfigCommands(bot))
