"""
掛在既有 discord.py bot 上的事件監聽器，轉發進 core/dispatcher.py。
第二階段開發重點（接上真正的 bot），見 design.md 第二階段。
"""

import asyncio
import datetime
import json
import logging

import discord
from discord.ext import commands, tasks

from core import bot_registry, message_cache, plugin_storage_repository, quota, repository, suspension
from core.database import get_db
from core.dispatcher import dispatch_event

logger = logging.getLogger(__name__)

MESSAGE_CACHE_EVENTS = {"on_message_edit", "on_message_delete"}
MAX_SCHEDULED_TASK_DISPATCH_CONCURRENCY = 10


class PluginPlatformListeners(commands.Cog):
    """
    監聽 Discord 事件並轉發給外掛平台的 dispatcher。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        """
        載入 Cog 時同步停權快取並啟動背景任務。
        """
        await suspension.refresh_from_database(get_db())
        await suspension.start_refresh_loop(get_db())
        self.scheduled_task_loop.start()

    async def cog_unload(self) -> None:
        """
        卸載 Cog 時停止排程任務消費迴圈。
        """
        self.scheduled_task_loop.cancel()
        await suspension.stop_refresh_loop()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        轉發訊息事件給 dispatcher，交由已安裝且訂閱此事件的外掛處理。

        Args:
            message: Discord 訊息物件
        """
        if message.author.bot or not message.guild:
            return
        if await repository.guild_has_event_subscription(message.guild.id, MESSAGE_CACHE_EVENTS):
            message_cache.cache_message(
                guild_id=message.guild.id,
                channel_id=message.channel.id,
                message_id=message.id,
                author_id=message.author.id,
                content=message.content,
            )
        await dispatch_event(
            message.guild.id,
            "on_message",
            {
                "message_id": message.id,
                "author_id": message.author.id,
                "channel_id": message.channel.id,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
            },
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """
        轉發按鈕與選單互動事件給 dispatcher。

        Args:
            interaction: Discord 互動物件
        """
        if interaction.guild is None:
            return
        interaction_data = interaction.data if isinstance(interaction.data, dict) else {}
        if interaction.type == discord.InteractionType.application_command:
            await dispatch_event(
                interaction.guild.id,
                "on_slash_command",
                {
                    "command_name": interaction_data.get("name"),
                    "options": interaction_data.get("options", []),
                    "invoking_user_id": interaction.user.id,
                    "channel_id": interaction.channel.id if interaction.channel else None,
                },
            )
            return
        if interaction.type != discord.InteractionType.component:
            return
        await dispatch_event(
            interaction.guild.id,
            "on_interaction",
            {
                "interaction_type": _get_interaction_component_type(interaction_data),
                "custom_id": interaction_data.get("custom_id"),
                "values": interaction_data.get("values"),
                "invoking_user_id": interaction.user.id,
                "message_id": interaction.message.id if interaction.message else None,
            },
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """
        轉發成員加入事件給 dispatcher。

        Args:
            member: 加入伺服器的成員
        """
        await dispatch_event(
            member.guild.id,
            "on_member_join",
            {
                "user_id": member.id,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            },
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """
        轉發成員離開事件給 dispatcher。

        Args:
            member: 離開伺服器的成員
        """
        await dispatch_event(
            member.guild.id,
            "on_member_leave",
            {
                "user_id": member.id,
                "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            },
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """
        使用 raw 事件轉發訊息編輯，避免 discord.py 快取未命中時漏事件。

        Args:
            payload: Discord raw message update payload
        """
        if payload.guild_id is None:
            return
        cached_message = message_cache.get_cached_message(payload.channel_id, payload.message_id)
        await dispatch_event(
            payload.guild_id,
            "on_message_edit",
            {
                "message_id": payload.message_id,
                "channel_id": payload.channel_id,
                "author_id": cached_message["author_id"] if cached_message else None,
                "old_content": cached_message["content"] if cached_message else None,
                "new_content": payload.data.get("content"),
                "edited_at": _normalize_discord_timestamp(payload.data.get("edited_timestamp")),
            },
        )
        if cached_message and payload.data.get("content") is not None:
            message_cache.cache_message(
                guild_id=payload.guild_id,
                channel_id=payload.channel_id,
                message_id=payload.message_id,
                author_id=cached_message["author_id"],
                content=payload.data["content"],
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        """
        使用 raw 事件轉發訊息刪除，避免 discord.py 快取未命中時漏事件。

        Args:
            payload: Discord raw message delete payload
        """
        if payload.guild_id is None:
            return
        cached_message = message_cache.get_cached_message(payload.channel_id, payload.message_id)
        await dispatch_event(
            payload.guild_id,
            "on_message_delete",
            {
                "message_id": payload.message_id,
                "channel_id": payload.channel_id,
                "author_id": cached_message["author_id"] if cached_message else None,
                "content": cached_message["content"] if cached_message else None,
                "deleted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
        )
        message_cache.remove_message(payload.channel_id, payload.message_id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """
        機器人被移出伺服器時清理該伺服器的所有外掛相關資料：安裝紀錄、排程任務、
        KV 儲存、訊息快取。不清的話這些資料會永久殘留在資料庫，機器人再也接觸
        不到這個伺服器、也沒有任何操作介面能清掉它們。

        Args:
            guild: 被移除的伺服器
        """
        deleted_plugin_ids = await repository.delete_all_installations_for_guild(guild.id)
        await plugin_storage_repository.delete_all_storage_for_guild(guild.id)
        quota.clear_guild_usage(guild.id)
        message_cache.purge_guild(guild.id)
        if deleted_plugin_ids:
            logger.info(f"伺服器 {guild.id} 已移除，清理外掛安裝：{deleted_plugin_ids}")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """
        轉發語音狀態變更事件給 dispatcher。

        Args:
            member: 狀態變更的成員
            before: 變更前語音狀態
            after: 變更後語音狀態
        """
        await dispatch_event(
            member.guild.id,
            "on_voice_state_update",
            {
                "user_id": member.id,
                "before_channel_id": before.channel.id if before.channel else None,
                "after_channel_id": after.channel.id if after.channel else None,
            },
        )

    @tasks.loop(minutes=1)
    async def scheduled_task_loop(self) -> None:
        """
        每分鐘消費已到期的外掛排程任務，並轉發成 on_scheduled_task 事件。
        """
        await self.bot.wait_until_ready()
        message_cache.prune_expired()
        await self.consume_pending_guild_notifications()
        await self.consume_due_scheduled_tasks()

    async def consume_pending_guild_notifications(self) -> None:
        """
        消費停權/封鎖連鎖效應產生的伺服器通知，由 bot 行程實際送出 Discord 訊息。
        """
        notifications = await repository.get_pending_guild_notifications()
        for notification in notifications:
            try:
                guild = self.bot.get_guild(notification["guild_id"])
                if guild is None or guild.system_channel is None:
                    logger.warning(f"找不到可送通知的系統頻道：guild_id={notification['guild_id']}")
                    await repository.mark_guild_notification_sent(notification["notification_id"])
                    continue
                payload = json.loads(notification["payload_json"])
                await guild.system_channel.send(_format_guild_notification(notification["notification_type"], payload))
                await repository.mark_guild_notification_sent(notification["notification_id"])
            except Exception as error:
                logger.error(f"送出伺服器通知失敗：{error}", exc_info=True)

    async def consume_due_scheduled_tasks(self) -> None:
        """
        消費目前所有已到期的外掛排程任務。
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        due_tasks = await repository.get_due_scheduled_tasks(now.isoformat())
        semaphore = asyncio.Semaphore(MAX_SCHEDULED_TASK_DISPATCH_CONCURRENCY)
        await asyncio.gather(
            *[self._consume_due_scheduled_task(scheduled_task, semaphore) for scheduled_task in due_tasks]
        )

    async def _consume_due_scheduled_task(self, scheduled_task: dict, semaphore: asyncio.Semaphore) -> None:
        """
        消費單一到期排程任務，外層用 semaphore 控制併發數。

        Args:
            scheduled_task: repository 回傳的排程任務資料
            semaphore: 排程分派併發限制
        """
        async with semaphore:
            try:
                task_payload = json.loads(scheduled_task["payload_json"])
                if not _manifest_handles_scheduled_task(scheduled_task.get("manifest_json")):
                    await repository.delete_scheduled_task(scheduled_task["task_id"])
                    return
                dispatch_succeeded = await dispatch_event(
                    scheduled_task["guild_id"],
                    "on_scheduled_task",
                    {
                        "task_name": task_payload["task_name"],
                        "payload": task_payload["payload"],
                    },
                    target_plugin_id=scheduled_task["plugin_id"],
                )
                if not dispatch_succeeded:
                    return
                recurring_interval_seconds = scheduled_task["recurring_interval_seconds"]
                if recurring_interval_seconds is None:
                    await repository.delete_scheduled_task(scheduled_task["task_id"])
                else:
                    next_run_at = _calculate_next_run_at(scheduled_task["run_at"], recurring_interval_seconds)
                    await repository.update_scheduled_task_run_at(scheduled_task["task_id"], next_run_at)
            except Exception as error:
                logger.error(f"處理外掛排程任務失敗：{error}", exc_info=True)


def _get_interaction_component_type(interaction_data: dict) -> str:
    """
    將 Discord component_type 轉成外掛 API 使用的互動類型文字。

    Args:
        interaction_data: Discord interaction data dict

    Returns:
        button、select_menu 或 unknown
    """
    component_type = interaction_data.get("component_type")
    if component_type == 2:
        return "button"
    if component_type in {3, 5, 6, 7, 8}:
        return "select_menu"
    return "unknown"


def _format_guild_notification(notification_type: str, payload: dict) -> str:
    """
    將通知資料格式化成要送到伺服器 system_channel 的文字。

    Args:
        notification_type: 通知類型
        payload: 通知 payload

    Returns:
        Discord 訊息文字
    """
    plugin_id = payload.get("plugin_id", "unknown")
    if notification_type == "plugin_banned":
        reason = payload.get("reason") or "未提供原因"
        return f"外掛 {plugin_id} 已被平台永久封鎖並自動解除安裝。原因：{reason}"
    if notification_type == "plugin_suspended":
        return f"外掛 {plugin_id} 已被平台停權並自動解除安裝。"
    if notification_type == "plugin_uninstalled":
        return f"外掛 {plugin_id} 已解除安裝，其儲存的資料已一併清除。"
    return f"外掛平台通知：{notification_type}"


def _normalize_discord_timestamp(value: object) -> str | None:
    """
    將 Discord payload 的時間欄位標準化成平台內部使用的 ISO 8601 字串。

    Args:
        value: Discord raw payload 內的時間值

    Returns:
        ISO 8601 字串；無法解析時回傳 None
    """
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.error(f"Discord 編輯時間格式不合法：{value}")
            return None
    else:
        logger.error(f"Discord 編輯時間型別不合法：{type(value).__name__}")
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.timezone.utc)
    return timestamp.astimezone(datetime.timezone.utc).isoformat()


def _calculate_next_run_at(run_at: str, recurring_interval_seconds: int) -> str:
    """
    計算週期性任務下一次執行時間。

    Args:
        run_at: 這次到期時間 ISO 8601 字串
        recurring_interval_seconds: 週期秒數

    Returns:
        下一次執行時間 ISO 8601 字串
    """
    current_run_at = datetime.datetime.fromisoformat(run_at)
    return (current_run_at + datetime.timedelta(seconds=recurring_interval_seconds)).isoformat()


def _manifest_handles_scheduled_task(manifest_json: str | None) -> bool:
    """
    檢查排程任務所屬外掛目前版本是否仍訂閱 on_scheduled_task。

    Args:
        manifest_json: 外掛 manifest JSON 字串

    Returns:
        True 表示仍可分派排程任務
    """
    if manifest_json is None:
        return False
    try:
        manifest_data = json.loads(manifest_json)
    except json.JSONDecodeError as error:
        logger.error(f"解析排程任務 manifest 失敗：{error}", exc_info=True)
        return False
    return "on_scheduled_task" in manifest_data.get("event_hooks", [])


async def setup(bot: commands.Bot) -> None:
    bot_registry.set_bot(bot)
    await bot.add_cog(PluginPlatformListeners(bot))
