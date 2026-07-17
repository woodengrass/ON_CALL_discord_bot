import json
from typing import Any

from core.database import get_db


async def get_all_data() -> dict[str, dict]:
    """
    取得所有伺服器的通用設定與模組設定，重建成與舊版 JSON 相容的巢狀結構，供記憶體快取載入使用。

    Returns:
        dict，格式為 {guild_id 字串: {"common": {...}, "modules": {模組名稱: {...}}}}
    """
    db = get_db()
    all_data: dict[str, dict] = {}
    async with db.execute("SELECT guild_id, section, key, value_json FROM guild_settings") as cursor:
        async for guild_id, section, key, value_json in cursor:
            guild_data = all_data.setdefault(str(guild_id), {"common": {}, "modules": {}})
            value = json.loads(value_json)
            if section == "common":
                guild_data["common"][key] = value
            else:
                guild_data["modules"].setdefault(section, {})[key] = value
    return all_data


async def set_value(guild_id: int, section: str, key: str, value: Any) -> None:
    """
    新增或更新一筆設定值。

    Args:
        guild_id: 伺服器 ID
        section: "common" 或實際模組名稱
        key: 設定鍵值
        value: 設定內容，會以 JSON 編碼儲存
    """
    db = get_db()
    await db.execute(
        """
        INSERT INTO guild_settings (guild_id, section, key, value_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (guild_id, section, key) DO UPDATE SET
            value_json = excluded.value_json
        """,
        (guild_id, section, key, json.dumps(value, ensure_ascii=False))
    )
    await db.commit()


