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


def test_dashboard_vendored_leaflet_is_served_offline() -> None:
    client = TestClient(create_dashboard_app())

    js = client.get("/assets/vendor/leaflet/leaflet.js")
    css = client.get("/assets/vendor/leaflet/leaflet.css")
    heat = client.get("/assets/vendor/leaflet/leaflet-heat.js")

    assert js.status_code == 200
    assert css.status_code == 200
    assert heat.status_code == 200
    # Sanity-check the bundled libraries — these strings are part of the
    # upstream Leaflet / Leaflet.heat source and confirm the vendored files
    # are intact and reachable without any external network call.
    assert "L.Map" in js.text or "Leaflet" in js.text
    assert ".leaflet-container" in css.text
    assert "heatLayer" in heat.text
