import datetime

from core.database import get_db


async def set_pending(guild_id: int, user_id: int, risk_score: int) -> None:
    """
    建立或覆蓋一筆待驗證紀錄，狀態設為 pending。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID
        risk_score: 計算出的風險分數
    """
    db = get_db()
    joined_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await db.execute(
        """
        INSERT INTO pending_verifications (guild_id, user_id, joined_at, risk_score, status)
        VALUES (?, ?, ?, ?, 'pending')
        ON CONFLICT (guild_id, user_id) DO UPDATE SET
            joined_at = excluded.joined_at,
            risk_score = excluded.risk_score,
            status = 'pending',
            review_channel_id = NULL
        """,
        (guild_id, user_id, joined_at, risk_score)
    )
    await db.commit()


async def create_pending(guild_id: int, user_id: int, risk_score: int) -> bool:
    """
    僅在尚無驗證紀錄時建立一筆待驗證紀錄，避免並行按鈕點擊覆寫既有流程狀態。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID
        risk_score: 計算出的風險分數

    Returns:
        True 表示成功建立；已有紀錄時回傳 False
    """
    db = get_db()
    joined_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor = await db.execute(
        """
        INSERT INTO pending_verifications (guild_id, user_id, joined_at, risk_score, status)
        VALUES (?, ?, ?, ?, 'pending')
        ON CONFLICT (guild_id, user_id) DO NOTHING
        """,
        (guild_id, user_id, joined_at, risk_score),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_entry(guild_id: int, user_id: int) -> dict | None:
    """
    取得指定成員的驗證紀錄。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID

    Returns:
        dict，包含 risk_score、status 與 review_channel_id；找不到則回傳 None
    """
    db = get_db()
    async with db.execute(
        "SELECT risk_score, status, review_channel_id FROM pending_verifications WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    ) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return {"risk_score": row[0], "status": row[1], "review_channel_id": row[2]}


async def claim_review_creation(guild_id: int, user_id: int) -> bool:
    """
    原子取得建立人工審核頻道的處理權，避免同一成員重複點擊而建立多個頻道。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID

    Returns:
        True 表示成功將 pending 轉為 review_creating；其他狀態則回傳 False
    """
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE pending_verifications
        SET status = 'review_creating', review_channel_id = NULL
        WHERE guild_id = ? AND user_id = ? AND status = 'pending'
        """,
        (guild_id, user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def set_review_channel(guild_id: int, user_id: int, channel_id: int) -> bool:
    """
    記錄指定成員的審核私人頻道 ID。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID
        channel_id: 審核頻道 ID

    Returns:
        True 表示建立中的紀錄已成功寫入頻道 ID
    """
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE pending_verifications
        SET review_channel_id = ?
        WHERE guild_id = ? AND user_id = ? AND status = 'review_creating'
        """,
        (channel_id, guild_id, user_id)
    )
    await db.commit()
    return cursor.rowcount > 0


async def complete_review_creation(guild_id: int, user_id: int, channel_id: int) -> bool:
    """
    在審核頻道與審核訊息都建立成功後，將建立中狀態轉為等待人工審核。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID
        channel_id: 審核頻道 ID

    Returns:
        True 表示狀態成功轉為 flagged
    """
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE pending_verifications
        SET status = 'flagged'
        WHERE guild_id = ? AND user_id = ?
          AND status = 'review_creating' AND review_channel_id = ?
        """,
        (guild_id, user_id, channel_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def reset_review_creation(guild_id: int, user_id: int) -> bool:
    """
    審核頻道建立失敗或啟動時發現未完成流程時，將紀錄復原為 pending。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID

    Returns:
        True 表示有建立中的紀錄被復原
    """
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE pending_verifications
        SET status = 'pending', review_channel_id = NULL
        WHERE guild_id = ? AND user_id = ? AND status = 'review_creating'
        """,
        (guild_id, user_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def get_review_creations() -> list[tuple[int, int, int | None]]:
    """
    取得上次程序中斷後仍停留在建立中狀態的審核紀錄。

    Returns:
        (guild_id, user_id, review_channel_id) 的清單
    """
    db = get_db()
    async with db.execute(
        """
        SELECT guild_id, user_id, review_channel_id
        FROM pending_verifications
        WHERE status = 'review_creating'
        """
    ) as cursor:
        rows = await cursor.fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


async def set_status(guild_id: int, user_id: int, status: str) -> None:
    """
    更新指定成員的驗證狀態。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID
        status: 新狀態，例如 "approved"、"rejected"、"flagged"
    """
    db = get_db()
    await db.execute(
        "UPDATE pending_verifications SET status = ? WHERE guild_id = ? AND user_id = ?",
        (status, guild_id, user_id)
    )
    await db.commit()


async def delete_entry(guild_id: int, user_id: int) -> None:
    """
    移除指定成員的驗證紀錄（例如成員離開伺服器時）。

    Args:
        guild_id: 伺服器 ID
        user_id: 使用者 ID
    """
    db = get_db()
    await db.execute(
        "DELETE FROM pending_verifications WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id)
    )
    await db.commit()


async def reset_flagged_entry_by_channel(guild_id: int, channel_id: int) -> int:
    """
    當審核頻道被刪除時呼叫：把指向該頻道、且仍為 flagged（尚未結案）的紀錄重置回 pending，
    並清空 review_channel_id，避免資料庫繼續指向一個已經不存在的頻道。
    只處理 status = 'flagged' 的紀錄，已經 approved/rejected 的紀錄不受影響
    （正常通過/拒絕流程結束時也會刪除頻道，但那時狀態已經不是 flagged 了）。

    Args:
        guild_id: 伺服器 ID
        channel_id: 被刪除的頻道 ID

    Returns:
        實際被重置的筆數
    """
    db = get_db()
    cursor = await db.execute(
        """
        UPDATE pending_verifications
        SET status = 'pending', review_channel_id = NULL
        WHERE guild_id = ? AND review_channel_id = ? AND status = 'flagged'
        """,
        (guild_id, channel_id)
    )
    await db.commit()
    return cursor.rowcount


async def get_stale_review_channels() -> list[tuple[int, int]]:
    """
    取得所有目前仍為 flagged 狀態且有記錄審核頻道的 (guild_id, review_channel_id)，
    供定期清理任務比對頻道是否還存在。

    Returns:
        (guild_id, review_channel_id) 的清單
    """
    db = get_db()
    async with db.execute(
        """
        SELECT guild_id, review_channel_id
        FROM pending_verifications
        WHERE status = 'flagged' AND review_channel_id IS NOT NULL
        """
    ) as cursor:
        rows = await cursor.fetchall()
    return [(row[0], row[1]) for row in rows]


async def delete_guild_entries(guild_id: int) -> int:
    """
    移除指定伺服器的所有驗證紀錄（機器人被移出伺服器時使用）。

    Args:
        guild_id: 伺服器 ID

    Returns:
        實際刪除的筆數
    """
    db = get_db()
    cursor = await db.execute("DELETE FROM pending_verifications WHERE guild_id = ?", (guild_id,))
    await db.commit()
    return cursor.rowcount

