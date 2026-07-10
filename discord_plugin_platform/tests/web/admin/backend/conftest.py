import json
from collections.abc import AsyncIterator

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from core import database, repository
from web.admin.backend.main import app


@pytest.fixture()
async def plugin_database(tmp_path, monkeypatch) -> AsyncIterator[aiosqlite.Connection]:
    """
    建立每個測試專用的暫存外掛平台資料庫，比照 tests/core/test_repository.py 的模式。
    """
    await database.close_db()
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "plugin_platform.db"))
    await database.init_db()
    yield database.get_db()
    await database.close_db()


@pytest.fixture()
def client(plugin_database: aiosqlite.Connection) -> TestClient:
    """
    提供綁定暫存資料庫的 FastAPI TestClient。
    """
    return TestClient(app)


def manifest_json(required_capabilities: list[str] | None = None) -> str:
    """
    建立符合 manifest 驗證規則的測試 manifest。
    """
    return json.dumps(
        {
            "name": "temp_role_punishment",
            "version": "1.0.0",
            "description": "測試外掛",
            "capability_api_version": 1,
            "event_hooks": ["on_message"],
            "required_capabilities": required_capabilities or [],
        },
        ensure_ascii=False,
    )


async def submit_plugin(
    plugin_id: str = "temp_role_punishment", required_capabilities: list[str] | None = None
) -> None:
    """
    提交一個可供路由測試使用的外掛版本。
    """
    await repository.submit_plugin_version(
        plugin_id=plugin_id,
        author_id=1234,
        name=plugin_id,
        version="1.0.0",
        manifest_json=manifest_json(required_capabilities),
        source_code="function on_message(payload) end",
        capability_api_version=1,
    )


def default_tier_config(allowed: bool = True) -> dict:
    """
    建立完整的 default 方案設定，approve 路由需要涵蓋所有方案。
    """
    return {
        "allowed": allowed,
        "storage_key_length_limit": 256,
        "storage_value_bytes_limit": 65536,
        "storage_keys_per_installation_limit": 1000,
        "instruction_limit": 500000,
        "memory_limit_bytes": 16777216,
    }
