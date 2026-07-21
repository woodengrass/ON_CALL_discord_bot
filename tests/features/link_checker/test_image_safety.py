from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Self
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from features.link_checker import cog as link_checker_cog
from features.link_checker.cog import ImageFileTooLargeError, LinkChecker


class FakeContent:
    """模擬 aiohttp 串流回應內容。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, chunk_size: int) -> AsyncIterator[bytes]:
        """依序產生預先指定的資料區塊。"""
        for chunk in self.chunks:
            yield chunk


class FakeResponse:
    """模擬可作為 async context manager 的 HTTP 回應。"""

    def __init__(self, chunks: list[bytes], status: int = 200) -> None:
        self.status = status
        self.content = FakeContent(chunks)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


class FakeSession:
    """模擬圖片下載使用的 HTTP session。"""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def get(self, url: str) -> FakeResponse:
        """回傳固定 HTTP 回應。"""
        return self.response


@pytest.mark.asyncio
async def test_download_image_rejects_declared_oversized_file(tmp_path: Path) -> None:
    """Discord 宣告的圖片附件大小超限時，不應啟動下載。"""
    checker = object.__new__(LinkChecker)
    checker.max_image_bytes = 5
    checker.session = None
    attachment = SimpleNamespace(size=6, url="https://cdn.example/image.png")

    with pytest.raises(ImageFileTooLargeError):
        await checker._download_image_attachment(attachment, str(tmp_path / "image.png"))


@pytest.mark.asyncio
async def test_download_image_stops_when_stream_exceeds_limit(tmp_path: Path) -> None:
    """實際圖片資料超過限制時，即使附件宣告較小也必須中止。"""
    checker = object.__new__(LinkChecker)
    checker.max_image_bytes = 5
    checker.session = FakeSession(FakeResponse([b"abc", b"def"]))
    attachment = SimpleNamespace(size=4, url="https://cdn.example/image.png")

    with pytest.raises(ImageFileTooLargeError):
        await checker._download_image_attachment(attachment, str(tmp_path / "image.png"))


def test_process_image_rejects_excessive_pixels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """圖片像素數超過設定上限時，不應進行 QR 或雜湊處理。"""
    image_filename = tmp_path / "large.png"
    Image.new("RGB", (10, 10)).save(image_filename)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 50)
    checker = object.__new__(LinkChecker)
    checker.max_image_pixels = 100
    checker.scam_hashes = []

    qr_urls, matched_label = checker._process_image(str(image_filename), check_qr=True, check_hash=True)

    assert qr_urls == []
    assert matched_label is None


@pytest.mark.asyncio
async def test_message_processes_images_sequentially_and_deletes_temp_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """每個圖片應在處理與刪除暫存檔後，才開始下載下一個附件。"""
    checker = object.__new__(LinkChecker)
    checker.is_module_enabled = Mock(return_value=True)
    checker.is_qr_code_check_enabled = Mock(return_value=True)
    checker.is_image_hash_check_enabled = Mock(return_value=False)
    checker.url_pattern = link_checker_cog.re.compile(r"https?://[^\s]+")
    events: list[str] = []
    temp_filenames: list[str] = []

    async def download(attachment: SimpleNamespace, temp_filename: str) -> None:
        if temp_filenames:
            assert not Path(temp_filenames[-1]).exists()
        events.append(f"download:{attachment.filename}")
        temp_filenames.append(temp_filename)
        Path(temp_filename).write_bytes(b"image")

    async def process_in_thread(
        function: object,
        temp_filename: str,
        check_qr: bool,
        check_hash: bool,
    ) -> tuple[list[str], None]:
        assert Path(temp_filename).exists()
        events.append(f"process:{Path(temp_filename).suffix}")
        return [], None

    checker._download_image_attachment = AsyncMock(side_effect=download)
    monkeypatch.setattr(link_checker_cog.asyncio, "to_thread", process_in_thread)
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False),
        guild=SimpleNamespace(id=100),
        content="",
        attachments=[
            SimpleNamespace(filename="first.png", content_type="image/png"),
            SimpleNamespace(filename="second.png", content_type="image/png"),
        ],
    )

    await checker.on_message(message)

    assert events == ["download:first.png", "process:.png", "download:second.png", "process:.png"]
    assert all(not Path(temp_filename).exists() for temp_filename in temp_filenames)
