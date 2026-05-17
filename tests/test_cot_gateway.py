from whispercatch_sentinel.cot import (
    COT_TYPE_HOSTILE_UAS,
    CotGateway,
    build_drone_events,
)
from whispercatch_sentinel.cuas import DroneContact


def _contact(**kwargs) -> DroneContact:
    base = dict(
        captured_at=0.0,
        source="droneid",
        protocol="OcuSync",
        rssi_dbm=-60.0,
        serial="DJI-Z",
        drone_lat=35.0,
        drone_lon=-117.0,
        drone_alt_m=120.0,
        pilot_lat=35.1,
        pilot_lon=-117.1,
        airframe="Mavic 3",
    )
    base.update(kwargs)
    return DroneContact(**base)


def test_build_drone_events_includes_drone_and_pilot() -> None:
    events = build_drone_events(_contact())
    assert len(events) == 2
    assert COT_TYPE_HOSTILE_UAS in events[0]
    assert 'callsign="Mavic 3"' in events[0]
    assert 'callsign="Pilot"' in events[1]


def test_gateway_broadcasts_per_event() -> None:
    sent = []

    def fake_send(group, port, payload):
        sent.append((group, port, payload))

    gateway = CotGateway("239.2.3.1", 6969, sender=fake_send)
    events = gateway.broadcast(_contact())

    assert len(events) == 2
    assert len(sent) == 2
    assert {s[0] for s in sent} == {"239.2.3.1"}


def test_no_pilot_when_pilot_coords_missing() -> None:
    events = build_drone_events(_contact(pilot_lat=None, pilot_lon=None))
    assert len(events) == 1
