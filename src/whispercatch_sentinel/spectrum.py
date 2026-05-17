"""HackRF One spectrum sweep + analog FPV detection pipeline.

The pipeline orchestrates a headless `SDRangel` REST instance to sweep the
2.4 GHz and 5.8 GHz ISM bands. When a candidate carrier looks like an
analog FM video downlink (continuous FM, wide bandwidth, persistent power),
an ATV channel plugin is requested so MJPEG frames can be streamed.
Anything else that cannot be classified is funnelled to the heatmap engine
as raw ``(frequency, rssi)`` evidence.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Protocol


log = logging.getLogger(__name__)


# Analog FPV video typically uses ~18-20 MHz wide FM carriers, with very high
# duty-cycle on the channel center. These thresholds are deliberately
# conservative to avoid spinning up the ATV decoder on bursty links.
ANALOG_FPV_MIN_BANDWIDTH_HZ = 6_000_000
ANALOG_FPV_MIN_DUTY_CYCLE = 0.85
ANALOG_FPV_MIN_RSSI_DBM = -75.0


# Channelization helpers ------------------------------------------------------
def band_channels_hz(start_hz: int, stop_hz: int, step_hz: int) -> list[int]:
    if step_hz <= 0:
        raise ValueError("step_hz must be positive")
    if stop_hz < start_hz:
        raise ValueError("stop_hz must be >= start_hz")
    return list(range(start_hz, stop_hz + 1, step_hz))


DEFAULT_SWEEPS: dict[str, tuple[int, int, int]] = {
    "2.4GHz": (2_400_000_000, 2_483_500_000, 1_000_000),
    "5.8GHz": (5_650_000_000, 5_925_000_000, 1_000_000),
}


@dataclass(frozen=True)
class SweepSample:
    frequency_hz: int
    rssi_dbm: float
    bandwidth_hz: int
    duty_cycle: float  # 0.0..1.0 fraction of dwell with power > noise floor
    modulation_hint: str = "unknown"


@dataclass(frozen=True)
class Classification:
    frequency_hz: int
    rssi_dbm: float
    bandwidth_hz: int
    kind: str          # one of: "analog_fpv", "unknown"
    reason: str


class SDRangelClient(Protocol):
    """Async port for the SDRangel REST API used during sweeps."""

    async def sweep(self, start_hz: int, stop_hz: int, step_hz: int) -> Iterable[SweepSample]: ...

    async def start_atv_channel(self, center_hz: int, bandwidth_hz: int) -> str: ...

    async def stop_channel(self, channel_id: str) -> None: ...


def classify_sample(sample: SweepSample) -> Classification:
    """Decide whether a sweep sample looks like analog FPV video."""
    if (
        sample.bandwidth_hz >= ANALOG_FPV_MIN_BANDWIDTH_HZ
        and sample.duty_cycle >= ANALOG_FPV_MIN_DUTY_CYCLE
        and sample.rssi_dbm >= ANALOG_FPV_MIN_RSSI_DBM
        and sample.modulation_hint in {"fm", "unknown"}
    ):
        return Classification(
            frequency_hz=sample.frequency_hz,
            rssi_dbm=sample.rssi_dbm,
            bandwidth_hz=sample.bandwidth_hz,
            kind="analog_fpv",
            reason="wide continuous FM carrier consistent with analog FPV downlink",
        )
    return Classification(
        frequency_hz=sample.frequency_hz,
        rssi_dbm=sample.rssi_dbm,
        bandwidth_hz=sample.bandwidth_hz,
        kind="unknown",
        reason="emitter could not be matched to known protocol",
    )


class SpectrumPipeline:
    """Async orchestrator coordinating sweeps, ATV spin-up, and heatmap fanout."""

    def __init__(
        self,
        client: SDRangelClient,
        *,
        on_unknown: Callable[[Classification], Awaitable[None]],
        on_analog_fpv: Callable[[Classification, str], Awaitable[None]],
        sweeps: dict[str, tuple[int, int, int]] | None = None,
    ) -> None:
        self._client = client
        self._on_unknown = on_unknown
        self._on_analog_fpv = on_analog_fpv
        self._sweeps = dict(sweeps or DEFAULT_SWEEPS)
        self._active_channels: dict[int, str] = {}

    async def run_once(self) -> list[Classification]:
        """Run a single pass across all configured bands."""
        results: list[Classification] = []
        for band, (start, stop, step) in self._sweeps.items():
            log.debug("sweeping %s (%d-%d Hz)", band, start, stop)
            try:
                samples = await self._client.sweep(start, stop, step)
            except (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError):
                # SDRangel is reached over the local REST socket; treat
                # network-level errors as transient and continue with the
                # next band so a single hiccup never halts the sweep loop.
                log.exception("sweep failed for %s; continuing", band)
                continue
            for sample in samples:
                classification = classify_sample(sample)
                results.append(classification)
                if classification.kind == "analog_fpv":
                    await self._handle_analog_fpv(classification)
                else:
                    await self._on_unknown(classification)
        return results

    async def _handle_analog_fpv(self, classification: Classification) -> None:
        center = classification.frequency_hz
        if center in self._active_channels:
            return
        try:
            channel_id = await self._client.start_atv_channel(
                center, classification.bandwidth_hz
            )
        except (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError):
            log.exception("failed to start ATV channel at %d Hz", center)
            return
        self._active_channels[center] = channel_id
        await self._on_analog_fpv(classification, channel_id)

    async def teardown(self) -> None:
        for center, channel_id in list(self._active_channels.items()):
            try:
                await self._client.stop_channel(channel_id)
            except (OSError, ConnectionError, TimeoutError, asyncio.TimeoutError):
                log.exception("failed to stop ATV channel %s", channel_id)
            self._active_channels.pop(center, None)
