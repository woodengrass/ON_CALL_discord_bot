from fastapi.testclient import TestClient

from tests.web.admin.backend.conftest import default_tier_config, submit_plugin


async def _approve_plugin(client: TestClient, plugin_id: str = "temp_role_punishment") -> None:
    await submit_plugin(plugin_id)
    client.post(f"/api/plugins/{plugin_id}/approve", json={"tier_configs": {"default": default_tier_config()}})


async def test_install_list_and_uninstall(client: TestClient) -> None:
    await _approve_plugin(client)

    install_response = client.put("/api/guilds/1111/installations/temp_role_punishment")
    assert install_response.status_code == 200
    assert install_response.json()["plugin_id"] == "temp_role_punishment"

    list_response = client.get("/api/guilds/1111/installations")
    assert [installation["plugin_id"] for installation in list_response.json()] == ["temp_role_punishment"]

    uninstall_response = client.delete("/api/guilds/1111/installations/temp_role_punishment")
    assert uninstall_response.status_code == 200
    assert client.get("/api/guilds/1111/installations").json() == []


async def test_install_blocked_plugin_returns_409(client: TestClient) -> None:
    await _approve_plugin(client)
    client.put("/api/guilds/1111/blocks/temp_role_punishment", json={"reason": "abuse"})

    response = client.put("/api/guilds/1111/installations/temp_role_punishment")
    assert response.status_code == 409


async def test_install_disallowed_tier_returns_403(client: TestClient) -> None:
    await submit_plugin()
    client.post(
        "/api/plugins/temp_role_punishment/approve",
        json={"tier_configs": {"default": default_tier_config(allowed=False)}},
    )

    response = client.put("/api/guilds/1111/installations/temp_role_punishment")
    assert response.status_code == 403


async def test_uninstall_unknown_installation_returns_404(client: TestClient) -> None:
    response = client.delete("/api/guilds/1111/installations/does-not-exist")
    assert response.status_code == 404


async def test_set_quota_override(client: TestClient) -> None:
    await _approve_plugin(client)
    client.put("/api/guilds/1111/installations/temp_role_punishment")

    response = client.put(
        "/api/guilds/1111/installations/temp_role_punishment/quota-override",
        json={"execution_quota": 50, "action_quota": 20},
    )
    assert response.status_code == 200
    assert response.json()["execution_quota_override"] == 50


async def test_set_resource_overrides(client: TestClient) -> None:
    await _approve_plugin(client)
    client.put("/api/guilds/1111/installations/temp_role_punishment")

    response = client.put(
        "/api/guilds/1111/installations/temp_role_punishment/resource-overrides",
        json={"instruction_limit": 2_000_000},
    )
    assert response.status_code == 200
    assert response.json()["resource_overrides_json"] == '{"instruction_limit": 2000000}'


async def test_set_resource_overrides_unknown_installation_returns_404(client: TestClient) -> None:
    response = client.put(
        "/api/guilds/1111/installations/does-not-exist/resource-overrides",
        json={"instruction_limit": 2_000_000},
    )
    assert response.status_code == 404
