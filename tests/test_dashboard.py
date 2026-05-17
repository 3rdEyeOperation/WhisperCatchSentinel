from fastapi.testclient import TestClient

from whispercatch_sentinel.dashboard.app import create_dashboard_app


def test_dashboard_index_injects_backend_url() -> None:
    client = TestClient(create_dashboard_app(backend_base_url="http://backend.internal:8000"))

    response = client.get("/")

    assert response.status_code == 200
    assert "WhisperCatch Sentinel Dashboard" in response.text
    assert '"backendBaseUrl": "http://backend.internal:8000"' in response.text


def test_dashboard_assets_are_served() -> None:
    client = TestClient(create_dashboard_app())

    response = client.get("/assets/dashboard.js")

    assert response.status_code == 200
    assert "connectStream" in response.text
