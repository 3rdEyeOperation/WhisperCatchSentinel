import asyncio

from whispercatch_sentinel.spectrum import (
    SpectrumPipeline,
    SweepSample,
    classify_sample,
)


def test_classify_wide_continuous_fm_as_analog_fpv() -> None:
    sample = SweepSample(
        frequency_hz=5_800_000_000,
        rssi_dbm=-55.0,
        bandwidth_hz=18_000_000,
        duty_cycle=0.95,
        modulation_hint="fm",
    )
    result = classify_sample(sample)
    assert result.kind == "analog_fpv"


def test_classify_narrow_bursty_as_unknown() -> None:
    sample = SweepSample(
        frequency_hz=2_440_000_000,
        rssi_dbm=-65.0,
        bandwidth_hz=1_500_000,
        duty_cycle=0.2,
        modulation_hint="unknown",
    )
    result = classify_sample(sample)
    assert result.kind == "unknown"


def test_classify_weak_signal_as_unknown_even_if_wide() -> None:
    sample = SweepSample(
        frequency_hz=5_800_000_000,
        rssi_dbm=-95.0,
        bandwidth_hz=20_000_000,
        duty_cycle=0.9,
        modulation_hint="fm",
    )
    assert classify_sample(sample).kind == "unknown"


class _FakeClient:
    def __init__(self, samples):
        self._samples = samples
        self.atv_calls = []

    async def sweep(self, start, stop, step):
        return [s for s in self._samples if start <= s.frequency_hz <= stop]

    async def start_atv_channel(self, center_hz, bandwidth_hz):
        self.atv_calls.append((center_hz, bandwidth_hz))
        return f"ch-{center_hz}"

    async def stop_channel(self, channel_id):  # pragma: no cover - not used
        pass


def test_pipeline_routes_classifications() -> None:
    fpv = SweepSample(5_800_000_000, -55.0, 18_000_000, 0.95, "fm")
    unk = SweepSample(2_440_000_000, -65.0, 2_000_000, 0.2)
    client = _FakeClient([fpv, unk])
    unknowns = []
    analogs = []

    async def on_unknown(c):
        unknowns.append(c)

    async def on_analog(c, channel_id):
        analogs.append((c, channel_id))

    pipeline = SpectrumPipeline(
        client,
        on_unknown=on_unknown,
        on_analog_fpv=on_analog,
        sweeps={
            "2.4GHz": (2_400_000_000, 2_483_500_000, 1_000_000),
            "5.8GHz": (5_650_000_000, 5_925_000_000, 1_000_000),
        },
    )
    results = asyncio.run(pipeline.run_once())
    kinds = {r.kind for r in results}
    assert kinds == {"analog_fpv", "unknown"}
    assert len(unknowns) == 1
    assert len(analogs) == 1
    assert analogs[0][1] == "ch-5800000000"
