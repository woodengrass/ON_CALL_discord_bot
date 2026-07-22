import time
from collections.abc import Callable

from core.ui_constants import PANEL_TIMEOUT_SECONDS

WarningSessionKey = tuple[int, int, str]


class WarningDraftStore:
    """
    管理定時提醒設定精靈的暫存草稿與逾時生命週期。

    Args:
        clock: 取得單調時間的函式，供逾時判斷與測試使用
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._drafts: dict[WarningSessionKey, tuple[float, dict]] = {}

    def create(self, session_key: WarningSessionKey, initial_data: dict | None = None) -> dict:
        """
        建立一筆新的提醒設定草稿。

        Args:
            session_key: 設定流程的完整 session key
            initial_data: 編輯既有提醒時的初始資料

        Returns:
            可由目前精靈流程更新的草稿資料
        """
        draft = dict(initial_data or {})
        deadline = self._clock() + PANEL_TIMEOUT_SECONDS
        self._drafts[session_key] = deadline, draft
        return draft

    def get(self, session_key: WarningSessionKey) -> dict | None:
        """
        取得尚未逾時的提醒設定草稿。

        Args:
            session_key: 設定流程的完整 session key

        Returns:
            草稿資料；找不到或已逾時時回傳 None
        """
        draft_entry = self._drafts.get(session_key)
        if draft_entry is None:
            return None

        deadline, draft = draft_entry
        if self._clock() >= deadline:
            self.discard(session_key)
            return None
        return draft

    def discard(self, session_key: WarningSessionKey) -> None:
        """
        丟棄指定設定流程的草稿。

        Args:
            session_key: 設定流程的完整 session key
        """
        self._drafts.pop(session_key, None)

    def clear(self) -> None:
        """清除目前所有提醒設定草稿。"""
        self._drafts.clear()


class WarningPageState:
    """計算定時提醒面板與選單的分頁範圍。"""

    def __init__(self, item_count: int, page_size: int, page: int) -> None:
        self.total_pages = max(1, (item_count + page_size - 1) // page_size)
        self.page = min(max(page, 0), self.total_pages - 1)

    @property
    def has_previous(self) -> bool:
        """判斷目前是否可切換至上一頁。"""
        return self.page > 0

    @property
    def has_next(self) -> bool:
        """判斷目前是否可切換至下一頁。"""
        return self.page < self.total_pages - 1
