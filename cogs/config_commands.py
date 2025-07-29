import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
import os
import json
import datetime

CONFIG_PATH = "config/honeypot_config.json"
GUILD_ID = 1399108525954957442
GUILD_OBJ = discord.Object(id=GUILD_ID)
COUNT_FILE = "config/counting.json"
WELCOME_FILE = "config/welcome_message.json"
TICKET_FILE = "config/ticket.json"

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

def load_all_counts():
    if not os.path.exists(COUNT_FILE):
        return {}
    try:
        with open(COUNT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_count(guild_id: int, channel_id: int, member_count: int):
    data = load_all_counts()
    data[str(guild_id)] = {
        "channel_id": channel_id,
        "member_count": member_count
    }
    with open(COUNT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_tickets():
    if not os.path.exists(TICKET_FILE):
        return {}
    try:
        with open(TICKET_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_tickets(data):
    with open(TICKET_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_welcome_config():
    if not os.path.exists(WELCOME_FILE):
        return {}
    try:
        with open(WELCOME_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_welcome_config(data):
    with open(WELCOME_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_panels():
    if not os.path.exists(PANEL_FILE):
        return {}
    try:
        with open(PANEL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}

def save_panels(data):
    with open(PANEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

class ConfigCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.checks.has_permissions(administrator=True)
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
    @app_commands.checks.has_permissions(administrator=True)
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
    @app_commands.checks.has_permissions(administrator=True)
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
    @app_commands.checks.has_permissions(administrator=True)
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
    @app_commands.checks.has_permissions(administrator=True)
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
    @app_commands.checks.has_permissions(administrator=True)
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

        output = "\n".join(f"{i+1}. {t[:150].replace('`', 'ˋ')}" for i, t in enumerate(texts))
        if len(output) > 1900:
            output = output[:1900] + "\n...（內容過多已截斷）"

        await interaction.response.send_message(f"**All Banned Texts:**\n{output}", ephemeral=True)

    @app_commands.guilds(GUILD_OBJ)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="people_counting", description="將此頻道設為顯示伺服器人數的頻道")
    async def people_counting(self, interaction: discord.Interaction):
        guild = interaction.guild
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ 此指令只能在文字頻道中使用", ephemeral=True)
            return

        member_count = guild.member_count
        new_name = f"人數-{member_count}"

        try:
            await channel.edit(name=new_name)
        except discord.Forbidden:
            await interaction.response.send_message("❌ 沒有修改頻道名稱的權限", ephemeral=True)
            return

        try:
            overwrite = channel.overwrites_for(guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(guild.default_role, overwrite=overwrite)
        except discord.Forbidden:
            await interaction.response.send_message("❌ 沒有修改頻道權限的權限", ephemeral=True)
            return

        save_count(guild.id, channel.id, member_count)
        await interaction.response.send_message(f"✅ 頻道名稱已更新為 `{new_name}` 並禁言所有人", ephemeral=True)
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="welcome", description="設定歡迎訊息")
    @app_commands.describe(message="自訂義歡迎訊息")
    async def set_welcome(self, interaction: discord.Interaction, message: str):
        guild_id = str(interaction.guild.id)
        channel_id = interaction.channel.id

        data = load_welcome_config()
        data[guild_id] = {
            "channel_id": channel_id,
            "message": message
        }
        save_welcome_config(data)

        await interaction.response.send_message(
            f"✅ 已設定此頻道為歡迎頻道並儲存訊息：\n{message}",
            ephemeral=True
        )
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="delete", description="刪除最近的訊息")
    @app_commands.describe(count="要刪除的訊息數量（最多100）")
    async def delete_messages(self, interaction: discord.Interaction, count: int):
        # 檢查是否有管理訊息的權限
        if not interaction.channel.permissions_for(interaction.user).manage_messages:
            await interaction.response.send_message("❌ 你沒有刪除訊息的權限", ephemeral=True)
            return

        # 檢查刪除數量是否合法
        if count <= 0 or count > 100:
            await interaction.response.send_message("⚠️ 請輸入 1～100 之間的數字", ephemeral=True)
            return

        # 延遲回覆，避免超時
        await interaction.response.defer(ephemeral=True)

        # 刪除訊息（加1是包含 slash 指令本身）
        deleted = await interaction.channel.purge(limit=count + 1)

        # 回報刪除結果
        await interaction.followup.send(f"✅ 已刪除 {len(deleted) - 1} 則訊息")  # 減1是排除 slash 指令本身
    @app_commands.guilds(GUILD_OBJ)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="ticket", description="建立客服單按鈕")
    @app_commands.describe(description="客服單介紹", reason="開單原因")
    async def ticket(self, interaction: discord.Interaction, description: str, reason: str):
        print(f"[ticket] 指令觸發 by {interaction.user}")

        from cogs.ticket_system import TicketOpenButton

        # 建立按鈕視圖
        view = TicketOpenButton(self.bot, reason)
        embed = discord.Embed(title="客服單系統", description=description, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, view=view)

        # 記錄面板到 ticket.json
        data = load_tickets()
        guild_id = str(interaction.guild.id)
        if guild_id not in data:
            data[guild_id] = {"tickets": [], "panels": []}

        # 用 original_response() 拿訊息 ID
        msg = await interaction.original_response()
        data[guild_id]["panels"].append({
            "channel_id": interaction.channel.id,
            "message_id": msg.id,
            "reason": reason
        })
        save_tickets(data)
        print(f"[ticket] 已在 {interaction.channel.id} 建立客服單面板，寫入 ticket.json")

async def setup(bot):
    await bot.add_cog(ConfigCommands(bot))