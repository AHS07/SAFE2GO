"""Streaming pieces that need no database or broker."""
from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.edge.stream.forwarder import (
    ForwarderState,
    ForwarderStats,
    _Rate,
    acknowledged_prefix,
    drain_eta_seconds,
)
from app.stream.codec import Envelope, MalformedRecord, decode, encode, topic_for

T = datetime(2030, 1, 7, 8, 0, tzinfo=UTC)
APP = Path(__file__).resolve().parents[2] / "app"


def _envelope(**overrides) -> Envelope:
    values = {
        "event_id": "e-1", "kind": "telemetry", "edge_seq": 7, "machine_id": "m-1",
        "event_time": T, "spooled_at": T, "data": {"engine_rpm": 1200.0},
    }
    values.update(overrides)
    return Envelope(**values)


# ---------------------------------------------------------------------------
# Codec
# ---------------------------------------------------------------------------


def test_envelope_round_trips() -> None:
    envelope = _envelope()
    assert decode(encode(envelope)) == envelope


@pytest.mark.parametrize("raw", [
    b"",
    b"not json",
    b'{"v": 2, "kind": "telemetry"}',
    b'{"v": 1, "kind": "weather"}',
    b'{"v": 1, "kind": "telemetry", "event_id": "e", "edge_seq": 1, "machine_id": "m",'
    b' "event_time": "2030-01-07T08:00:00", "spooled_at": "2030-01-07T08:00:00+00:00", "data": {}}',
    b'{"v": 1, "kind": "telemetry", "event_id": "e", "edge_seq": "x", "machine_id": "m",'
    b' "event_time": "2030-01-07T08:00:00+00:00", "spooled_at": "2030-01-07T08:00:00+00:00", "data": {}}',
])
def test_bad_records_are_rejected_as_malformed(raw: bytes) -> None:
    with pytest.raises(MalformedRecord):
        decode(raw)


def test_gaps_travel_with_ticks_and_events_on_their_own_topic() -> None:
    assert topic_for("telemetry", "t", "e") == "t"
    assert topic_for("gap", "t", "e") == "t"
    assert topic_for("incident", "t", "e") == "e"
    assert topic_for("behavior_event", "t", "e") == "e"


# ---------------------------------------------------------------------------
# Forwarder rules
# ---------------------------------------------------------------------------


def _rows(*machines: str) -> list:
    return [SimpleNamespace(seq=i + 1, machine_id=m) for i, m in enumerate(machines)]


def test_acknowledged_prefix_stops_each_machine_at_its_first_failure() -> None:
    rows = _rows("a", "b", "a", "b", "a", "b")
    fail = RuntimeError("no ack")
    results = [None, None, fail, None, None, fail]
    # a: 1 ok, 3 failed -> 5 kept even though acknowledged. b: 2 and 4 ok, 6 failed.
    assert acknowledged_prefix(rows, results) == [1, 2, 4]


def test_acknowledged_prefix_when_all_succeed_or_all_fail() -> None:
    rows = _rows("a", "a", "b")
    assert acknowledged_prefix(rows, [None, None, None]) == [1, 2, 3]
    err = TimeoutError()
    assert acknowledged_prefix(rows, [err, err, err]) == []


def test_drain_eta() -> None:
    stats = ForwarderStats(state=ForwarderState.DRAINING, send_rate=500.0, arrival_rate=100.0)
    assert drain_eta_seconds(2000, stats) == 5.0
    assert drain_eta_seconds(0, stats) == 0.0
    assert drain_eta_seconds(100, ForwarderStats(state=ForwarderState.LINK_DOWN)) is None
    stalled = ForwarderStats(state=ForwarderState.DRAINING, send_rate=100.0, arrival_rate=150.0)
    assert drain_eta_seconds(100, stalled) is None, "arrivals outpace sends: no finish time"


def test_rate_covers_the_elapsed_time_then_a_sliding_window() -> None:
    rate = _Rate()
    rate.add(100.0, 50)
    assert rate.rate(100.0) == 50.0, "less than a second of data counts as one second"
    rate.add(102.0, 50)
    assert rate.rate(102.0) == 50.0
    assert rate.rate(200.0) == 0.0, "samples older than the window drop out"


# ---------------------------------------------------------------------------
# The safety path never imports Kafka
# ---------------------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("relative", [
    "edge/telemetry/ingest.py",
    "edge/telemetry/pipeline.py",
    "edge/safety/engine.py",
    "edge/behavior/engine.py",
    "edge/outbox.py",
    "edge/stream/spool.py",
])
def test_tick_and_safety_modules_do_not_import_kafka(relative: str) -> None:
    imported = _imports(APP / relative)
    assert not any(name.startswith("aiokafka") for name in imported)
    assert "app.stream.kafka" not in imported
    assert "app.edge.stream.forwarder" not in imported
