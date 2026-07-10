from fastapi.testclient import TestClient


async def test_get_default_resource_tier(client: TestClient) -> None:
    response = client.get("/api/guilds/1111/resource-tier")
    assert response.status_code == 200
    assert response.json() == {"guild_id": 1111, "tier_name": "default"}


async def test_set_and_get_resource_tier(client: TestClient) -> None:
    client.post("/api/tiers", json={"tier_name": "large", "display_order": 10, "description": "大型伺服器"})

    set_response = client.put("/api/guilds/1111/resource-tier", json={"tier_name": "large"})
    assert set_response.status_code == 200

    get_response = client.get("/api/guilds/1111/resource-tier")
    assert get_response.json()["tier_name"] == "large"


async def test_block_list_and_unblock_plugin(client: TestClient) -> None:
    block_response = client.put("/api/guilds/1111/blocks/temp_role_punishment", json={"reason": "abuse"})
    assert block_response.status_code == 200
    assert block_response.json()["reason"] == "abuse"

    list_response = client.get("/api/guilds/1111/blocks")
    assert [block["plugin_id"] for block in list_response.json()] == ["temp_role_punishment"]

    unblock_response = client.delete("/api/guilds/1111/blocks/temp_role_punishment")
    assert unblock_response.status_code == 200
    assert client.get("/api/guilds/1111/blocks").json() == []


async def test_unblock_unknown_block_returns_404(client: TestClient) -> None:
    response = client.delete("/api/guilds/1111/blocks/temp_role_punishment")
    assert response.status_code == 404


async def test_list_guilds_returns_known_guild_ids(client: TestClient) -> None:
    client.put("/api/guilds/2222/resource-tier", json={"tier_name": "default"})
    client.put("/api/guilds/1111/blocks/temp_role_punishment", json={"reason": "abuse"})

    response = client.get("/api/guilds")

    assert response.status_code == 200
    assert response.json() == [1111, 2222]
