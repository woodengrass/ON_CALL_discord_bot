from collections.abc import AsyncIterator

import aiosqlite
import pytest

from features.verification import repository


@pytest.fixture
async def database(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[aiosqlite.Connection]:
    """建立只包含驗證資料表的記憶體資料庫。"""
    connection = await aiosqlite.connect(":memory:")
    await connection.execute(
        """
        CREATE TABLE pending_verifications (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            status TEXT NOT NULL,
            review_channel_id INTEGER,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    await connection.commit()
    monkeypatch.setattr(repository, "get_db", lambda: connection)
    yield connection
    await connection.close()


@pytest.mark.asyncio
async def test_review_creation_state_transitions_are_conditional(
    database: aiosqlite.Connection,
) -> None:
    """同一筆 pending 紀錄只能被取得一次，完成後必須帶有對應頻道。"""
    await repository.set_pending(100, 200, 3)

    assert await repository.claim_review_creation(100, 200) is True
    assert await repository.claim_review_creation(100, 200) is False
    assert await repository.set_review_channel(100, 200, 300) is True
    assert await repository.complete_review_creation(100, 200, 301) is False
    assert await repository.complete_review_creation(100, 200, 300) is True

    entry = await repository.get_entry(100, 200)
    assert entry == {"risk_score": 3, "status": "flagged", "review_channel_id": 300}


@pytest.mark.asyncio
async def test_reset_review_creation_returns_entry_to_pending(
    database: aiosqlite.Connection,
) -> None:
    """建立流程失敗後應清空頻道 ID，讓使用者可以重新發起審核。"""
    await repository.set_pending(100, 200, 3)
    await repository.claim_review_creation(100, 200)
    await repository.set_review_channel(100, 200, 300)

    assert await repository.reset_review_creation(100, 200) is True
    assert await repository.reset_review_creation(100, 200) is False

    entry = await repository.get_entry(100, 200)
    assert entry == {"risk_score": 3, "status": "pending", "review_channel_id": None}


@pytest.mark.asyncio
async def test_create_pending_does_not_overwrite_review_creation(database: aiosqlite.Connection) -> None:
    """補救流程的並行點擊不得把已取得的審核建立權重設為 pending。"""
    await repository.set_pending(100, 200, 3)
    assert await repository.claim_review_creation(100, 200) is True

    assert await repository.create_pending(100, 200, 1) is False

    entry = await repository.get_entry(100, 200)
    assert entry == {"risk_score": 3, "status": "review_creating", "review_channel_id": None}
