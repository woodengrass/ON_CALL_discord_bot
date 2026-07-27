import discord

from core.guild_settings import GuildSettings
from core.i18n import i18n
from core.ui_constants import PANEL_TIMEOUT_SECONDS
from core.ui_navigation import BackCallback


class LinkCheckerToggleView(discord.ui.View):
    """
    連結安全檢查功能的開關儀表板，可分別切換總開關、QR code 網址檢查與詐騙圖片比對。
    """

    def __init__(self, guild_id: int, on_back: BackCallback) -> None:
        super().__init__(timeout=PANEL_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.on_back = on_back
        self.update_buttons()

    def update_buttons(self) -> None:
        """
        依目前設定重建三個切換按鈕。
        """
        self.clear_items()
        config = GuildSettings.get_module_config(self.guild_id, "link_checker")
        master_enabled = config.get("enabled", False)
        qr_code_enabled = config.get("qr_code_enabled", True)
        image_hash_enabled = config.get("image_hash_enabled", True)

        self.add_item(self._create_toggle_button("master", master_enabled))
        self.add_item(self._create_toggle_button("qr_code", qr_code_enabled))
        self.add_item(self._create_toggle_button("image_hash", image_hash_enabled))
        self._add_back_button()

    def _create_toggle_button(self, config_key: str, current_state: bool) -> discord.ui.Button:
        """
        建立單一切換按鈕。

        Args:
            config_key: 對應的開關識別字串（master/qr_code/image_hash）
            current_state: 目前的開關狀態

        Returns:
            設定好樣式與回呼的按鈕元件
        """
        style = discord.ButtonStyle.success if current_state else discord.ButtonStyle.danger
        state_text = i18n.get_text("ui.state_on" if current_state else "ui.state_off", self.guild_id)

        if config_key == "master":
            label_key = "ui.link_checker_enable"
        elif config_key == "qr_code":
            label_key = "ui.link_checker_qr"
        else:
            label_key = "ui.link_checker_image_hash"

        label = f"{i18n.get_text(label_key, self.guild_id)}: {state_text}"
        button = discord.ui.Button(label=label, style=style, custom_id=config_key)

        async def callback(interaction: discord.Interaction) -> None:
            new_state = not current_state
            db_key_map = {"master": "enabled", "qr_code": "qr_code_enabled", "image_hash": "image_hash_enabled"}
            await GuildSettings.set_module_config(self.guild_id, "link_checker", db_key_map[config_key], new_state)
            self.update_buttons()
            await interaction.response.edit_message(view=self)
            feature = i18n.get_text(label_key, self.guild_id)
            status = i18n.get_text("ui.state_on" if new_state else "ui.state_off", self.guild_id)
            await interaction.followup.send(
                i18n.get_text("messages.setting_status_updated", self.guild_id, feature=feature, status=status),
                ephemeral=True,
            )

        button.callback = callback
        return button

    def _add_back_button(self) -> None:
        back_button = discord.ui.Button(
            label=i18n.get_text("ui.back", self.guild_id),
            style=discord.ButtonStyle.secondary,
        )
        back_button.callback = self.back_to_main
        self.add_item(back_button)

    async def back_to_main(self, interaction: discord.Interaction) -> None:
        await self.on_back(interaction)
