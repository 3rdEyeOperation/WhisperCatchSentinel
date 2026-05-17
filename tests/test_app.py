from fastapi.testclient import TestClient

from whispercatch_sentinel.app import create_app


def test_health_endpoint_is_headless_and_cloud_free() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["headless"] is True
    assert body["cloud_processing"] is False


def test_status_endpoint_exposes_required_device_profiles() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/config/status")

    assert response.status_code == 200
    body = response.json()
    names = {item["name"] for item in body["devices"]}
    assert {
        "HackRF One",
        "RTL-SDR V4",
        "Alfa USB Wi-Fi",
        "Sniffle BLE Coded PHY",
    }.issubset(names)


def test_cot_endpoint_sends_event(monkeypatch) -> None:
    sent = {}

    def fake_send(group: str, port: int, payload: str) -> None:
        sent["group"] = group
        sent["port"] = port
        sent["payload"] = payload

    monkeypatch.setattr("whispercatch_sentinel.api.factory.multicast_cot", fake_send)

    client = TestClient(create_app())
    response = client.post(
        "/api/v1/cot",
        json={"uid": "drone-42", "lat": 34.1, "lon": -117.2, "hae": 10.0},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "sent", "uid": "drone-42"}
    assert sent["group"] == "239.2.3.1"
    assert sent["port"] == 6969
    assert 'uid="drone-42"' in sent["payload"]
