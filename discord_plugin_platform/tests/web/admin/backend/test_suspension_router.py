from fastapi.testclient import TestClient

from core import repository
from tests.web.admin.backend.conftest import default_tier_config, submit_plugin


async def test_suspend_and_unsuspend_plugin(client: TestClient) -> None:
    await submit_plugin()
    client.post(
        "/api/plugins/temp_role_punishment/approve",
        json={"tier_configs": {"default": default_tier_config()}},
    )
    client.put("/api/guilds/1111/installations/temp_role_punishment")

    suspend_response = client.post("/api/plugins/temp_role_punishment/suspend")
    assert suspend_response.status_code == 200
    assert suspend_response.json()["status"] == "suspended"
    assert await repository.get_installation(1111, "temp_role_punishment") is None

    unsuspend_response = client.post("/api/plugins/temp_role_punishment/unsuspend")
    assert unsuspend_response.status_code == 200
    assert unsuspend_response.json()["status"] == "approved"


async def test_suspend_unknown_plugin_returns_404(client: TestClient) -> None:
    response = client.post("/api/plugins/does-not-exist/suspend")
    assert response.status_code == 404
