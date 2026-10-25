"""Unit tests for the authoritative sequential ledger (docs/ledger-spec.md)."""

import importlib
import importlib.util
import math
from pathlib import Path

import pytest

from tycho.ledger import Instrument, rebalance_orders, run_ledger, slippage_bps

GOLDEN = Path(__file__).resolve().parent / "golden"


def inst(instrument_id, mult=1.0, step=1.0, min_notional=1.0):
    return Instrument(instrument_id, mult, step, min_notional)


def fill(time, instrument_id, qty, price, fee_rate=0.0, slippage_bps=0.0):
    return {
        "time": time,
        "type": "FILL",
        "instrument_id": instrument_id,
        "qty": qty,
        "price": price,
        "fee_rate": fee_rate,
        "slippage_bps": slippage_bps,
    }


def test_golden_cases_run_not_skip():
    """tests/golden skips only when ledger.py is missing; assert that cannot happen."""
    spec = importlib.util.spec_from_file_location("golden_harness", GOLDEN / "test_golden.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    assert harness.LEDGER_PATH.exists(), "golden harness would skip: ledger.py missing"
    assert harness.CASES, "no golden cases collected"
    ledger = importlib.import_module("tycho.ledger")
    for path in harness.CASES:
        case = harness.load_case(path)
        assert hasattr(ledger, case["function"]), f"{path.name}: {case['function']} missing"


def test_event_ordering_ties():
    events = [
        fill("2024-01-02T00:00:00Z", "AAA", -1.0, 100.0),
        {"time": "2024-01-02T00:00:00Z", "type": "DELIST", "instrument_id": "AAA",
         "mark_price": 100.0, "fee_rate": 0.0, "slippage_bps": 0.0},
        {"time": "2024-01-02T00:00:00Z", "type": "FUNDING", "instrument_id": "AAA",
         "rate": 0.001, "mark_price": 100.0},
        {"time": "2024-01-02T00:00:00Z", "type": "MARK", "instrument_id": "AAA",
         "mark_price": 100.0},
        fill("2024-01-01T00:00:00Z", "AAA", 2.0, 100.0),
        fill("2024-01-02T00:00:00Z", "AAA", -1.0, 100.0),  # same-type tie: input order
    ]
    snapshots = run_ledger(1000.0, {"AAA": inst("AAA")}, events)
    assert [s["time"] for s in snapshots] == [
        "2024-01-01T00:00:00Z",
        "2024-01-02T00:00:00Z",
        "2024-01-02T00:00:00Z",
        "2024-01-02T00:00:00Z",
        "2024-01-02T00:00:00Z",
        "2024-01-02T00:00:00Z",
    ]
    assert [s["type"] for s in snapshots] == ["FILL", "MARK", "FUNDING", "DELIST", "FILL", "FILL"]
    # Funding hits the +2 held at the funding instant, before the first same-time sell.
    assert snapshots[2]["funding_cum"] == pytest.approx(-0.2)
    # DELIST closes the position, so the two sells after it change nothing.
    assert snapshots[3]["positions"] == {}
    assert snapshots[4]["wallet"] == snapshots[5]["wallet"] == snapshots[3]["wallet"]


def test_wallet_and_equity_identities_at_every_snapshot():
    instruments = {"AAA": inst("AAA"), "BBB": inst("BBB", mult=10.0, step=0.5, min_notional=1.0)}
    events = [
        fill("2024-01-01T00:00:00Z", "AAA", 2.0, 100.0, fee_rate=0.0005, slippage_bps=5.0),
        fill("2024-01-01T00:00:00Z", "BBB", -3.0, 50.0, fee_rate=0.0005, slippage_bps=10.0),
        {"time": "2024-01-02T00:00:00Z", "type": "FUNDING", "instrument_id": "AAA",
         "rate": 0.001, "mark_price": 100.0},
        {"time": "2024-01-02T00:00:00Z", "type": "FUNDING", "instrument_id": "BBB",
         "rate": 0.001, "mark_price": 55.0},
        {"time": "2024-01-03T00:00:00Z", "type": "MARK", "instrument_id": "AAA",
         "mark_price": 110.0},
        fill("2024-01-04T00:00:00Z", "AAA", -5.0, 90.0, fee_rate=0.0005, slippage_bps=2.0),
        {"time": "2024-01-05T00:00:00Z", "type": "DELIST", "instrument_id": "BBB",
         "mark_price": 60.0, "fee_rate": 0.0005, "slippage_bps": 50.0},
    ]
    snapshots = run_ledger(1000.0, instruments, events)
    assert len(snapshots) == len(events)
    for snapshot in snapshots:
        assert snapshot["wallet"] == pytest.approx(
            1000.0 + snapshot["realized_cum"] - snapshot["fees_cum"] + snapshot["funding_cum"]
        )
        assert snapshot["equity"] == pytest.approx(snapshot["wallet"] + snapshot["unrealized"])
    final = snapshots[-1]
    # AAA: +2@100 then -5@90 crosses zero; BBB: -3 delisted at 60 with 50 bps.
    assert final["positions"] == {"AAA": -3.0}
    assert final["funding_cum"] == pytest.approx(-0.2 + 1.65)
    assert final["fees_cum"] > 0.0


def test_rebalance_pending_arguments_are_required():
    kwargs = dict(
        target_weights={"AAA": 0.2},
        positions={},
        marks={"AAA": 100.0},
        equity=10000.0,
        instruments={"AAA": inst("AAA")},
        band_relative=0.25,
    )
    with pytest.raises(TypeError):
        rebalance_orders(**kwargs, partial_ratio=0.5)
    with pytest.raises(TypeError):
        rebalance_orders(**kwargs, absolute_band_nav=0.02)
    with pytest.raises(TypeError):
        rebalance_orders(**kwargs)


def test_band_boundary_trades_at_exact_threshold():
    # |d| == max(band_relative*|target|, absolute_band_nav) is outside the band ("<" blocks).
    orders = rebalance_orders(
        target_weights={"AAA": 0.1},
        positions={"AAA": 9.0},  # mark 100, equity 10000 -> w_cur=0.09, d=0.01
        marks={"AAA": 100.0},
        equity=10000.0,
        instruments={"AAA": inst("AAA")},
        band_relative=0.1,          # threshold = max(0.01, 0.0) = 0.01
        absolute_band_nav=0.0,
        partial_ratio=1.0,
    )
    assert orders == {"AAA": 1.0}


def test_slippage_floor_and_sqrt():
    assert slippage_bps(2.5e6, 0.04, 1e9) == pytest.approx(20.0)
    assert slippage_bps(1e6, 0.02, 1e12) == pytest.approx(2.0)
    assert slippage_bps(0.0, 0.02, 1e12) == 2.0
    assert math.isclose(slippage_bps(1e9, 0.1, 1e9, y=0.5, min_bps=0.0), 500.0)
