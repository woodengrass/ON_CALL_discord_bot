from fastapi.testclient import TestClient

from core import repository


async def test_get_execution_stats(client: TestClient) -> None:
    await repository.log_execution(1111, "temp_role_punishment", "on_message", "[]", 10, "success")
    await repository.log_execution(1111, "temp_role_punishment", "on_message", "[]", 20, "crashed", "boom")

    response = client.get("/api/plugins/temp_role_punishment/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["outcome_counts"] == {"success": 1, "crashed": 1}


async def test_get_execution_stats_empty(client: TestClient) -> None:
    response = client.get("/api/plugins/temp_role_punishment/stats")
    assert response.status_code == 200
    assert response.json() == {
        "total": 0,
        "outcome_counts": {},
        "avg_execution_ms": None,
        "p95_execution_ms": None,
    }
