import datetime
import json
import logging
import time

import aiosqlite
import discord

from core import bot_registry, database, quota, repository, suspension
from core.capability_api import CAPABILITY_OWNERS
from sandbox.worker import execute_plugin_event

logger = logging.getLogger(__name__)

EXECUTION_TIMEOUT_SECONDS = 2  # 暫定值，待第二階段實測後校準，見 design.md 第 5.4 節
MAX_ACTIONS_PER_EXECUTION = 20

# storage/schedule_task 能力在外掛執行期間就會直接寫進 SQLite（見
# core/plugin_storage_repository.py），但這次分派最後可能被 _validate_actions()
# 判定失敗，或外掛執行本身崩潰——這兩種情況代表這次執行不可信任，連同已經寫入
# 的 storage/schedule_task 都要一起回退，不能留下「授權被拒但 side effect 已經
# 發生」的不一致狀態（quota_exceeded 或個別動作執行失敗不算，那只是操作性問題，
# 不代表這次執行有問題，見 design.md 附錄 A.2.3 的說明）。
#
# 原本用「全域 asyncio.Lock() + SQLite SAVEPOINT」做這件事，但實測發現一個
# 根本性的缺陷：SQLite 的交易狀態是連線層級的，不是「哪個 coroutine 開的」
# 層級。全平台共用同一條連線，鎖只能讓「兩個都要開 SAVEPOINT 的安裝」互相排隊，
# 完全擋不住任何其他不相關的 db.commit()（例如同一次分派迴圈裡，另一個外掛
# 執行成功後呼叫的 log_execution(outcome="success")，這個呼叫在鎖釋放之後才
# 執行、不受保護）——只要在 SAVEPOINT 開著的當下，任何地方對共用連線呼叫一次
# commit()，就會把這個 SAVEPOINT 直接沖掉。改成這次分派需要用到 storage/
# schedule_task 能力時，開一條專用連線，交易狀態完全跟共用連線分開，不需要
# SAVEPOINT 也不需要全域鎖，見 design.md 第 5.4.2 節。
_STORAGE_CAPABILITY_NAMES = {"storage", "schedule_task"}
_EXECUTION_DB_BUSY_TIMEOUT_MS = 5000

_ACTION_PARAM_KEYS = {
    "send_message": ({"channel_id", "content"}, {"channel_id", "content", "embed", "buttons"}),
    "reply_message": (
        {"channel_id", "message_id", "content"},
        {"channel_id", "message_id", "content", "embed", "buttons"},
    ),
    "edit_message": ({"channel_id", "message_id", "content"}, {"channel_id", "message_id", "content", "embed"}),
    "pin_message": ({"channel_id", "message_id"}, {"channel_id", "message_id"}),
    "unpin_message": ({"channel_id", "message_id"}, {"channel_id", "message_id"}),
    "send_poll": ({"channel_id", "question", "options", "duration"}, {"channel_id", "question", "options", "duration"}),
    "delete_message": ({"channel_id", "message_id"}, {"channel_id", "message_id"}),
    "add_role": ({"user_id", "role_id"}, {"user_id", "role_id"}),
    "remove_role": ({"user_id", "role_id"}, {"user_id", "role_id"}),
    "set_nickname": ({"user_id", "nickname"}, {"user_id", "nickname"}),
    "timeout_member": ({"user_id", "duration_seconds", "reason"}, {"user_id", "duration_seconds", "reason"}),
    "create_thread": ({"channel_id", "name"}, {"channel_id", "name"}),
    "archive_thread": ({"thread_id"}, {"thread_id"}),
}


async def _open_execution_db() -> aiosqlite.Connection:
    """
    為一次有 storage/schedule_task 能力的外掛執行開一條專用連線，交易狀態
    跟平台共用的連線完全分開，見 design.md 第 5.4.2 節。

    Returns:
        新開的連線，已設定 busy_timeout；呼叫端用完後要負責 commit/rollback
        再 close()，這裡不做
    """
    execution_db = await aiosqlite.connect(database.DB_PATH)
    await execution_db.execute(f"PRAGMA busy_timeout = {_EXECUTION_DB_BUSY_TIMEOUT_MS}")
    return execution_db


async def dispatch_event(
    guild_id: int,
    event_type: str,
    event_payload: dict,
    target_plugin_id: str | None = None,
) -> bool:
    """
    把 Discord 事件分派給該伺服器已安裝、有訂閱這個事件的外掛執行。

    流程：停權檢查 → 執行次數配額檢查 → 建立沙箱執行 → 驗證動作清單 →
    動作次數配額檢查 → 真正執行動作 → 記錄稽核紀錄。

    Args:
        guild_id: 伺服器 ID
        event_type: 事件名稱，對應附錄 A 定義的事件
        event_payload: 事件資料
        target_plugin_id: 若指定，只分派給該外掛；仍會保留停權、啟用狀態、事件訂閱與配額檢查

    Returns:
        True 表示至少一個目標外掛成功完成；若沒有目標外掛成功則回傳 False
    """
    installations = await repository.get_enabled_installations_for_guild(guild_id)
    has_successful_execution = False

    for installation in installations:
        plugin_id = installation["plugin_id"]

        if target_plugin_id is not None and plugin_id != target_plugin_id:
            continue

        if not _installation_handles_event(installation, event_type):
            continue

        if suspension.is_suspended(plugin_id):
            continue  # 已停權，完全不建立沙箱，不消耗任何執行資源

        if not await quota.check_and_consume_execution_quota(guild_id, plugin_id):
            await repository.log_execution(guild_id, plugin_id, event_type, "[]", 0, "quota_exceeded")
            continue

        source_code = await repository.get_plugin_source(plugin_id, installation["installed_version"])
        if source_code is None:
            await repository.log_execution(
                guild_id,
                plugin_id,
                event_type,
                "[]",
                0,
                "crashed",
                "找不到外掛原始碼",
            )
            continue

        granted_capabilities = set(json.loads(installation["granted_capabilities_json"]))
        resource_limits = None
        if "resource_overrides_json" in installation:
            resource_limits = await repository.resolve_resource_limits(guild_id, plugin_id)
        needs_storage_transaction = bool(granted_capabilities & _STORAGE_CAPABILITY_NAMES)
        started_at = time.monotonic()
        execution_db = None

        try:
            execution_db = await _open_execution_db() if needs_storage_transaction else None
            try:
                try:
                    actions = await execute_plugin_event(
                        guild_id=guild_id,
                        plugin_id=plugin_id,
                        source_code=source_code,
                        event_type=event_type,
                        event_payload=event_payload,
                        granted_capabilities=granted_capabilities,
                        execution_db=execution_db,
                        resource_overrides=resource_limits,
                    )
                except Exception as error:
                    execution_ms = int((time.monotonic() - started_at) * 1000)
                    logger.error(f"外掛執行失敗（plugin_id={plugin_id}）：{error}", exc_info=True)
                    if execution_db is not None:
                        await execution_db.rollback()
                    await repository.log_execution(
                        guild_id, plugin_id, event_type, "[]", execution_ms, "crashed", str(error)
                    )
                    continue

                execution_ms = int((time.monotonic() - started_at) * 1000)

                if not _validate_actions(installation, actions):
                    if execution_db is not None:
                        await execution_db.rollback()
                    await repository.log_execution(
                        guild_id, plugin_id, event_type, "[]", execution_ms, "rejected_invalid_action"
                    )
                    continue

                if execution_db is not None:
                    await execution_db.commit()
            finally:
                if execution_db is not None:
                    await execution_db.close()
        except Exception as error:
            # 這裡攔到的是專用連線本身的基礎設施問題（_open_execution_db() 連線失敗、
            # 或 commit()/rollback()/close() 在連線中途真正壞掉時失敗），不是外掛執行
            # 或動作驗證的例外（那兩種已經在上面內層處理掉）。這類例外原本完全沒有
            # 保護，會直接炸穿 dispatch_event()，導致 for 迴圈裡這次分派後面所有其他
            # 安裝都不會被處理——跟 design.md 第 5.4.2 節「crashed 應該回退且被記錄」
            # 的既定行為不一致，也跟第 12.3 節第 5 點修好的 _execute_actions() 例外
            # 保護是同一種基礎設施層級的坑，這裡比照同樣的處理方式：記錄 crashed 並
            # continue 到下一個安裝，不讓它擴大成影響整批分派的問題。
            execution_ms = int((time.monotonic() - started_at) * 1000)
            logger.error(f"執行專用資料庫連線發生錯誤（plugin_id={plugin_id}）：{error}", exc_info=True)
            await repository.log_execution(
                guild_id, plugin_id, event_type, "[]", execution_ms, "crashed", str(error)
            )
            continue

        if actions and not await quota.check_and_consume_action_quota(guild_id, plugin_id, len(actions)):
            await repository.log_execution(guild_id, plugin_id, event_type, "[]", execution_ms, "quota_exceeded")
            continue

        try:
            action_errors = await _execute_actions(guild_id, actions)
        except Exception as error:
            # _execute_actions() 本身已經把每個動作的例外個別接住記錄，不會往外拋；
            # 這裡攔到的是更底層的基礎設施問題（最明確的例子是 bot_registry.get_bot()
            # 在 Cog 還沒載入完成前被呼叫，丟出 RuntimeError）。原本這裡完全沒保護，
            # 會直接炸穿 dispatch_event()，導致 for 迴圈裡後面所有其他安裝都不會被
            # 處理，不只是這一個安裝失敗，範圍比表面看起來大，一定要接住。
            logger.error(f"執行動作清單失敗（plugin_id={plugin_id}）：{error}", exc_info=True)
            await repository.log_execution(
                guild_id, plugin_id, event_type, "[]", execution_ms, "crashed", str(error)
            )
            continue

        await repository.log_execution(
            guild_id,
            plugin_id,
            event_type,
            json.dumps(actions, ensure_ascii=False),
            execution_ms,
            "success",
            json.dumps({"action_errors": action_errors}, ensure_ascii=False) if action_errors else None,
        )
        has_successful_execution = True

    return has_successful_execution


def _validate_actions(installation: dict, actions: list[dict]) -> bool:
    """
    驗證沙箱回傳的動作清單是否都在這次安裝授權的能力範圍內。

    Args:
        installation: 安裝紀錄
        actions: 沙箱回傳的動作清單

    Returns:
        True 表示全部通過驗證
    """
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS_PER_EXECUTION:
        return False

    granted_capabilities = set(json.loads(installation["granted_capabilities_json"]))
    for action in actions:
        if not isinstance(action, dict) or set(action.keys()) != {"type", "params"}:
            return False
        action_type = action.get("type")
        params = action.get("params")
        if not isinstance(action_type, str) or not isinstance(params, dict):
            return False
        if action_type not in _ACTION_HANDLERS:
            return False
        required_capability = CAPABILITY_OWNERS.get(action_type)
        if required_capability is None or required_capability not in granted_capabilities:
            return False
        required_keys, allowed_keys = _ACTION_PARAM_KEYS[action_type]
        param_keys = set(params.keys())
        if not required_keys.issubset(param_keys) or not param_keys.issubset(allowed_keys):
            return False
        if not _validate_action_param_values(action_type, params):
            return False
    return True


def _is_int_id(value: object) -> bool:
    """
    檢查 Discord snowflake 參數是否為正整數，避免 bool 被當成 int 通過。
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_optional_dict(value: object) -> bool:
    """
    檢查可省略的 embed 參數型別。
    """
    return value is None or isinstance(value, dict)


def _is_optional_buttons(value: object) -> bool:
    """
    檢查可省略的 buttons 參數型別。
    """
    if value is None:
        return True
    return isinstance(value, list) and all(isinstance(button, dict) for button in value)


def _validate_action_param_values(action_type: str, params: dict) -> bool:
    """
    驗證各延後動作參數的基本型別與安全範圍。
    """
    id_fields = {
        "channel_id",
        "message_id",
        "user_id",
        "role_id",
        "thread_id",
    }
    for field in id_fields & set(params.keys()):
        if not _is_int_id(params[field]):
            return False

    if "content" in params and params["content"] is not None and not isinstance(params["content"], str):
        return False
    if "embed" in params and not _is_optional_dict(params["embed"]):
        return False
    if "buttons" in params and not _is_optional_buttons(params["buttons"]):
        return False
    if action_type == "send_poll":
        return (
            isinstance(params["question"], str)
            and isinstance(params["options"], list)
            and 2 <= len(params["options"]) <= 10
            and all(isinstance(option, str) and option for option in params["options"])
            and isinstance(params["duration"], int)
            and not isinstance(params["duration"], bool)
            and 1 <= params["duration"] <= 168
        )
    if action_type == "set_nickname":
        return params["nickname"] is None or isinstance(params["nickname"], str)
    if action_type == "timeout_member":
        return (
            isinstance(params["duration_seconds"], int)
            and not isinstance(params["duration_seconds"], bool)
            and 1 <= params["duration_seconds"] <= 2_419_200
            and (params["reason"] is None or isinstance(params["reason"], str))
        )
    if action_type == "create_thread":
        return isinstance(params["name"], str) and bool(params["name"])
    return True


def _installation_handles_event(installation: dict, event_type: str) -> bool:
    """
    檢查安裝紀錄對應的 manifest 是否訂閱指定事件。

    Args:
        installation: 安裝紀錄，需包含 manifest_json
        event_type: 事件名稱

    Returns:
        True 表示該外掛訂閱此事件
    """
    try:
        manifest_data = json.loads(installation["manifest_json"])
    except (KeyError, json.JSONDecodeError) as error:
        logger.error(f"解析外掛 manifest 失敗：{error}", exc_info=True)
        return False
    return event_type in manifest_data.get("event_hooks", [])


async def _execute_actions(guild_id: int, actions: list[dict]) -> list[dict]:
    """
    依序真正執行動作清單裡的每個動作，呼叫真正的 Discord API。

    單一動作失敗（例如頻道/成員/訊息已經不存在）只記錄錯誤並繼續處理其餘動作，
    不會因為其中一項失敗就讓整批已通過驗證的動作全部中止。

    Args:
        guild_id: 伺服器 ID
        actions: 已通過驗證的動作清單

    Returns:
        action-level 錯誤摘要清單；空清單代表全部動作都沒有拋出例外
    """
    bot = bot_registry.get_bot()
    guild = bot.get_guild(guild_id)
    action_errors = []
    if guild is None:
        logger.error(f"執行動作時找不到伺服器（guild_id={guild_id}），機器人可能已被移出")
        return [{"index": None, "type": None, "error": "找不到伺服器"}]

    for index, action in enumerate(actions):
        handler = _ACTION_HANDLERS.get(action["type"])
        if handler is None:
            logger.error(f"未知的動作類型，略過（guild_id={guild_id}，action={action}）")
            action_errors.append({"index": index, "type": action["type"], "error": "未知的動作類型"})
            continue
        try:
            await handler(guild, action["params"])
        except Exception as error:
            logger.error(f"執行動作失敗（guild_id={guild_id}，action={action}）：{error}", exc_info=True)
            action_errors.append({"index": index, "type": action["type"], "error": str(error)})
    return action_errors


def _build_embed(embed_params: dict | None) -> discord.Embed | None:
    """
    把能力函式傳來的 embed 參數轉成 discord.Embed，對應附錄 A.2.1 的欄位（標題/描述/顏色/圖片網址）。

    Args:
        embed_params: dict 或 None

    Returns:
        discord.Embed，embed_params 是 None 時回傳 None
    """
    if not embed_params:
        return None
    embed = discord.Embed(
        title=embed_params.get("title"),
        description=embed_params.get("description"),
        color=embed_params.get("color"),
    )
    image_url = embed_params.get("image_url")
    if image_url:
        embed.set_image(url=image_url)
    return embed


def _build_view(buttons_params: list | None) -> discord.ui.View | None:
    """
    把能力函式傳來的 buttons 參數轉成 discord.ui.View。

    Args:
        buttons_params: list of {"label": str, "custom_id": str, "style": str}，或 None

    Returns:
        discord.ui.View，buttons_params 是 None 或空清單時回傳 None
    """
    if not buttons_params:
        return None
    view = discord.ui.View(timeout=None)
    for button_params in buttons_params:
        style = getattr(discord.ButtonStyle, button_params.get("style", "primary"), discord.ButtonStyle.primary)
        view.add_item(
            discord.ui.Button(
                label=button_params.get("label", ""),
                style=style,
                custom_id=button_params.get("custom_id"),
            )
        )
    return view


async def _handle_send_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    await channel.send(
        content=params.get("content"),
        embed=_build_embed(params.get("embed")),
        view=_build_view(params.get("buttons")),
    )


async def _handle_reply_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.reply(
        content=params.get("content"),
        embed=_build_embed(params.get("embed")),
        view=_build_view(params.get("buttons")),
    )


async def _handle_edit_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.edit(content=params.get("content"), embed=_build_embed(params.get("embed")))


async def _handle_pin_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.pin()


async def _handle_unpin_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.unpin()


async def _handle_send_poll(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    poll = discord.Poll(question=params["question"], duration=datetime.timedelta(hours=params["duration"]))
    for option in params["options"]:
        poll.add_answer(text=option)
    await channel.send(poll=poll)


async def _handle_delete_message(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    message = await channel.fetch_message(params["message_id"])
    await message.delete()


async def _handle_add_role(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    role = guild.get_role(params["role_id"])
    if member is None or role is None:
        return
    await member.add_roles(role)


async def _handle_remove_role(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    role = guild.get_role(params["role_id"])
    if member is None or role is None:
        return
    await member.remove_roles(role)


async def _handle_set_nickname(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    if member is None:
        return
    await member.edit(nick=params["nickname"])


async def _handle_timeout_member(guild: discord.Guild, params: dict) -> None:
    member = guild.get_member(params["user_id"])
    if member is None:
        return
    until = discord.utils.utcnow() + datetime.timedelta(seconds=params["duration_seconds"])
    await member.timeout(until, reason=params.get("reason"))


async def _handle_create_thread(guild: discord.Guild, params: dict) -> None:
    channel = guild.get_channel(params["channel_id"])
    if channel is None:
        return
    await channel.create_thread(name=params["name"], type=discord.ChannelType.public_thread)


async def _handle_archive_thread(guild: discord.Guild, params: dict) -> None:
    thread = guild.get_thread(params["thread_id"])
    if thread is None:
        return
    await thread.edit(archived=True)


_ACTION_HANDLERS = {
    "send_message": _handle_send_message,
    "reply_message": _handle_reply_message,
    "edit_message": _handle_edit_message,
    "pin_message": _handle_pin_message,
    "unpin_message": _handle_unpin_message,
    "send_poll": _handle_send_poll,
    "delete_message": _handle_delete_message,
    "add_role": _handle_add_role,
    "remove_role": _handle_remove_role,
    "set_nickname": _handle_set_nickname,
    "timeout_member": _handle_timeout_member,
    "create_thread": _handle_create_thread,
    "archive_thread": _handle_archive_thread,
}
