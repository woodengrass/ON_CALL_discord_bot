import asyncio
import datetime
import io
import logging
import os
import re
import time
from urllib.parse import urljoin, urlparse

import aiohttp
import discord
import imagehash
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image
from pyzbar.pyzbar import decode as decode_qr_codes

from core.audit_log_repository import add_log_entry
from core.config import CONFIG
from core.guild_settings import GuildSettings
from core.i18n import i18n
from features.link_checker.repository import get_all_keywords, get_all_scam_hashes, seed_default_keywords_if_empty
from features.link_checker.url_safety import PublicAddressResolver, is_safe_public_url

logger = logging.getLogger(__name__)

load_dotenv("token.env")

GOOGLE_API_KEY = os.getenv("GOOGLE_SAFE_BROWSING_KEY")
MAX_SHORT_URL_REDIRECTS = 3
REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}

IMAGE_FILE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
SCAM_IMAGE_HAMMING_THRESHOLD = 8  # 感知雜湊漢明距離門檻，數值越小代表要求越接近原圖

link_checker_config = CONFIG.get("link_checker", {})
IMAGE_SCAM_TIMEOUT_HOURS = link_checker_config.get("image_scam_timeout_hours", 240)
IMAGE_SCAM_TIMEOUT_DURATION = datetime.timedelta(hours=IMAGE_SCAM_TIMEOUT_HOURS)


class LinkChecker(commands.Cog):
    """
    偵測訊息中的網址，透過關鍵字黑名單與 Google Safe Browsing API 檢查是否為惡意連結。
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        self.url_pattern = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:/[-\w./?%&=]*)?')

        self.shortener_domains = {
            "bit.ly", "tinyurl.com", "goo.gl", "rebrand.ly", "t.co", "is.gd",
            "buff.ly", "adf.ly", "ow.ly", "j.mp", "su.pr", "bc.vc", "zz.gd"
        }

        self.cache: dict[str, tuple[bool, float]] = {}
        self.cache_ttl = 3600  # 快取存活 1 小時
        self.session: aiohttp.ClientSession | None = None
        self.suspicious_keywords: list[str] = []
        self.scam_hashes: list[tuple[str, str]] = []

        self.clean_cache_task.start()

    async def cog_load(self) -> None:
        """
        載入 Cog 時建立共用的 aiohttp session，並從資料庫載入可疑關鍵字與詐騙圖片雜湊清單。
        """
        connector = aiohttp.TCPConnector(resolver=PublicAddressResolver(), ttl_dns_cache=0)
        timeout = aiohttp.ClientTimeout(total=5, connect=3, sock_read=3)
        self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        await seed_default_keywords_if_empty()
        await self.reload_keywords()
        await self.reload_scam_hashes()

    async def reload_keywords(self) -> None:
        """
        從資料庫重新載入可疑關鍵字快取，新增/刪除關鍵字後呼叫可立即生效。
        """
        self.suspicious_keywords = await get_all_keywords()

    async def reload_scam_hashes(self) -> None:
        """
        從資料庫重新載入已知詐騙圖片的感知雜湊快取，新增/刪除雜湊後呼叫可立即生效。
        """
        self.scam_hashes = await get_all_scam_hashes()

    async def cog_unload(self) -> None:
        """
        卸載 Cog 時停止快取清理背景任務並關閉共用的 aiohttp session。
        """
        self.clean_cache_task.cancel()
        if self.session:
            await self.session.close()

    @tasks.loop(hours=1)
    async def clean_cache_task(self) -> None:
        """
        定期清除已超過存活時間的網址安全性快取。
        """
        now = time.time()
        expired_keys = [
            cache_key
            for cache_key, cache_value in self.cache.items()
            if now - cache_value[1] > self.cache_ttl
        ]
        for cache_key in expired_keys:
            del self.cache[cache_key]

    def is_module_enabled(self, guild_id: int) -> bool:
        """
        檢查指定伺服器是否已啟用連結檢查功能。

        Args:
            guild_id: 伺服器 ID

        Returns:
            True 表示已啟用
        """
        config = GuildSettings.get_module_config(guild_id, "link_checker")
        return config.get("enabled", False)

    def is_whitelisted(self, user_id: int, guild_id: int) -> bool:
        """
        檢查使用者是否在伺服器共用白名單中。

        Args:
            user_id: 使用者 ID
            guild_id: 伺服器 ID

        Returns:
            True 表示使用者在白名單中
        """
        return str(user_id) in GuildSettings.get_whitelist(guild_id)

    def is_qr_code_check_enabled(self, guild_id: int) -> bool:
        """
        檢查指定伺服器是否已啟用 QR code 網址檢查子功能。

        Args:
            guild_id: 伺服器 ID

        Returns:
            True 表示已啟用（預設開啟）
        """
        config = GuildSettings.get_module_config(guild_id, "link_checker")
        return config.get("qr_code_enabled", True)

    def is_image_hash_check_enabled(self, guild_id: int) -> bool:
        """
        檢查指定伺服器是否已啟用詐騙圖片比對子功能。

        Args:
            guild_id: 伺服器 ID

        Returns:
            True 表示已啟用（預設開啟）
        """
        config = GuildSettings.get_module_config(guild_id, "link_checker")
        return config.get("image_hash_enabled", True)

    async def unshorten_url(self, url: str) -> str:
        """
        若網址屬於已知短網址服務，還原為原始網址。

        Args:
            url: 原始網址

        Returns:
            還原後的網址；若非短網址或還原失敗則回傳原網址
        """
        parsed_url = urlparse(url)
        domain = parsed_url.hostname.lower() if parsed_url.hostname else ""
        if domain not in self.shortener_domains:
            return url

        current_url = url
        try:
            for _redirect_count in range(MAX_SHORT_URL_REDIRECTS + 1):
                if not is_safe_public_url(current_url):
                    logger.warning("拒絕展開不安全的短網址目標：%s", current_url)
                    return url

                status, location = await self._request_redirect(current_url)
                if status not in REDIRECT_STATUS_CODES or not location:
                    return current_url
                current_url = urljoin(current_url, location)

            logger.warning("短網址重新導向次數超過上限：%s", url)
        except Exception as error:
            logger.error(f"短網址還原失敗：{error}", exc_info=True)
        return url

    async def _request_redirect(self, url: str) -> tuple[int, str | None]:
        """
        對單一 URL 發出不自動跟隨的輕量請求，回傳狀態碼與 Location。

        Args:
            url: 已通過基本結構驗證的網址

        Returns:
            HTTP 狀態碼與 Location 標頭；沒有 Location 時回傳 None
        """
        if self.session is None:
            raise RuntimeError("HTTP session 尚未初始化")
        async with self.session.head(url, allow_redirects=False) as response:
            if response.status not in {405, 501}:
                return response.status, response.headers.get("Location")

        headers = {"Range": "bytes=0-0"}
        async with self.session.get(url, headers=headers, allow_redirects=False) as response:
            return response.status, response.headers.get("Location")

    async def check_google_safe_browsing(self, url: str) -> bool:
        """
        呼叫 Google Safe Browsing API 檢查網址是否為已知威脅。
        Google 已將 v4 的 threatMatches:find 方法標示為棄用，改用 v5alpha1 的 urls:search，
        此端點目前仍是 Google 標示的 Alpha 版本，格式未來仍可能調整。

        Args:
            url: 欲檢查的網址

        Returns:
            True 表示安全；若 API 未設定或呼叫失敗則預設回傳 True
        """
        if not GOOGLE_API_KEY:
            return True

        api_url = "https://safebrowsing.googleapis.com/v5alpha1/urls:search"
        params = {"key": GOOGLE_API_KEY, "urls[]": url}

        try:
            async with self.session.get(api_url, params=params, timeout=3) as response:
                if response.status == 200:
                    data = await response.json()

                    if data.get("threats"):
                        return False
                else:
                    logger.error(f"Google Safe Browsing API 回應錯誤：{response.status}")
        except Exception as e:
            logger.error(f"Google Safe Browsing API 請求失敗：{e}", exc_info=True)

        return True

    async def check_url_safety(self, url: str) -> bool:
        """
        綜合快取、短網址還原、關鍵字黑名單與 Google Safe Browsing API 檢查網址安全性。

        Args:
            url: 欲檢查的網址

        Returns:
            True 表示安全
        """
        if url in self.cache:
            is_safe, timestamp = self.cache[url]
            if time.time() - timestamp < self.cache_ttl:
                return is_safe

        final_url = await self.unshorten_url(url)
        final_url_lower = final_url.lower()

        is_safe = True

        for keyword in self.suspicious_keywords:
            if keyword in final_url_lower:
                is_safe = False
                break

        if is_safe:
            is_safe = await self.check_google_safe_browsing(final_url)

        self.cache[url] = (is_safe, time.time())
        if final_url != url:
            self.cache[final_url] = (is_safe, time.time())

        return is_safe

    def _is_image_attachment(self, attachment: discord.Attachment) -> bool:
        """
        判斷附件是否為圖片檔案。

        Args:
            attachment: Discord 附件物件

        Returns:
            True 表示是圖片
        """
        if attachment.content_type and attachment.content_type.startswith("image/"):
            return True
        return attachment.filename.lower().endswith(IMAGE_FILE_EXTENSIONS)

    def _process_image(self, image_bytes: bytes, check_qr: bool, check_hash: bool) -> tuple[list[str], str | None]:
        """
        同步處理單張圖片：解碼 QR code 取得網址、計算感知雜湊並比對已知詐騙圖片。
        內含 CPU-bound 運算（QR 解碼、雜湊計算），呼叫端應以 asyncio.to_thread 執行，避免阻塞事件迴圈。

        Args:
            image_bytes: 圖片原始位元組
            check_qr: 是否解碼 QR code
            check_hash: 是否比對詐騙圖片感知雜湊

        Returns:
            (QR code 中解出的網址清單, 相符的詐騙圖片標籤；沒有相符則為 None)
        """
        qr_urls: list[str] = []
        matched_label: str | None = None

        if not check_qr and not check_hash:
            return qr_urls, matched_label

        try:
            image = Image.open(io.BytesIO(image_bytes))
        except Exception:
            return qr_urls, matched_label

        if check_qr:
            try:
                for qr_code in decode_qr_codes(image):
                    try:
                        qr_urls.append(qr_code.data.decode("utf-8"))
                    except UnicodeDecodeError:
                        continue
            except Exception as error:
                logger.error(f"QR code 解碼失敗：{error}", exc_info=True)

        if check_hash:
            try:
                current_hash = imagehash.phash(image)
                for scam_hash_hex, label in self.scam_hashes:
                    scam_hash = imagehash.hex_to_hash(scam_hash_hex)
                    if current_hash - scam_hash <= SCAM_IMAGE_HAMMING_THRESHOLD:
                        matched_label = label
                        break
            except Exception as error:
                logger.error(f"圖片感知雜湊比對失敗：{error}", exc_info=True)

        return qr_urls, matched_label

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        監聽訊息，檢查其中網址與圖片附件的安全性並依結果發送警告。
        圖片附件會嘗試解碼 QR code（取出的網址併入一般網址檢查流程）並比對已知詐騙圖片的感知雜湊。

        Args:
            message: 收到的訊息物件
        """
        if message.author.bot or not message.guild:
            return

        if not self.is_module_enabled(message.guild.id):
            return

        if self.is_whitelisted(message.author.id, message.guild.id):
            return

        check_qr = self.is_qr_code_check_enabled(message.guild.id)
        check_hash = self.is_image_hash_check_enabled(message.guild.id)

        urls = list(self.url_pattern.findall(message.content))
        image_scam_label: str | None = None

        if (check_qr or check_hash) and message.attachments:
            for attachment in message.attachments:
                if not self._is_image_attachment(attachment):
                    continue
                try:
                    image_bytes = await attachment.read()
                except Exception as e:
                    logger.error(f"下載附件圖片失敗：{e}", exc_info=True)
                    continue

                qr_urls, matched_label = await asyncio.to_thread(
                    self._process_image, image_bytes, check_qr, check_hash
                )
                urls.extend(qr_urls)
                if matched_label and image_scam_label is None:
                    image_scam_label = matched_label

        if not urls and not image_scam_label:
            return

        has_unsafe_link = False
        unsafe_links = []

        for url in urls:
            is_safe = await self.check_url_safety(url)

            if not is_safe:
                has_unsafe_link = True
                unsafe_links.append(url)

        if has_unsafe_link:
            try:
                warning_message = i18n.get_text("messages.link_unsafe_warning", message.guild.id, url=unsafe_links[0])
                await message.reply(warning_message, mention_author=True)
            except Exception as e:
                logger.error(f"發送惡意連結警告失敗：{e}", exc_info=True)

        if image_scam_label:
            await self._take_image_scam_action(message, image_scam_label)

    async def _take_image_scam_action(self, message: discord.Message, matched_label: str) -> None:
        """
        對命中已知詐騙圖片的訊息採取處置：刪除訊息、禁言使用者、寫入稽核紀錄並通知公告頻道。

        Args:
            message: 觸發偵測的訊息物件
            matched_label: 比對命中的詐騙圖片標籤
        """
        user = message.author
        guild = message.guild
        channel = message.channel
        logger.warning(
            "偵測到詐騙圖片：伺服器=%s (%s)，頻道=%s (%s)，使用者=%s (%s)，標籤=%s",
            guild.name, guild.id, channel, channel.id, user, user.id, matched_label,
        )

        try:
            await message.delete()
        except Exception as error:
            logger.error(f"刪除詐騙圖片訊息失敗：{error}", exc_info=True)

        try:
            warning_message = i18n.get_text("messages.image_scam_warning", guild.id)
            await channel.send(f"{user.mention} {warning_message}")
        except Exception as error:
            logger.error(f"發送詐騙圖片警告失敗：{error}", exc_info=True)

        try:
            if guild.me.guild_permissions.moderate_members:
                reason = i18n.get_text("messages.image_scam_timeout_reason", guild.id)
                await user.timeout(IMAGE_SCAM_TIMEOUT_DURATION, reason=reason)
                await add_log_entry(guild.id, user.id, "image_scam_timeout", reason)
            else:
                logger.warning("缺少禁言成員權限：伺服器 ID=%s，使用者 ID=%s", guild.id, user.id)
        except Exception as error:
            logger.error(f"禁言詐騙圖片使用者失敗：{error}", exc_info=True)

        announcement_id = GuildSettings.get_log_channel(guild.id)
        if announcement_id:
            log_channel = guild.get_channel(int(announcement_id))
            if log_channel:
                try:
                    log_message = i18n.get_text(
                        "messages.image_scam_detected_log", guild.id,
                        user=user.mention, channel=channel.mention, label=matched_label,
                    )
                    await log_channel.send(log_message)
                except Exception as error:
                    logger.error(f"發送詐騙圖片通知失敗：{error}", exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkChecker(bot))

