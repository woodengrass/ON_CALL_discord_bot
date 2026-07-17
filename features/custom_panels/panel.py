import logging
import uuid

import discord
from discord.ui import Button, ChannelSelect, Modal, RoleSelect, Select, TextInput, View

from core.i18n import i18n
from core.ui_constants import MODAL_TITLE_MAX_LENGTH, PANEL_TIMEOUT_SECONDS, TEXT_INPUT_LABEL_MAX_LENGTH, truncate_text
from features.custom_panels.repository import CustomPanelStore

logger = logging.getLogger(__name__)


# ==============================================================================
#  STEP 4: 發布面板 (Publish)
# ==============================================================================
class PanelPublishSelect(ChannelSelect):
    """
    選擇要發布自訂面板的頻道。
    """

    def __init__(self, editor_view: "CustomPanelEditorView") -> None:
        self.editor_view = editor_view
        super().__init__(
            placeholder=i18n.get_text("ui.select_publish_channel", editor_view.guild_id),
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1, max_values=1
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from features.custom_panels.cog import CustomPanelView

        await interaction.response.defer(ephemeral=True)

        guild_id = self.editor_view.guild_id
        raw_channel = self.values[0]
        channel = interaction.guild.get_channel(raw_channel.id)
        if not channel:
            try:
                channel = await interaction.guild.fetch_channel(raw_channel.id)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                logger.exception("Failed to fetch custom panel publish channel %s", raw_channel.id)
                error_message = i18n.get_text("messages.error_channel_not_found", guild_id)
                await interaction.followup.send(error_message, ephemeral=True)
                return

        # 發送實際面板
        embed = self.editor_view.current_embed
        temp_config = {
            "guild_id": interaction.guild.id,
            "buttons": self.editor_view.buttons_data
        }
        view = CustomPanelView(interaction.client, temp_config)

        try:
            message = await channel.send(embed=embed, view=view)
        except Exception as e:
            logger.error(f"發布自訂面板失敗：{e}", exc_info=True)
            error_message = i18n.get_text("messages.error_unknown", guild_id)
            await interaction.followup.send(error_message, ephemeral=True)
            return

        # 存檔
        temp_config["channel_id"] = channel.id
        temp_config["message_id"] = message.id
        await CustomPanelStore.set_panel(message.id, temp_config)

        interaction.client.add_view(view)

        # 恢復主面板顯示
        await self.editor_view.update_preview(interaction, is_followup=True)
        published_message = i18n.get_text("messages.panel_published", guild_id, channel=channel.mention)
        await interaction.followup.send(published_message, ephemeral=True)


class PanelPublishView(View):
    """
    發布面板的頻道選擇視圖，包含取消按鈕。
    """

    def __init__(self, editor_view: "CustomPanelEditorView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.add_item(PanelPublishSelect(editor_view))
        self.editor_view = editor_view

        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.label = i18n.get_text("ui.back", editor_view.guild_id)

    @discord.ui.button(label=None, style=discord.ButtonStyle.secondary, row=1)
    async def cancel_callback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # 取消發布，回到主面板
        await self.editor_view.update_preview(interaction)


# ==============================================================================
#  STEP 3: 按鈕詳細設定 (Modal)
# ==============================================================================
class ButtonConfigModal(Modal):
    """
    按鈕細節設定表單，依按鈕類型顯示對應欄位（隱藏訊息內容 / 表單標題與提示）。
    """

    def __init__(self, editor_view: "CustomPanelEditorView", button_type: int, temp_data: dict) -> None:
        super().__init__(title=truncate_text(
            i18n.get_text("ui.modal_button_config_title", editor_view.guild_id), MODAL_TITLE_MAX_LENGTH
        ))
        self.editor_view = editor_view
        self.button_type = button_type
        self.temp_data = temp_data
        guild_id = editor_view.guild_id

        self.label_input = TextInput(
            label=truncate_text(i18n.get_text("ui.input_btn_label", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
            max_length=80,
        )
        self.style_input = TextInput(
            label=truncate_text(i18n.get_text("ui.input_btn_style", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
            placeholder=i18n.get_text("ui.button_style", guild_id),
            required=False
        )
        self.add_item(self.label_input)
        self.add_item(self.style_input)

        if button_type == 2:
            self.content_input = TextInput(label=truncate_text(
                i18n.get_text("ui.input_hidden_content", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
                                           style=discord.TextStyle.paragraph)
            self.add_item(self.content_input)
        elif button_type in [3, 4]:
            self.modal_title_input = TextInput(
                label=truncate_text(i18n.get_text("ui.input_modal_title", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
                placeholder=i18n.get_text("ui.form_title", guild_id)
            )
            self.input_label_input = TextInput(
                label=truncate_text(i18n.get_text("ui.input_input_label", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
                placeholder=i18n.get_text("ui.input_hint", guild_id)
            )
            self.add_item(self.modal_title_input)
            self.add_item(self.input_label_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        button_uuid = f"btn_{uuid.uuid4().hex[:8]}"
        config = {
            "type": self.button_type,
            "label": self.label_input.value,
            "style": self.style_input.value.lower() if self.style_input.value else "gray",
            **self.temp_data
        }
        if self.button_type == 2:
            config["content"] = self.content_input.value
        elif self.button_type in [3, 4]:
            config["modal_title"] = self.modal_title_input.value
            config["input_label"] = self.input_label_input.value

        self.editor_view.buttons_data[button_uuid] = config

        # 提交後，直接更新原訊息為主面板 (自動更新預覽)
        await self.editor_view.update_preview(interaction)


# ==============================================================================
#  STEP 2: 選擇依賴項 (Role/Channel)
# ==============================================================================
class ButtonDependencyView(View):
    """
    依按鈕類型收集所需的身分組/頻道等依賴設定，完成後進入按鈕細節設定。
    """

    def __init__(self, editor_view: "CustomPanelEditorView", button_type: int) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.editor_view = editor_view
        self.button_type = button_type
        self.temp_data: dict = {}
        guild_id = editor_view.guild_id

        if button_type == 1:
            self.add_item(self._create_role_select(guild_id, "role_ids", "select_role", 5))
        elif button_type == 3:
            self.add_item(self._create_channel_select(guild_id, "log_channel_id", "select_log_channel"))
        elif button_type == 4:
            self.add_item(self._create_role_select(guild_id, "approve_role_id", "select_role"))
            self.add_item(self._create_channel_select(guild_id, "log_channel_id", "select_log_channel"))
            self.add_item(self._create_channel_select(guild_id, "notify_channel_id", "select_notify_channel"))

        self.add_item(Button(label=i18n.get_text("ui.next", guild_id), style=discord.ButtonStyle.primary,
                             row=4, custom_id="next_step"))
        self.add_item(Button(label=i18n.get_text("ui.cancel", guild_id), style=discord.ButtonStyle.secondary,
                             row=4, custom_id="cancel_step"))

        for child in self.children:
            if child.custom_id == "next_step":
                child.callback = self.go_next
            elif child.custom_id == "cancel_step":
                child.callback = self.go_cancel

    def _create_role_select(self, guild_id: int, key: str, label_key: str, max_values: int = 1) -> RoleSelect:
        """
        建立身分組選擇器，選擇結果會存入 temp_data。

        Args:
            guild_id: 伺服器 ID
            key: 存入 temp_data 的鍵值名稱
            label_key: 選單提示文字對應的 i18n 鍵值
            max_values: 最多可選擇的身分組數量

        Returns:
            設定好回呼的 RoleSelect 元件
        """
        select = RoleSelect(placeholder=i18n.get_text(f"ui.{label_key}", guild_id), min_values=1, max_values=max_values)

        async def callback(interaction: discord.Interaction) -> None:
            self.temp_data[key] = [role.id for role in select.values] if max_values > 1 else select.values[0].id
            await interaction.response.defer()

        select.callback = callback
        return select

    def _create_channel_select(self, guild_id: int, key: str, label_key: str) -> ChannelSelect:
        """
        建立頻道選擇器，選擇結果會存入 temp_data。

        Args:
            guild_id: 伺服器 ID
            key: 存入 temp_data 的鍵值名稱
            label_key: 選單提示文字對應的 i18n 鍵值

        Returns:
            設定好回呼的 ChannelSelect 元件
        """
        select = ChannelSelect(placeholder=i18n.get_text(f"ui.{label_key}", guild_id),
                               channel_types=[discord.ChannelType.text])

        async def callback(interaction: discord.Interaction) -> None:
            self.temp_data[key] = select.values[0].id
            await interaction.response.defer()

        select.callback = callback
        return select

    async def go_next(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ButtonConfigModal(self.editor_view, self.button_type, self.temp_data))

    async def go_cancel(self, interaction: discord.Interaction) -> None:
        await self.editor_view.update_preview(interaction)


# ==============================================================================
#  STEP 1: 選擇按鈕類型
# ==============================================================================
class ButtonTypeSelect(Select):
    """
    按鈕類型選擇器（領取身分組 / 隱藏訊息 / 表單提交 / 審核申請）。
    """

    def __init__(self, editor_view: "CustomPanelEditorView") -> None:
        self.editor_view = editor_view
        guild_id = editor_view.guild_id
        options = [
            discord.SelectOption(label=i18n.get_text("ui.button_role", guild_id), value="1"),
            discord.SelectOption(label=i18n.get_text("ui.button_hidden", guild_id), value="2"),
            discord.SelectOption(label=i18n.get_text("ui.button_form", guild_id), value="3"),
            discord.SelectOption(label=i18n.get_text("ui.button_verify", guild_id), value="4"),
        ]
        super().__init__(placeholder=i18n.get_text("ui.select_btn_type", guild_id), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        button_type = int(self.values[0])
        if button_type == 2:
            await interaction.response.send_modal(ButtonConfigModal(self.editor_view, button_type, {}))
        else:
            view = ButtonDependencyView(self.editor_view, button_type)
            prompt_message = i18n.get_text("messages.configure_parameters_prompt", self.editor_view.guild_id)
            await interaction.response.edit_message(content=prompt_message, embed=None, view=view)


class ButtonTypeView(View):
    """
    按鈕類型選擇的視圖容器，包含取消按鈕。
    """

    def __init__(self, editor_view: "CustomPanelEditorView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.add_item(ButtonTypeSelect(editor_view))
        self.editor_view = editor_view

        cancel_button = Button(
            label=i18n.get_text("ui.cancel", editor_view.guild_id),
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        cancel_button.callback = self.cancel_op
        self.add_item(cancel_button)

    async def cancel_op(self, interaction: discord.Interaction) -> None:
        await self.editor_view.update_preview(interaction)


# ==============================================================================
#  STEP 0: 編輯 Embed (縮圖修正)
# ==============================================================================
class PanelEmbedModal(Modal):
    """
    編輯自訂面板 Embed 內容的表單（標題、內文、縮圖網址）。
    """

    def __init__(self, editor_view: "CustomPanelEditorView") -> None:
        guild_id = editor_view.guild_id
        super().__init__(title=truncate_text(
            i18n.get_text("ui.modal_edit_content_title", guild_id), MODAL_TITLE_MAX_LENGTH
        ))
        self.editor_view = editor_view
        self.input_title = TextInput(label=truncate_text(
                                     i18n.get_text("ui.input_embed_title", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
                                     default=editor_view.current_embed.title, required=False)
        self.input_desc = TextInput(label=truncate_text(
                                    i18n.get_text("ui.input_embed_desc", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
                                    default=editor_view.current_embed.description, style=discord.TextStyle.paragraph,
                                    required=False)
        self.input_image = TextInput(
            label=truncate_text(i18n.get_text("ui.input_image_url", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
            required=False,
            placeholder=i18n.get_text("ui.thumbnail_url", guild_id)
        )
        self.add_item(self.input_title)
        self.add_item(self.input_desc)
        self.add_item(self.input_image)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.editor_view.current_embed.title = self.input_title.value
        self.editor_view.current_embed.description = self.input_desc.value

        # 使用 set_thumbnail (小圖)
        if self.input_image.value:
            self.editor_view.current_embed.set_thumbnail(url=self.input_image.value)
        else:
            self.editor_view.current_embed.set_thumbnail(url=None)

        await self.editor_view.update_preview(interaction)


# ==============================================================================
#  MAIN: 面板編輯器主視圖
# ==============================================================================
class CustomPanelEditorView(View):
    """
    自訂面板編輯器的主視圖，統籌 Embed 編輯、按鈕新增與面板發布流程。
    """

    def __init__(self, interaction: discord.Interaction) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = interaction.guild.id
        self.buttons_data: dict = {}
        self.current_embed = discord.Embed(
            title=i18n.get_text("messages.default_panel_title", self.guild_id),
            description=i18n.get_text("messages.default_panel_desc", self.guild_id),
            color=discord.Color.blue()
        )
        self._init_ui_buttons()

    def _init_ui_buttons(self) -> None:
        """
        建立編輯器主畫面的三個功能按鈕：編輯內容、新增按鈕、發布面板。
        """
        guild_id = self.guild_id
        edit_embed_button = Button(label=i18n.get_text("ui.edit_embed", guild_id), style=discord.ButtonStyle.primary)
        edit_embed_button.callback = self.edit_embed_callback
        add_button_button = Button(label=i18n.get_text("ui.add_button", guild_id), style=discord.ButtonStyle.success)
        add_button_button.callback = self.add_button_callback
        publish_button = Button(label=i18n.get_text("ui.publish", guild_id), style=discord.ButtonStyle.success)
        publish_button.callback = self.publish_callback
        self.add_item(edit_embed_button)
        self.add_item(add_button_button)
        self.add_item(publish_button)

    async def update_preview(self, interaction: discord.Interaction, is_followup: bool = False) -> None:
        """
        重建預覽並切換回主介面。

        Args:
            interaction: 觸發更新的互動物件
            is_followup: 若為 True，代表 interaction 已被回覆過，改用編輯訊息的方式更新
        """
        self.clear_items()
        self._init_ui_buttons()

        for button_config in self.buttons_data.values():
            style_map = {"blue": discord.ButtonStyle.primary, "green": discord.ButtonStyle.success,
                         "red": discord.ButtonStyle.danger, "gray": discord.ButtonStyle.secondary}
            preview_button = Button(
                label=button_config.get("label"),
                style=style_map.get(button_config.get("style"), discord.ButtonStyle.secondary),
                disabled=True
            )
            self.add_item(preview_button)

        if is_followup:
            # 如果 interaction 已經被回覆過 (例如從 Publish 回來)，用 edit
            await interaction.message.edit(content=None, embed=self.current_embed, view=self)
        else:
            # 這是標準路徑：取代當前的設定介面，變回主面板
            await interaction.response.edit_message(content=None, embed=self.current_embed, view=self)

    async def edit_embed_callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(PanelEmbedModal(self))

    async def add_button_callback(self, interaction: discord.Interaction) -> None:
        if len(self.buttons_data) >= 20:
            error_message = i18n.get_text("messages.error_max_buttons", self.guild_id)
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # 進入設定模式：隱藏 Embed，顯示設定 View
        prompt_message = i18n.get_text("messages.select_button_type_prompt", self.guild_id)
        await interaction.response.edit_message(content=prompt_message, embed=None, view=ButtonTypeView(self))

    async def publish_callback(self, interaction: discord.Interaction) -> None:
        # 進入發布模式
        prompt_message = i18n.get_text("messages.select_publish_channel_prompt", self.guild_id)
        await interaction.response.edit_message(content=prompt_message, embed=None, view=PanelPublishView(self))

