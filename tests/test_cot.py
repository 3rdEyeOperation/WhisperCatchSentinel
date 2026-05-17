from whispercatch_sentinel.cot import build_cot_event


def test_build_cot_event_contains_coordinates_and_uid() -> None:
    payload = build_cot_event(uid="drone-123", lat=35.0, lon=-117.0)

    assert 'uid="drone-123"' in payload
    assert 'lat="35.0"' in payload
    assert 'lon="-117.0"' in payload
    assert payload.startswith("<event")
    assert payload.endswith("</event>")
