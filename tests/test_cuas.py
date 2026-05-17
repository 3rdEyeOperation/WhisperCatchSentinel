import time

from whispercatch_sentinel.cuas import CuasAggregator, DroneContact


def _contact(**kwargs) -> DroneContact:
    base = dict(
        captured_at=time.time(),
        source="droneid",
        protocol="ASTM DRI",
        rssi_dbm=-60.0,
    )
    base.update(kwargs)
    return DroneContact(**base)


def test_deduplicates_by_serial_across_sources() -> None:
    agg = CuasAggregator()
    agg.ingest(_contact(serial="DJI-123", drone_lat=10.0, drone_lon=20.0, rssi_dbm=-70.0))
    agg.ingest(
        _contact(source="wifi", protocol="OcuSync", mac="aa:bb:cc:dd:ee:ff",
                 serial="DJI-123", rssi_dbm=-55.0, pilot_lat=10.5, pilot_lon=20.5)
    )

    snap = agg.snapshot()
    assert len(snap) == 1
    merged = snap[0]
    assert merged.serial == "DJI-123"
    assert merged.drone_lat == 10.0
    assert merged.pilot_lat == 10.5
    assert merged.rssi_dbm == -55.0  # strongest wins
    assert merged.mac == "aa:bb:cc:dd:ee:ff"


def test_distinct_keys_kept_separate() -> None:
    agg = CuasAggregator()
    agg.ingest(_contact(serial="A"))
    agg.ingest(_contact(serial="B"))
    agg.ingest(_contact(mac="11:22:33:44:55:66"))
    assert len(agg.snapshot()) == 3


def test_prune_drops_stale_contacts() -> None:
    agg = CuasAggregator(ttl_seconds=10.0)
    old = _contact(serial="OLD", captured_at=time.time() - 1000)
    fresh = _contact(serial="NEW")
    agg.ingest(old)
    agg.ingest(fresh)

    pruned = agg.prune()
    assert pruned == 1
    assert {c.serial for c in agg.snapshot()} == {"NEW"}


def test_requires_identifier() -> None:
    agg = CuasAggregator()
    try:
        agg.ingest(_contact())
    except ValueError:
        return
    raise AssertionError("expected ValueError without serial/mac/uid")
