import httpx
import pytest

# Ports as defined in docker-compose.yml
SERVICES = {
    "gateway": "http://localhost:8000",
    "codegen": "http://localhost:8001",
    "connectors": "http://localhost:8002",
    "sandbox": "http://localhost:8003",
    "scheduler": "http://localhost:8004",
    "insights": "http://localhost:8005",
    "orchestrator": "http://localhost:8006",
    "metadata": "http://localhost:8007",
    "uasr": "http://localhost:8009",
}

@pytest.mark.parametrize("name,url", SERVICES.items())
def test_service_health(name, url):
    """Verify that every microservice in the compose stack is healthy."""
    try:
        resp = httpx.get(f"{url}/health", timeout=5.0)
    except httpx.RequestError as exc:
        pytest.fail(f"Failed to connect to {name} at {url}: {exc}")

    assert resp.status_code == 200, f"{name} healthcheck failed with {resp.status_code}"
    # Most services return {"status": "healthy"} or similar
    data = resp.json()
    assert "status" in data or "healthy" in data.values(), f"Unexpected health response from {name}: {data}"

def test_gateway_docs():
    """Verify the API Gateway serves the consolidated OpenAPI Swagger UI."""
    resp = httpx.get(f"{SERVICES['gateway']}/docs")
    assert resp.status_code == 200
    assert "Swagger UI" in resp.text
