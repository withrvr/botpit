from fastapi.testclient import TestClient

from app.core.config import settings


def test_health_is_public_and_cheap(client: TestClient) -> None:
    r = client.get(f"{settings.API_V1_STR}/health/")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] == "ok"
    assert body["event_bus"].endswith(":ok")
    assert body["llm"] in {"ok", "unreachable", "unchecked"} or body["llm"].startswith(
        "degraded"
    )
    assert "http" not in body["llm"]  # no base URL leaks through the public probe
    assert "x-request-id" in r.headers


def test_health_llm_requires_superuser(client: TestClient) -> None:
    assert client.get(f"{settings.API_V1_STR}/health/llm").status_code == 401


def test_health_llm_reports_for_superuser(
    client: TestClient, superuser_token_headers: dict[str, str]
) -> None:
    r = client.get(f"{settings.API_V1_STR}/health/llm", headers=superuser_token_headers)
    assert r.status_code in {200, 503}
    body = r.json()
    assert body["provider"] == settings.LLM_PROVIDER
    assert set(body["models_present"]) | set(body["models_missing"]) == set(
        settings.LLM_MODELS
    )
    # second call within the TTL is served from cache: same checked_at
    again = client.get(
        f"{settings.API_V1_STR}/health/llm", headers=superuser_token_headers
    )
    assert again.json()["checked_at"] == body["checked_at"]
