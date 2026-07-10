from fastapi.testclient import TestClient

from tests.web.admin.backend.conftest import default_tier_config, submit_plugin


async def test_create_list_update_delete_tier(client: TestClient) -> None:
    create_response = client.post(
        "/api/tiers", json={"tier_name": "large", "display_order": 10, "description": "大型伺服器"}
    )
    assert create_response.status_code == 200
    assert create_response.json()["tier_name"] == "large"

    list_response = client.get("/api/tiers")
    tier_names = {tier["tier_name"] for tier in list_response.json()}
    assert {"default", "large"} <= tier_names

    update_response = client.put("/api/tiers/large", json={"display_order": 20, "description": "更新過的說明"})
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "更新過的說明"

    delete_response = client.delete("/api/tiers/large")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}


async def test_update_unknown_tier_returns_404(client: TestClient) -> None:
    response = client.put("/api/tiers/does-not-exist", json={"display_order": 1, "description": "x"})
    assert response.status_code == 404


async def test_delete_unknown_tier_returns_404(client: TestClient) -> None:
    response = client.delete("/api/tiers/does-not-exist")
    assert response.status_code == 404


async def test_get_and_update_plugin_tier_config(client: TestClient) -> None:
    await submit_plugin()
    client.post(
        "/api/plugins/temp_role_punishment/approve",
        json={"tier_configs": {"default": default_tier_config()}},
    )

    get_response = client.get("/api/tiers/default/plugins/temp_role_punishment")
    assert get_response.status_code == 200
    assert get_response.json()["allowed"] is True

    update_response = client.put(
        "/api/tiers/default/plugins/temp_role_punishment",
        json=default_tier_config(allowed=False),
    )
    assert update_response.status_code == 200
    assert update_response.json()["allowed"] is False


async def test_get_plugin_tier_config_404_when_not_set(client: TestClient) -> None:
    await submit_plugin()

    response = client.get("/api/tiers/default/plugins/temp_role_punishment")
    assert response.status_code == 404


async def test_get_resource_defaults(client: TestClient) -> None:
    response = client.get("/api/tiers/resource-defaults")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "execution_quota",
        "action_quota",
        "storage_key_length_limit",
        "storage_value_bytes_limit",
        "storage_keys_per_installation_limit",
        "instruction_limit",
        "memory_limit_bytes",
    }
