from fastapi.testclient import TestClient

from tests.web.admin.backend.conftest import default_tier_config, submit_plugin


async def test_list_and_get_plugin(client: TestClient) -> None:
    await submit_plugin()

    list_response = client.get("/api/plugins", params={"status": "pending_review"})
    assert list_response.status_code == 200
    assert [plugin["plugin_id"] for plugin in list_response.json()] == ["temp_role_punishment"]

    detail_response = client.get("/api/plugins/temp_role_punishment")
    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["source_code"] == "function on_message(payload) end"
    assert body["required_capabilities"] == []


async def test_get_plugin_detail_404_for_unknown_plugin(client: TestClient) -> None:
    response = client.get("/api/plugins/does-not-exist")
    assert response.status_code == 404


async def test_approve_plugin_requires_every_tier(client: TestClient) -> None:
    await submit_plugin()

    response = client.post(
        "/api/plugins/temp_role_punishment/approve",
        json={"tier_configs": {"default": default_tier_config()}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


async def test_approve_plugin_missing_tier_returns_400(client: TestClient) -> None:
    from core import repository

    await submit_plugin()
    await repository.create_resource_tier("large", 10, "large guild")

    response = client.post(
        "/api/plugins/temp_role_punishment/approve",
        json={"tier_configs": {"default": default_tier_config()}},
    )
    assert response.status_code == 400


async def test_reject_plugin_structured(client: TestClient) -> None:
    await submit_plugin(required_capabilities=["storage"])

    response = client.post(
        "/api/plugins/temp_role_punishment/reject",
        json={"reason_presets": ["insecure"], "custom_reason": None, "flagged_capabilities": ["storage"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


async def test_reject_plugin_invalid_flagged_capability_returns_400(client: TestClient) -> None:
    await submit_plugin()

    response = client.post(
        "/api/plugins/temp_role_punishment/reject",
        json={"reason_presets": [], "custom_reason": "no", "flagged_capabilities": ["discord_message_send"]},
    )
    assert response.status_code == 400


async def test_ban_and_unban_plugin(client: TestClient) -> None:
    await submit_plugin()
    client.post(
        "/api/plugins/temp_role_punishment/approve",
        json={"tier_configs": {"default": default_tier_config()}},
    )

    ban_response = client.post("/api/plugins/temp_role_punishment/ban", json={"reason": "malicious"})
    assert ban_response.status_code == 200
    assert ban_response.json()["status"] == "banned"

    unban_response = client.post("/api/plugins/temp_role_punishment/unban")
    assert unban_response.status_code == 200
    assert unban_response.json()["status"] == "rejected"


async def test_set_pricing_tier(client: TestClient) -> None:
    await submit_plugin()

    response = client.post("/api/plugins/temp_role_punishment/pricing-tier", json={"pricing_tier": "paid"})
    assert response.status_code == 200
    assert response.json()["pricing_tier"] == "paid"


async def test_set_pricing_tier_invalid_value_returns_400(client: TestClient) -> None:
    await submit_plugin()

    response = client.post("/api/plugins/temp_role_punishment/pricing-tier", json={"pricing_tier": "gold"})
    assert response.status_code == 400
