import re
import uuid

import discord

from core.i18n import i18n
from core.ui_constants import MAX_SELECT_OPTIONS, PANEL_TIMEOUT_SECONDS, WARNING_PAGE_SIZE
from features.warnings.repository import WarningStore
from features.warnings.wizard_state import WarningDraftStore, WarningPageState, WarningSessionKey


def _create_warning_session_key(guild_id: int, user_id: int) -> WarningSessionKey:
    """
    建立不會與同一使用者其他設定流程共用狀態的 session key。

    Args:
        guild_id: 伺服器 ID
        user_id: 操作使用者 ID

    Returns:
        包含伺服器、使用者及隨機 nonce 的 session key
    """
    return guild_id, user_id, uuid.uuid4().hex


async def _get_wip_warning(
    interaction: discord.Interaction,
    session_key: WarningSessionKey,
    draft_store: WarningDraftStore,
) -> dict | None:
    """
    驗證互動身分並取得指定設定流程的暫存資料。

    Args:
        interaction: Discord 互動
        session_key: 設定流程的完整 session key

    Returns:
        驗證成功時回傳暫存資料，否則回傳 None 並回覆錯誤訊息
    """
    guild_id, user_id, _ = session_key
    if interaction.guild_id != guild_id or interaction.user.id != user_id:
        error_message = i18n.get_text("messages.error_wip_not_found", guild_id)
        await interaction.response.send_message(error_message, ephemeral=True)
        return None

    wip_data = draft_store.get(session_key)
    if wip_data is None:
        error_message = i18n.get_text("messages.error_wip_not_found", guild_id)
        await interaction.response.send_message(error_message, ephemeral=True)
        return None
    return wip_data


def _parse_schedule_time(value: str) -> str | None:
    """
    驗證並正規化 24 小時制 HH:MM 時間。

    Args:
        value: 使用者輸入的時間

    Returns:
        合法時間字串；格式或範圍無效時回傳 None
    """
    normalized_value = value.strip()
    if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", normalized_value) is None:
        return None
    return normalized_value


def _parse_schedule_days(value: str, frequency_type: str) -> list[int] | None:
    """
    解析每週或每月排程日期，並嚴格驗證格式與範圍。

    Args:
        value: 使用者輸入的逗號分隔日期
        frequency_type: weekly 或 monthly

    Returns:
        合法日期整數列表；格式或範圍無效時回傳 None
    """
    if frequency_type == "weekly":
        maximum_day = 7
    elif frequency_type == "monthly":
        maximum_day = 31
    else:
        return None
    day_tokens = value.split(",")
    if not day_tokens:
        return None

    normalized_tokens = [token.strip() for token in day_tokens]
    if any(re.fullmatch(r"[0-9]+", token) is None for token in normalized_tokens):
        return None

    days = [int(token) for token in normalized_tokens]
    if any(day < 1 or day > maximum_day for day in days):
        return None
    return days


def get_warnings(guild_id: int) -> dict:
    """
    取得指定伺服器的所有定時提醒排程。

    Args:
        guild_id: 伺服器 ID

    Returns:
        dict，鍵為提醒 ID，值為該筆提醒的設定資料
    """
    # 過濾出當前伺服器的設定
    return {key: value for key, value in WarningStore.data.items() if value.get("guild_id") == guild_id}


# ==============================================================================
#  精靈步驟 3：排程時間設定 (Schedule)
# ==============================================================================
class WarningTimeModal(discord.ui.Modal):
    """
    設定提醒排程時間（與每週/每月日期）的表單。
    """

    def __init__(
        self,
        guild_id: int,
        frequency_type: str,
        user_id: int,
        session_key: WarningSessionKey,
        parent_view: "WarningSettingView",
        draft_store: WarningDraftStore,
    ) -> None:
        super().__init__(title=i18n.get_text("ui.modal_time_title", guild_id)[:45])
        self.guild_id = guild_id
        self.frequency_type = frequency_type
        self.user_id = user_id
        self.session_key = session_key
        self.parent_view = parent_view  # 最原始的 WarningSettingView
        self.draft_store = draft_store

        # 預設值讀取 (如果是編輯模式)
        wip_data = draft_store.get(session_key) or {}
        schedule_config = wip_data.get("schedule", {})

        self.time_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_time_hhmm", guild_id)[:45],
            placeholder=i18n.get_text("ui.time_hhmm", guild_id),
            default=schedule_config.get("time", "12:00"),
            required=True, max_length=5
        )
        self.add_item(self.time_input)

        # 只有每週或每月才需要輸入日期
        if frequency_type in ["weekly", "monthly"]:
            default_days = ",".join(map(str, schedule_config.get("days", [])))
            self.days_input = discord.ui.TextInput(
                label=i18n.get_text("ui.input_time_days", guild_id)[:45],
                placeholder=i18n.get_text("ui.time_days", guild_id),
                default=default_days,
                required=True, max_length=50
            )
            self.add_item(self.days_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        wip_data = await _get_wip_warning(interaction, self.session_key, self.draft_store)
        if wip_data is None:
            return

        schedule_time = _parse_schedule_time(self.time_input.value)
        if schedule_time is None:
            error_message = i18n.get_text("messages.error_invalid_time_format", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # 儲存排程
        days: list[int] = []
        if self.frequency_type in ["weekly", "monthly"]:
            parsed_days = _parse_schedule_days(self.days_input.value, self.frequency_type)
            if parsed_days is None:
                error_message = i18n.get_text("messages.error_invalid_days_format", self.guild_id)
                await interaction.response.send_message(error_message, ephemeral=True)
                return
            days = parsed_days

        wip_data["schedule"] = {
            "type": self.frequency_type,
            "time": schedule_time,
            "days": days
        }

        # 寫入資料庫
        warning_id = wip_data.get("id", f"warn_{uuid.uuid4().hex[:8]}")

        await WarningStore.set_warning(
            warning_id,
            {
                "guild_id": self.guild_id,
                "channel_id": wip_data.get("channel_id"),
                "role_id": wip_data.get("role_id"),
                "active": wip_data.get("active", True),
                "schedule": wip_data["schedule"],
                "content": wip_data["content"],
            },
        )

        # 清除暫存
        self.draft_store.discard(self.session_key)

        # 刷新主面板
        updated_view = WarningSettingView(self.guild_id, self.draft_store, self.parent_view.page)
        await interaction.response.edit_message(content=None, embed=updated_view.get_embed(), view=updated_view)
        await interaction.followup.send(i18n.get_text("messages.warning_success_saved", self.guild_id), ephemeral=True)


class WarningScheduleView(discord.ui.View):
    """
    排程頻率選擇視圖（每天/每週/每月）。
    """

    def __init__(
        self,
        guild_id: int,
        user_id: int,
        session_key: WarningSessionKey,
        parent_view: "WarningSettingView",
        draft_store: WarningDraftStore,
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.user_id = user_id
        self.session_key = session_key
        self.parent_view = parent_view
        self.draft_store = draft_store

        options = [
            discord.SelectOption(label=i18n.get_text("ui.freq_daily", guild_id), value="daily"),
            discord.SelectOption(label=i18n.get_text("ui.freq_weekly", guild_id), value="weekly"),
            discord.SelectOption(label=i18n.get_text("ui.freq_monthly", guild_id), value="monthly"),
        ]
        frequency_select = discord.ui.Select(placeholder=i18n.get_text("ui.freq_select", guild_id), options=options)
        frequency_select.callback = self.frequency_callback
        self.add_item(frequency_select)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_callback
        self.add_item(back_button)
        cancel_button = discord.ui.Button(
            label=i18n.get_text("ui.cancel", guild_id), style=discord.ButtonStyle.secondary
        )
        cancel_button.callback = self.cancel_callback
        self.add_item(cancel_button)

    async def frequency_callback(self, interaction: discord.Interaction) -> None:
        if await _get_wip_warning(interaction, self.session_key, self.draft_store) is None:
            return
        frequency_type = interaction.data["values"][0]
        self.stop()
        await interaction.response.send_modal(
            WarningTimeModal(
                self.guild_id,
                frequency_type,
                self.user_id,
                self.session_key,
                self.parent_view,
                self.draft_store,
            )
        )

    async def back_callback(self, interaction: discord.Interaction) -> None:
        if await _get_wip_warning(interaction, self.session_key, self.draft_store) is None:
            return
        self.stop()
        await interaction.response.edit_message(
            content=i18n.get_text("ui.warning_target", self.guild_id),
            embed=None,
            view=WarningTargetView(
                self.guild_id, self.user_id, self.session_key, self.parent_view, self.draft_store
            ),
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        if await _get_wip_warning(interaction, self.session_key, self.draft_store) is None:
            return
        self.draft_store.discard(self.session_key)
        self.stop()
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )

    async def on_timeout(self) -> None:
        """在目前排程步驟逾時時丟棄未完成草稿。"""
        self.draft_store.discard(self.session_key)


# ==============================================================================
#  精靈步驟 2：目標設定 (Targets)
# ==============================================================================
class WarningTargetView(discord.ui.View):
    """
    設定提醒發送頻道與標註身分組的視圖。
    """

    def __init__(
        self,
        guild_id: int,
        user_id: int,
        session_key: WarningSessionKey,
        parent_view: "WarningSettingView",
        draft_store: WarningDraftStore,
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.user_id = user_id
        self.session_key = session_key
        self.parent_view = parent_view
        self.draft_store = draft_store

        # 頻道選擇
        channel_select = discord.ui.ChannelSelect(
            placeholder=i18n.get_text("ui.warning_channel", guild_id),
            channel_types=[discord.ChannelType.text],
            min_values=1, max_values=1
        )
        channel_select.callback = self.channel_callback
        self.add_item(channel_select)

        # 身分組選擇
        role_select = discord.ui.RoleSelect(
            placeholder=i18n.get_text("ui.warning_role", guild_id), min_values=0, max_values=1
        )
        role_select.callback = self.role_callback
        self.add_item(role_select)

        # 下一步按鈕
        next_button = discord.ui.Button(label=i18n.get_text("ui.next_step", guild_id),
                                        style=discord.ButtonStyle.primary)
        next_button.callback = self.next_callback
        self.add_item(next_button)
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_callback
        self.add_item(back_button)
        cancel_button = discord.ui.Button(
            label=i18n.get_text("ui.cancel", guild_id), style=discord.ButtonStyle.secondary
        )
        cancel_button.callback = self.cancel_callback
        self.add_item(cancel_button)

    async def channel_callback(self, interaction: discord.Interaction) -> None:
        wip_data = await _get_wip_warning(interaction, self.session_key, self.draft_store)
        if wip_data is None:
            return
        wip_data["channel_id"] = interaction.data["values"][0]
        await interaction.response.defer()

    async def role_callback(self, interaction: discord.Interaction) -> None:
        wip_data = await _get_wip_warning(interaction, self.session_key, self.draft_store)
        if wip_data is None:
            return
        if interaction.data["values"]:
            wip_data["role_id"] = interaction.data["values"][0]
        await interaction.response.defer()

    async def next_callback(self, interaction: discord.Interaction) -> None:
        wip_data = await _get_wip_warning(interaction, self.session_key, self.draft_store)
        if wip_data is None:
            return
        if "channel_id" not in wip_data or not wip_data["channel_id"]:
            error_message = i18n.get_text("messages.error_channel_required", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        self.stop()
        view = WarningScheduleView(
            self.guild_id, self.user_id, self.session_key, self.parent_view, self.draft_store
        )
        schedule_prompt = i18n.get_text("ui.warning_schedule", self.guild_id)
        await interaction.response.edit_message(content=schedule_prompt, view=view)

    async def back_callback(self, interaction: discord.Interaction) -> None:
        if await _get_wip_warning(interaction, self.session_key, self.draft_store) is None:
            return
        self.stop()
        await interaction.response.send_modal(
            WarningContentModal(
                self.guild_id,
                self.user_id,
                self.session_key,
                self.parent_view,
                self.draft_store,
                preserve_existing=True,
            )
        )

    async def cancel_callback(self, interaction: discord.Interaction) -> None:
        if await _get_wip_warning(interaction, self.session_key, self.draft_store) is None:
            return
        self.draft_store.discard(self.session_key)
        self.stop()
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )

    async def on_timeout(self) -> None:
        """在目前目標設定步驟逾時時丟棄未完成草稿。"""
        self.draft_store.discard(self.session_key)


# ==============================================================================
#  精靈步驟 1：內容設定表單 (Content Modal)
# ==============================================================================
class WarningContentModal(discord.ui.Modal):
    """
    設定提醒內容（標題、內文、底部、縮圖與大圖）的表單。
    """

    def __init__(
        self,
        guild_id: int,
        user_id: int,
        session_key: WarningSessionKey,
        parent_view: "WarningSettingView",
        draft_store: WarningDraftStore,
        edit_id: str | None = None,
        preserve_existing: bool = False,
    ) -> None:
        super().__init__(title=i18n.get_text("ui.modal_warning_content_title", guild_id)[:45])
        self.guild_id = guild_id
        self.user_id = user_id
        self.session_key = session_key
        self.parent_view = parent_view
        self.draft_store = draft_store

        # 初始化暫存
        if not preserve_existing:
            initial_data: dict | None = None
            if edit_id:
                initial_data = WarningStore.data.get(edit_id, {})
            wip_data = self.draft_store.create(session_key, initial_data)
            if edit_id:
                wip_data["id"] = edit_id

        wip_content = (self.draft_store.get(session_key) or {}).get("content", {})

        self.title_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_title", guild_id)[:45],
            default=wip_content.get("title", ""), required=False, max_length=256
        )
        self.add_item(self.title_input)

        self.desc_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_desc", guild_id)[:45], style=discord.TextStyle.paragraph,
            default=wip_content.get("desc", ""), required=True, max_length=2000
        )
        self.add_item(self.desc_input)

        self.footer_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_footer", guild_id)[:45],
            default=wip_content.get("footer", ""), required=False, max_length=1024
        )
        self.add_item(self.footer_input)

        self.thumbnail_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_thumb", guild_id)[:45],
            default=wip_content.get("thumbnail", ""), required=False
        )
        self.add_item(self.thumbnail_input)

        self.image_input = discord.ui.TextInput(
            label=i18n.get_text("ui.input_warning_image", guild_id)[:45],
            default=wip_content.get("image", ""), required=False
        )
        self.add_item(self.image_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        wip_data = await _get_wip_warning(interaction, self.session_key, self.draft_store)
        if wip_data is None:
            return
        wip_data["content"] = {
            "title": self.title_input.value.strip(),
            "desc": self.desc_input.value.strip(),
            "footer": self.footer_input.value.strip(),
            "thumbnail": self.thumbnail_input.value.strip(),
            "image": self.image_input.value.strip()
        }

        view = WarningTargetView(
            self.guild_id, self.user_id, self.session_key, self.parent_view, self.draft_store
        )
        self.stop()
        target_prompt = i18n.get_text("ui.warning_target", self.guild_id)
        await interaction.response.edit_message(content=target_prompt, embed=None, view=view)


# ==============================================================================
#  操作選擇器 (Action Selects)
# ==============================================================================
class WarningListSelect(discord.ui.Select):
    """用於列出單頁提醒供編輯、刪除或切換狀態。"""

    def __init__(
        self,
        guild_id: int,
        action: str,
        parent_view: "WarningSettingView",
        page: int,
        draft_store: WarningDraftStore,
    ) -> None:
        self.guild_id = guild_id
        self.action = action
        self.parent_view = parent_view
        self.draft_store = draft_store

        warning_items = list(get_warnings(guild_id).items())
        page_start = page * MAX_SELECT_OPTIONS
        page_items = warning_items[page_start:page_start + MAX_SELECT_OPTIONS]
        options = []
        for warning_id, warning_data in page_items:
            title = warning_data.get("content", {}).get("title", i18n.get_text("labels.untitled", guild_id))[:50]
            status_text = i18n.get_text(
                "labels.warning_active" if warning_data.get("active", True) else "labels.warning_paused", guild_id
            )
            identifier_text = i18n.get_text("labels.identifier", guild_id, identifier=warning_id)
            options.append(
                discord.SelectOption(
                    label=f"{title} ({status_text})"[:100],
                    value=warning_id,
                    description=identifier_text,
                )
            )

        super().__init__(placeholder=i18n.get_text("ui.select_warning", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        warning_id = self.values[0]
        if self.action == "edit":
            session_key = _create_warning_session_key(self.guild_id, interaction.user.id)
            await interaction.response.send_modal(
                WarningContentModal(
                    self.guild_id,
                    interaction.user.id,
                    session_key,
                    self.parent_view,
                    self.draft_store,
                    warning_id,
                )
            )

        elif self.action == "delete":
            await WarningStore.remove_warning(warning_id)
            updated_view = WarningSettingView(self.guild_id, self.draft_store, self.parent_view.page)
            await interaction.response.edit_message(content=None, embed=updated_view.get_embed(), view=updated_view)
            await interaction.followup.send(i18n.get_text("messages.warning_success_deleted", self.guild_id),
                                            ephemeral=True)

        elif self.action == "toggle":
            active = await WarningStore.toggle_warning(warning_id)
            if active is None:
                error_message = i18n.get_text("messages.error_warning_not_found", self.guild_id)
                await interaction.response.send_message(error_message, ephemeral=True)
                return
            status_text = i18n.get_text(
                "labels.warning_active" if active else "labels.warning_paused", self.guild_id
            )
            toggled_message = i18n.get_text("messages.warning_success_toggled", self.guild_id, status=status_text)

            updated_view = WarningSettingView(self.guild_id, self.draft_store, self.parent_view.page)
            await interaction.response.edit_message(content=None, embed=updated_view.get_embed(), view=updated_view)
            await interaction.followup.send(toggled_message, ephemeral=True)


class WarningSelectionView(discord.ui.View):
    """
    顯示提醒操作目標的分頁選單。
    """

    def __init__(
        self,
        guild_id: int,
        action: str,
        parent_view: "WarningSettingView",
        draft_store: WarningDraftStore,
        page: int = 0,
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.action = action
        self.parent_view = parent_view
        self.draft_store = draft_store
        page_state = WarningPageState(len(get_warnings(guild_id)), MAX_SELECT_OPTIONS, page)
        self.total_pages = page_state.total_pages
        self.page = page_state.page
        self.add_item(WarningListSelect(guild_id, action, parent_view, self.page, draft_store))

        if page_state.has_previous:
            previous_button = discord.ui.Button(
                label=i18n.get_text("ui.previous_page", guild_id), style=discord.ButtonStyle.secondary
            )
            previous_button.callback = self.previous_page
            self.add_item(previous_button)
        if page_state.has_next:
            next_button = discord.ui.Button(
                label=i18n.get_text("ui.next_page", guild_id), style=discord.ButtonStyle.secondary
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            view=WarningSelectionView(
                self.guild_id, self.action, self.parent_view, self.draft_store, self.page - 1
            )
        )

    async def next_page(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            view=WarningSelectionView(
                self.guild_id, self.action, self.parent_view, self.draft_store, self.page + 1
            )
        )

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            content=None, embed=self.parent_view.get_embed(), view=self.parent_view
        )


class WarningActionSelect(discord.ui.Select):
    """主面板的一級菜單"""

    def __init__(
        self, guild_id: int, parent_view: "WarningSettingView", draft_store: WarningDraftStore
    ) -> None:
        self.guild_id = guild_id
        self.parent_view = parent_view
        self.draft_store = draft_store

        options = [
            discord.SelectOption(label=i18n.get_text("ui.add_warning", guild_id), value="add"),
            discord.SelectOption(label=i18n.get_text("ui.edit_warning", guild_id), value="edit"),
            discord.SelectOption(label=i18n.get_text("ui.toggle_warning", guild_id), value="toggle"),
            discord.SelectOption(label=i18n.get_text("ui.delete_warning", guild_id), value="delete"),
        ]
        super().__init__(placeholder=i18n.get_text("ui.warning_action", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_value = self.values[0]

        if selected_value == "add":
            session_key = _create_warning_session_key(self.guild_id, interaction.user.id)
            await interaction.response.send_modal(
                WarningContentModal(
                    self.guild_id,
                    interaction.user.id,
                    session_key,
                    self.parent_view,
                    self.draft_store,
                )
            )
        else:
            # Edit, Toggle, Delete 必須要有現存資料才能操作
            warnings = get_warnings(self.guild_id)
            if not warnings:
                error_message = i18n.get_text("messages.error_no_warnings", self.guild_id)
                await interaction.response.send_message(error_message, ephemeral=True)
                return

            view = WarningSelectionView(self.guild_id, selected_value, self.parent_view, self.draft_store)
            target_prompt = i18n.get_text("messages.select_target_prompt", self.guild_id)
            await interaction.response.edit_message(content=target_prompt, embed=None, view=view)


# ==============================================================================
#  主面板視圖 (Main View)
# ==============================================================================
class WarningSettingView(discord.ui.View):
    """
    定時提醒設定的主面板視圖。
    """

    def __init__(self, guild_id: int, draft_store: WarningDraftStore, page: int = 0) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.draft_store = draft_store
        page_state = WarningPageState(len(get_warnings(guild_id)), WARNING_PAGE_SIZE, page)
        self.page_state = page_state
        self.total_pages = page_state.total_pages
        self.page = page_state.page
        self._build_items()

    def _build_items(self) -> None:
        self.clear_items()
        self.add_item(WarningActionSelect(self.guild_id, self, self.draft_store))
        if self.page_state.has_previous:
            previous_button = discord.ui.Button(
                label=i18n.get_text("ui.previous_page", self.guild_id),
                style=discord.ButtonStyle.secondary,
            )
            previous_button.callback = self.previous_page
            self.add_item(previous_button)
        if self.page_state.has_next:
            next_button = discord.ui.Button(
                label=i18n.get_text("ui.next_page", self.guild_id),
                style=discord.ButtonStyle.secondary,
            )
            next_button.callback = self.next_page
            self.add_item(next_button)

    async def previous_page(self, interaction: discord.Interaction) -> None:
        view = WarningSettingView(self.guild_id, self.draft_store, self.page - 1)
        await interaction.response.edit_message(embed=view.get_embed(), view=view)

    async def next_page(self, interaction: discord.Interaction) -> None:
        view = WarningSettingView(self.guild_id, self.draft_store, self.page + 1)
        await interaction.response.edit_message(embed=view.get_embed(), view=view)

    def get_embed(self) -> discord.Embed:
        """
        依目前設定產生定時提醒總覽的 Embed。

        Returns:
            顯示所有提醒排程摘要的 Embed
        """
        embed = discord.Embed(
            title=i18n.get_text("messages.warning_panel_title", self.guild_id),
            description=i18n.get_text("messages.warning_panel_desc", self.guild_id),
            color=discord.Color.purple()
        )

        warnings = get_warnings(self.guild_id)
        page_text = i18n.get_text(
            "labels.page_indicator", self.guild_id, current=self.page + 1, total=self.total_pages
        )
        list_header = i18n.get_text("messages.warning_list_header", self.guild_id, count=len(warnings))
        list_header = f"{list_header} - {page_text}"
        if not warnings:
            embed.add_field(name=list_header,
                            value=i18n.get_text("messages.warning_no_data", self.guild_id), inline=False)
        else:
            embed.add_field(name=list_header, value="​", inline=False)

            warning_items = list(warnings.items())
            page_start = self.page * WARNING_PAGE_SIZE
            for warning_id, warning_data in warning_items[page_start:page_start + WARNING_PAGE_SIZE]:
                title = warning_data.get("content", {}).get("title") or (
                    f"*({i18n.get_text('labels.untitled', self.guild_id)})*"
                )
                status_text = i18n.get_text(
                    "labels.warning_active" if warning_data.get("active", True) else "labels.warning_paused",
                    self.guild_id
                )

                channel_text = f"<#{warning_data['channel_id']}>" if warning_data.get("channel_id") else i18n.get_text(
                    "messages.value_not_set", self.guild_id)
                role_text = f"<@&{warning_data['role_id']}>" if warning_data.get("role_id") else i18n.get_text(
                    "labels.none_value", self.guild_id)

                schedule_config = warning_data.get("schedule", {})
                frequency_labels = {
                    "daily": i18n.get_text("labels.freq_daily", self.guild_id),
                    "weekly": i18n.get_text("labels.freq_weekly", self.guild_id),
                    "monthly": i18n.get_text("labels.freq_monthly", self.guild_id),
                }
                frequency_text = frequency_labels.get(
                    schedule_config.get("type"), i18n.get_text("labels.freq_unknown", self.guild_id)
                )
                schedule_text = f"{frequency_text} {schedule_config.get('time', '00:00')}"
                if schedule_config.get("days"):
                    schedule_text += f" ({','.join(map(str, schedule_config['days']))})"

                field_value = i18n.get_text(
                    "messages.warning_field_value",
                    self.guild_id,
                    channel=channel_text,
                    role=role_text,
                    schedule=schedule_text,
                )
                field_name = i18n.get_text(
                    "messages.warning_field_name", self.guild_id, title=title, status=status_text
                )
                embed.add_field(name=field_name, value=field_value, inline=False)

        return embed

