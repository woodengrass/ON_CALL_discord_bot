import discord
from discord.ui import Modal, TextInput

from core.i18n import i18n
from core.ui_constants import MODAL_TITLE_MAX_LENGTH, PANEL_TIMEOUT_SECONDS, TEXT_INPUT_LABEL_MAX_LENGTH, truncate_text
from features.text_triggers.repository import add_trigger, delete_trigger, get_guild_triggers


# --- 新增觸發詞表單 ---
class TriggerAddModal(Modal):
    """
    新增觸發詞的表單，可設定觸發詞、回覆內容（支援多筆隨機回覆）與是否為模糊比對。
    """

    def __init__(self, guild_id: int, bot: discord.Client, parent_view: "TriggerSettingView") -> None:
        self.guild_id = guild_id
        self.bot = bot
        self.parent_view = parent_view
        super().__init__(title=truncate_text(i18n.get_text("ui.modal_trigger_title", guild_id), MODAL_TITLE_MAX_LENGTH))

        self.trigger_input = TextInput(
            label=truncate_text(i18n.get_text("ui.input_trigger", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
            placeholder=i18n.get_text("ui.trigger_word", guild_id),
            min_length=1,
            max_length=50
        )

        # 提示文字，告知使用者可用變數
        self.response_input = TextInput(
            label=truncate_text(i18n.get_text("ui.input_response", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
            placeholder=i18n.get_text("ui.trigger_response", guild_id),
            style=discord.TextStyle.paragraph,
            min_length=1,
            max_length=1000
        )

        self.wildcard_input = TextInput(
            label=truncate_text(i18n.get_text("ui.input_wildcard", guild_id), TEXT_INPUT_LABEL_MAX_LENGTH),
            placeholder=i18n.get_text("ui.trigger_wildcard", guild_id),
            min_length=1,
            max_length=5,
            required=False
        )

        self.add_item(self.trigger_input)
        self.add_item(self.response_input)
        self.add_item(self.wildcard_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        trigger = self.trigger_input.value.strip()
        raw_response = self.response_input.value
        wildcard_str = self.wildcard_input.value.strip().lower()
        is_wildcard = wildcard_str in ["y", "yes", "true", "是", "1"]

        response_list = [response_part.strip() for response_part in raw_response.split(',') if response_part.strip()]

        if not response_list:
            saved_response = raw_response.strip()
            response_display = saved_response[:20] + "..."
        elif len(response_list) == 1:
            saved_response = response_list[0]
            response_display = saved_response[:20] + "..."
        else:
            saved_response = response_list
            response_display = i18n.get_text(
                "labels.random_response_count", self.guild_id, count=len(response_list)
            )

        await add_trigger(self.guild_id, trigger, saved_response, is_wildcard)

        trigger_cog = self.bot.get_cog("TextTriggers")
        if trigger_cog:
            await trigger_cog.reload_triggers()

        added_message = i18n.get_text(
            "messages.trigger_added",
            self.guild_id,
            trigger=trigger,
            response=response_display,
            wildcard=is_wildcard
        )
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)
        await interaction.followup.send(added_message, ephemeral=True)


# --- 刪除觸發詞選單 ---
class TriggerDeleteSelect(discord.ui.Select):
    """
    刪除觸發詞用的下拉選單。
    """

    def __init__(
        self,
        guild_id: int,
        bot: discord.Client,
        options: list[discord.SelectOption],
        parent_view: "TriggerSettingView",
    ) -> None:
        self.guild_id = guild_id
        self.bot = bot
        self.parent_view = parent_view
        super().__init__(
            placeholder=i18n.get_text("ui.select_trigger_delete", guild_id),
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        trigger_to_delete = self.values[0]

        deleted = await delete_trigger(self.guild_id, trigger_to_delete)

        if deleted:
            trigger_cog = self.bot.get_cog("TextTriggers")
            if trigger_cog:
                await trigger_cog.reload_triggers()

            deleted_message = i18n.get_text("messages.trigger_deleted", self.guild_id, trigger=trigger_to_delete)
            await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)
            await interaction.followup.send(deleted_message, ephemeral=True)
            return

        not_found_message = i18n.get_text("messages.trigger_not_found", self.guild_id, trigger=trigger_to_delete)
        await interaction.response.send_message(not_found_message, ephemeral=True)


# --- 刪除觸發詞視圖容器 ---
class TriggerDeleteView(discord.ui.View):
    """
    刪除觸發詞的視圖容器，依目前已設定的觸發詞動態建立下拉選單。
    由於 View 建構子不能是非同步的，觸發詞資料需由呼叫端事先查詢好再傳入。
    """

    def __init__(
        self,
        guild_id: int,
        bot: discord.Client,
        triggers: dict[str, dict],
        parent_view: "TriggerSettingView",
    ) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)

        if not triggers:
            button_label = i18n.get_text("ui.no_trigger_delete", guild_id)
            self.add_item(discord.ui.Button(label=button_label, disabled=True))
        else:
            options = []
            for index, (trigger_name, trigger_config) in enumerate(triggers.items()):
                if index >= 25:
                    break
                description = trigger_config.get("response", "")
                if isinstance(description, list):
                    description = i18n.get_text("labels.random_response_count", guild_id, count=len(description))
                elif isinstance(description, str):
                    description = description[:50]
                else:
                    description = str(description)[:50]

                options.append(
                    discord.SelectOption(label=trigger_name[:25], description=description, value=trigger_name)
                )

            self.add_item(TriggerDeleteSelect(guild_id, bot, options, parent_view))

        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)
        self.parent_view = parent_view

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)


class TriggerListView(discord.ui.View):
    """顯示觸發詞清單時提供返回主面板的操作。"""

    def __init__(self, guild_id: int, parent_view: "TriggerSettingView") -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.parent_view = parent_view
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", guild_id), style=discord.ButtonStyle.secondary
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=None, embed=None, view=self.parent_view)


# --- 觸發詞主面板 ---
class TriggerSettingView(discord.ui.View):
    """
    觸發詞設定的主面板，提供新增、刪除與查看觸發詞列表的按鈕。
    """

    def __init__(self, guild_id: int, bot: discord.Client) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.bot = bot

        label_keys = {
            "btn_add_trigger": "ui.add_trigger",
            "btn_del_trigger": "ui.del_trigger",
            "btn_view_trigger": "ui.view_trigger",
        }
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id in label_keys:
                item.label = i18n.get_text(label_keys[item.custom_id], guild_id)

    @discord.ui.button(label=None, style=discord.ButtonStyle.success, custom_id="btn_add_trigger")
    async def add_trigger(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        modal = TriggerAddModal(self.guild_id, self.bot, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label=None, style=discord.ButtonStyle.danger, custom_id="btn_del_trigger")
    async def delete_trigger(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        triggers = await get_guild_triggers(self.guild_id)
        view = TriggerDeleteView(self.guild_id, self.bot, triggers, self)

        prompt_message = i18n.get_text("messages.no_triggers", self.guild_id) if not triggers else i18n.get_text(
            "ui.select_trigger_delete", self.guild_id)
        await interaction.response.edit_message(content=prompt_message, embed=None, view=view)

    @discord.ui.button(label=None, style=discord.ButtonStyle.primary, custom_id="btn_view_trigger")
    async def view_triggers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        triggers = await get_guild_triggers(self.guild_id)

        if not triggers:
            no_triggers_message = i18n.get_text("messages.no_triggers", self.guild_id)
            await interaction.response.send_message(no_triggers_message, ephemeral=True)
            return

        lines = []
        for trigger, trigger_config in triggers.items():
            is_wildcard = trigger_config.get("wildcard", False)
            response = trigger_config.get("response", i18n.get_text("messages.value_not_set", self.guild_id))
            if isinstance(response, list):
                response = i18n.get_text("labels.random_response_count", self.guild_id, count=len(response))
            elif isinstance(response, str):
                response = response[:20] + "..." if len(response) > 20 else response

            wildcard_suffix = i18n.get_text("labels.wildcard_tag", self.guild_id) if is_wildcard else ""
            lines.append(f"`{trigger}` → {response}{wildcard_suffix}")

        output = "\n".join(lines)
        if len(output) > 1900:
            output = output[:1900] + "\n" + i18n.get_text("messages.text_truncated", self.guild_id)

        list_message = i18n.get_text("messages.trigger_list", self.guild_id, output=output)
        embed = discord.Embed(description=list_message, color=discord.Color.blue())
        await interaction.response.edit_message(content=None, embed=embed, view=TriggerListView(self.guild_id, self))

