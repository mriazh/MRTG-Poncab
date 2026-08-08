"""Unit tests for RouterOS traffic collector and rate calculation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mrtg_poncab.collector import (
    DEFAULT_MAX_SANE_BPS,
    TrafficCollector,
    calculate_rate,
)
from mrtg_poncab.db import Database


def test_calculate_rate_first_baseline_sample() -> None:
    """The first sample has no previous reference and should yield 0.0 bps with UP status."""
    result = calculate_rate(
        prev_rx_bytes=None,
        prev_tx_bytes=None,
        prev_epoch=None,
        curr_rx_bytes=100_000,
        curr_tx_bytes=200_000,
        curr_epoch=1_000,
    )
    assert result.rx_bps == 0.0
    assert result.tx_bps == 0.0
    assert result.status == "UP"
    assert result.should_store is True


def test_calculate_rate_normal_progression() -> None:
    """Normal delta calculation converts byte delta to bits per second accurately."""
    # 5 minutes (300s), 300,000,000 bytes delta = 2.4 Gb / 300s = 8,000,000 bps = 8 Mbps
    result = calculate_rate(
        prev_rx_bytes=1_000_000_000,
        prev_tx_bytes=2_000_000_000,
        prev_epoch=1_000.0,
        curr_rx_bytes=1_300_000_000,
        curr_tx_bytes=2_150_000_000,
        curr_epoch=1_300.0,
    )
    assert result.status == "UP"
    assert result.should_store is True
    # rx: 300,000,000 * 8 / 300 = 8,000,000 bps
    assert result.rx_bps == 8_000_000.0
    # tx: 150,000,000 * 8 / 300 = 4,000,000 bps
    assert result.tx_bps == 4_000_000.0


def test_calculate_rate_counter_rollover_or_reboot() -> None:
    """When octet counters decrease, detect RESET and suppress false rate spikes."""
    result = calculate_rate(
        prev_rx_bytes=5_000_000_000,
        prev_tx_bytes=5_000_000_000,
        prev_epoch=1_000.0,
        curr_rx_bytes=100_000,  # router rebooted
        curr_tx_bytes=200_000,
        curr_epoch=1_300.0,
    )
    assert result.status == "RESET"
    assert result.rx_bps == 0.0
    assert result.tx_bps == 0.0
    assert result.should_store is True


def test_calculate_rate_non_positive_interval_is_discarded() -> None:
    """Zero or negative time intervals are discarded to prevent division by zero."""
    result = calculate_rate(
        prev_rx_bytes=100_000,
        prev_tx_bytes=100_000,
        prev_epoch=1_000.0,
        curr_rx_bytes=200_000,
        curr_tx_bytes=200_000,
        curr_epoch=1_000.0,  # same timestamp
    )
    assert result.should_store is False


def test_calculate_rate_implausible_spike_is_guarded() -> None:
    """Rates exceeding the physical threshold (>10 Gbps) are flagged RESET."""
    result = calculate_rate(
        prev_rx_bytes=1_000,
        prev_tx_bytes=1_000,
        prev_epoch=1_000.0,
        curr_rx_bytes=100_000_000_000,  # enormous corrupt jump
        curr_tx_bytes=1_000,
        curr_epoch=1_001.0,  # in 1 second
        max_sane_bps=DEFAULT_MAX_SANE_BPS,
    )
    assert result.status == "RESET"
    assert result.rx_bps == 0.0


class DummyRouterOSClient:
    """Mock RouterOS API client for deterministic offline testing."""

    def __init__(self, responses: list[dict[str, Any] | Exception]) -> None:
        self.responses = list(responses)
        self.disconnect_called = False

    def query_interface(self, interface_name: str) -> dict[str, Any]:
        if not self.responses:
            raise RuntimeError("No more mock responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def disconnect(self) -> None:
        self.disconnect_called = True


def test_traffic_collector_poll_lifecycle(tmp_path: Path) -> None:
    """Test collector poll_once storing baseline, normal delta, and down event."""
    db_path = tmp_path / "test_traffic.db"
    database = Database(db_path)
    database.initialize()

    mock_client = DummyRouterOSClient(
        [
            {"name": "WAN", "rx_bytes": 1_000_000, "tx_bytes": 2_000_000, "uptime": "1d"},
            {"name": "WAN", "rx_bytes": 1_300_000, "tx_bytes": 2_150_000, "uptime": "1d"},
            ConnectionResetError("Socket reset by peer"),
        ]
    )

    now = 1_700_000_000.0
    collector = TrafficCollector(
        database=database,
        client=mock_client,  # type: ignore[arg-type]
        clock=lambda: now,
    )

    # 1. First sample (baseline)
    s1 = collector.poll_once()
    assert s1 is not None
    assert s1.rx_bps == 0.0
    assert s1.status == "UP"

    # Advance time 300 seconds (5 min)
    now += 300.0
    # rx delta: 300,000 * 8 / 300 = 8,000 bps
    # tx delta: 150,000 * 8 / 300 = 4,000 bps
    s2 = collector.poll_once()
    assert s2 is not None
    assert s2.rx_bps == 8_000.0
    assert s2.tx_bps == 4_000.0
    assert s2.status == "UP"

    # Advance time and trigger connection failure
    now += 300.0
    s3 = collector.poll_once()
    assert s3 is not None
    assert s3.status == "DOWN"
    assert s3.rx_bps == 0.0
    assert mock_client.disconnect_called is True

    # Verify all records stored in SQLite
    samples = database.get_traffic_samples(start=0, end=2_000_000_000)
    assert len(samples) == 3
    assert samples[0]["status"] == "UP"
    assert samples[1]["status"] == "UP"
    assert samples[2]["status"] == "DOWN"
