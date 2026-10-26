"""Blind reference tests: only docs/ledger-spec.md and protected golden inputs.

Production modules may be called, never inspected. Frozen tolerances are USDT,
not returns; both daily increments and total PnL must pass without relative slack.
"""

from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import tomllib

import numpy as np
import pytest

from tycho import ledger
from tycho import reference_ledger as reference
from tycho import scanner


ROOT = Path(__file__).resolve().parents[1]
TOLERANCES = tomllib.loads((ROOT / "config/parity.toml").read_text())
SCANNER_BOUND = tomllib.loads((ROOT / "config/scanner_bound.toml").read_text())["max_relative_equity_diff"]
CASES = sorted((ROOT / "tests" / "golden" / "cases").glob("*.json"))
METRICS = ("wallet", "equity", "unrealized", "realized_cum", "fees_cum", "funding_cum")


def nonzero(mapping):
    return {key: value for key, value in mapping.items() if value}


def daily_pnl(snapshots, initial, days):
    closing = {snapshot["time"][:10]: snapshot["equity"] for snapshot in snapshots}
    previous = initial
    result = []
    for day in days:
        equity = closing.get(day, previous)
        result.append(equity - previous)
        previous = equity
    return np.asarray(result, dtype=np.float64)


def daily_equity(snapshots, initial, days):
    closing = {snapshot["time"][:10]: snapshot["equity"] for snapshot in snapshots}
    previous = initial
    result = []
    for day in days:
        previous = closing.get(day, previous)
        result.append(previous)
    return np.asarray(result, dtype=np.float64)


def max_relative_equity_diff(scanner_equity, ledger_equity):
    assert scanner_equity.shape == ledger_equity.shape, "equity calendar mismatch"
    assert np.isfinite(scanner_equity).all() and np.isfinite(ledger_equity).all(), "nonfinite equity"
    return float(np.max(np.abs(scanner_equity - ledger_equity) / ledger_equity, initial=0))


def assert_parity(actual, expected):
    assert actual.shape == expected.shape, "daily calendar mismatch"
    assert np.isfinite(actual).all() and np.isfinite(expected).all(), "nonfinite PnL"
    assert np.max(np.abs(actual - expected), initial=0) <= TOLERANCES["daily_pnl_abs_diff"], "daily PnL mismatch"
    assert abs(actual.sum() - expected.sum()) <= TOLERANCES["total_pnl_abs_diff"], "total PnL mismatch"


def test_daily_and_total_guards_are_independent():
    with pytest.raises(AssertionError, match="daily PnL mismatch"):
        assert_parity(np.array([2e-8, -2e-8]), np.zeros(2))
    with pytest.raises(AssertionError, match="total PnL mismatch"):
        assert_parity(np.full(30, 5e-9), np.zeros(30))
    with pytest.raises(AssertionError, match="nonfinite"):
        assert_parity(np.array([float("nan")]), np.zeros(1))


@pytest.mark.parametrize("path", CASES, ids=lambda path: path.stem)
def test_reference_matches_protected_goldens(path):
    case = json.loads(path.read_text())
    inputs = deepcopy(case["inputs"])
    if "instruments" in inputs:
        inputs["instruments"] = {
            key: reference.Instrument(**spec) for key, spec in inputs["instruments"].items()
        }
    expected = case["expected"]
    if case["function"] == "slippage_bps":
        assert reference.slippage_bps(**inputs) == pytest.approx(expected["bps"], abs=1e-9, rel=0)
    elif case["function"] == "rebalance_orders":
        orders = reference.rebalance_orders(**inputs, zero_crossing_rule="full_trade_on_zero_or_cross", synthetic=True)
        assert nonzero(orders) == expected["orders"]
    else:
        snapshots = reference.run_ledger(**inputs, synthetic=True)
        for key in METRICS:
            assert snapshots[-1][key] == pytest.approx(expected[key], abs=1e-9, rel=0)
        assert nonzero(snapshots[-1]["positions"]) == expected["positions"]
        if "equity_series" in expected:
            assert [row["equity"] for row in snapshots] == pytest.approx(expected["equity_series"], abs=1e-9, rel=0)


def synthetic_tape(capital):
    scale = capital / 500
    specs = {
        "AAA": dict(instrument_id="AAA", contract_multiplier=1.0, step_size=0.001, min_notional=1.0),
        "BBB": dict(instrument_id="BBB", contract_multiplier=1000.0, step_size=0.001, min_notional=1.0),
    }
    events = []

    def event(day, hour, kind, symbol, **fields):
        events.append(dict(time=f"2024-01-{day:02}T{hour:02}:00:00Z", type=kind, instrument_id=symbol, **fields))

    def fill(day, hour, symbol, qty, price):
        event(day, hour, "FILL", symbol, qty=qty * scale, price=price, fee_rate=0.0005, slippage_bps=2.0)

    fill(1, 1, "AAA", 1.0, 100.0)
    fill(1, 1, "BBB", -2.0, 0.05)
    event(2, 0, "MARK", "AAA", mark_price=110.0)
    event(2, 0, "MARK", "BBB", mark_price=0.045)
    fill(2, 12, "AAA", -1.5, 110.0)
    event(2, 16, "FUNDING", "AAA", rate=0.001, mark_price=111.0)
    event(2, 16, "FUNDING", "BBB", rate=-0.0003, mark_price=0.046)
    fill(3, 12, "AAA", 0.25, 105.0)
    event(3, 12, "FUNDING", "AAA", rate=0.002, mark_price=105.0)
    fill(3, 12, "BBB", -1.0, 0.04)
    event(3, 23, "MARK", "AAA", mark_price=103.0)
    event(4, 12, "DELIST", "BBB", mark_price=0.043, fee_rate=0.0005, slippage_bps=50.0)
    event(4, 12, "FUNDING", "BBB", rate=0.001, mark_price=0.043)
    event(4, 12, "MARK", "BBB", mark_price=0.043)
    event(5, 0, "MARK", "BBB", mark_price=0.06)
    event(5, 0, "MARK", "AAA", mark_price=99.0)
    return specs, events


def run(module, capital, specs, events):
    instruments = {key: module.Instrument(**spec) for key, spec in specs.items()}
    options = {"synthetic": True} if module is reference else {}
    return module.run_ledger(capital, instruments, deepcopy(events), **options)


@pytest.mark.parametrize("capital", [500.0, 100000.0])
def test_daily_and_total_sequential_parity(capital):
    specs, events = synthetic_tape(capital)
    expected = run(reference, capital, specs, events)
    actual = run(ledger, capital, specs, events)
    days = [f"2024-01-{day:02}" for day in range(1, 6)]
    assert_parity(daily_pnl(actual, capital, days), daily_pnl(expected, capital, days))
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected):
        assert (got["time"], got["type"], got["instrument_id"]) == (want["time"], want["type"], want["instrument_id"])
        for key in METRICS:
            assert abs(got[key] - want[key]) <= TOLERANCES["daily_pnl_abs_diff"]
        assert nonzero(got["positions"]) == nonzero(want["positions"])
        for symbol in nonzero(want["positions"]):
            assert got["entry"][symbol] == pytest.approx(want["entry"][symbol], abs=1e-9, rel=0)


@pytest.mark.parametrize("capital", [500.0, 100000.0])
@pytest.mark.parametrize("fault", ["wrong_fee", "funding_at_bar_start", "lookahead_bar", "missing_delist"])
def test_seeded_faults_break_daily_and_total_parity(capital, fault):
    specs, events = synthetic_tape(capital)
    corrupt = deepcopy(events)
    if fault == "wrong_fee":
        for event in corrupt:
            if "fee_rate" in event:
                event["fee_rate"] *= 2
    elif fault == "funding_at_bar_start":
        for event in corrupt:
            if event["type"] == "FUNDING":
                event["time"] = event["time"][:11] + "00:00:00Z"
    elif fault == "lookahead_bar":
        corrupt[0]["price"] = 110.0  # next day's mark, unavailable at the opening fill
    else:
        corrupt = [event for event in corrupt if event["type"] != "DELIST"]
    days = [f"2024-01-{day:02}" for day in range(1, 6)]
    expected = daily_pnl(run(reference, capital, specs, events), capital, days)
    actual = daily_pnl(run(ledger, capital, specs, corrupt), capital, days)
    assert np.max(np.abs(actual - expected)) > TOLERANCES["daily_pnl_abs_diff"]
    assert abs(actual.sum() - expected.sum()) > TOLERANCES["total_pnl_abs_diff"]
    with pytest.raises(AssertionError, match="PnL mismatch"):
        assert_parity(actual, expected)


def test_calendar_carries_equity_over_empty_days():
    rows = [{"time": "2024-01-02T12:00:00Z", "equity": 503.0}]
    days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(4)]
    assert daily_pnl(rows, 500.0, days).tolist() == [0, 3, 0, 0]


def test_policy_is_required_and_real_runs_refuse():
    specs, _ = synthetic_tape(500)
    instruments = {key: reference.Instrument(**spec) for key, spec in specs.items()}
    args = ({"AAA": -0.01}, {"AAA": 1.0}, {"AAA": 100.0}, 500.0, instruments, 0.25, 0.01, 0.5)
    with pytest.raises(TypeError):
        reference.rebalance_orders(*args, synthetic=True)
    with pytest.raises(ValueError, match="D-39"):
        reference.rebalance_orders(*args, zero_crossing_rule="PENDING", synthetic=True)
    with pytest.raises(ValueError, match="D-39"):
        reference.rebalance_orders(*args, zero_crossing_rule="full_trade", synthetic=True)
    with pytest.raises(ValueError, match="synthetic-only"):
        reference.rebalance_orders(*args, zero_crossing_rule="full_trade_on_zero_or_cross", synthetic=False)
    with pytest.raises(ValueError, match="synthetic-only"):
        reference.run_ledger(500, instruments, [], synthetic=False)


@pytest.mark.parametrize("bad_event", [
    dict(type="UNKNOWN", time="2024-01-01T00:00:00Z", instrument_id="AAA"),
    dict(type="MARK", time="2024-01-01T00:00:00", instrument_id="AAA", mark_price=100),
    dict(type="MARK", time="2024-01-01T00:00:00Z", instrument_id="AAA", mark_price=float("nan")),
    dict(type="MARK", time="2024-01-01T00:00:00Z", instrument_id="AAA", mark_price=-1),
    dict(type="FILL", time="2024-01-01T00:00:00Z", instrument_id="AAA", qty=1, price=100, fee_rate=-0.01, slippage_bps=0),
])
def test_invalid_events_refuse(bad_event):
    specs, _ = synthetic_tape(500)
    with pytest.raises(ValueError):
        run(reference, 500, specs, [bad_event])


def scanner_tape(capital, variant):
    """Imposed zero-cost fills: exact overlap of scanner and event accounting.

    Funding is paid at the preceding mark before each price move. This is an
    explicit diagnostic tape, not the E-001 execution/cost model.
    Scanner API documentation: owner reply on #47 (no source inspection).
    """
    weights = np.array([
        [.2, -.1], [.1, -.15], [-.1, .2], [.05, 0], [0, 0], [0, 0],
    ], dtype=np.float64) * variant
    returns = np.array([
        [0, 0], [.03, -.02], [-.02, .04], [.01, -.01], [.04, .2], [-.02, .3],
    ], dtype=np.float64)
    funding = np.array([
        [0, 0], [.001, -.0002], [-.0001, .0003], [.0002, .0001], [0, 0], [0, 0],
    ], dtype=np.float64)
    specs, _ = synthetic_tape(capital)
    prices = np.array([100.0, .05])
    multipliers = np.array([1.0, 1000.0])
    positions, previous = np.zeros(2), np.zeros(2)
    nav, events = capital, []
    for t, target in enumerate(weights):
        day = f"2024-02-{t + 1:02}"
        for a, symbol in enumerate(specs):
            events.append(dict(time=day + "T00:00:00Z", type="FUNDING",
                               instrument_id=symbol, mark_price=prices[a], rate=funding[t, a]))
        nav *= 1 + previous @ (returns[t] - funding[t])
        prices *= 1 + returns[t]
        wanted = target * nav / (prices * multipliers)
        for a, symbol in enumerate(specs):
            events.append(dict(time=day + "T12:00:00Z", type="MARK",
                               instrument_id=symbol, mark_price=prices[a]))
            if t == 3 and symbol == "BBB":
                events.append(dict(time=day + "T12:00:01Z", type="DELIST",
                                   instrument_id=symbol, mark_price=prices[a], fee_rate=0.0, slippage_bps=0.0))
            elif wanted[a] != positions[a]:
                events.append(dict(time=day + "T12:00:01Z", type="FILL",
                                   instrument_id=symbol, qty=wanted[a] - positions[a], price=prices[a],
                                   fee_rate=0.0, slippage_bps=0.0))
        positions, previous = wanted, target
    return specs, events, weights[None, :, :], returns, funding


def scanner_daily(capital, weights, returns, costs, funding):
    result = scanner.scan(weights, returns, costs, funding)
    equity = capital * result["equity"][0]
    return np.diff(np.concatenate(([capital], equity)))


def cost_scanner_tape(capital, variant, fee_rate=0.0005, slippage_bps=2.0, delist_slippage_bps=50.0):
    """Same imposed-fill diagnostic with real per-fill costs and a delist.

    Each cost input equals fee_rate + slippage on the notional traded that day.
    Funding is paid at the preceding mark. Designed before the D-41 bound commit.
    """
    weights = np.array([
        [.2, -.1], [.1, -.15], [-.1, .2], [.05, 0], [0, 0], [0, 0],
    ], dtype=np.float64) * variant
    returns = np.array([
        [0, 0], [.03, -.02], [-.02, .04], [.01, -.01], [.04, .2], [-.02, .3],
    ], dtype=np.float64)
    funding = np.array([
        [0, 0], [.001, -.0002], [-.0001, .0003], [.0002, .0001], [0, 0], [0, 0],
    ], dtype=np.float64)
    specs, _ = synthetic_tape(capital)
    prices = np.array([100.0, .05])
    multipliers = np.array([1.0, 1000.0])
    costs = np.zeros_like(returns)
    positions, previous = np.zeros(2), np.zeros(2)
    nav, events = capital, []
    for t, target in enumerate(weights):
        day = f"2024-02-{t + 1:02}"
        for a, symbol in enumerate(specs):
            events.append(dict(time=day + "T00:00:00Z", type="FUNDING",
                               instrument_id=symbol, mark_price=prices[a], rate=funding[t, a]))
        nav *= 1 + previous @ (returns[t] - funding[t])
        prices *= 1 + returns[t]
        wanted = target * nav / (prices * multipliers)
        for a, symbol in enumerate(specs):
            events.append(dict(time=day + "T12:00:00Z", type="MARK",
                               instrument_id=symbol, mark_price=prices[a]))
            if t == 3 and symbol == "BBB":
                costs[t, a] = fee_rate + delist_slippage_bps / 1e4
                events.append(dict(time=day + "T12:00:01Z", type="DELIST",
                                   instrument_id=symbol, mark_price=prices[a],
                                   fee_rate=fee_rate, slippage_bps=delist_slippage_bps))
            elif wanted[a] != positions[a]:
                costs[t, a] = fee_rate + slippage_bps / 1e4
                events.append(dict(time=day + "T12:00:01Z", type="FILL",
                                   instrument_id=symbol, qty=wanted[a] - positions[a], price=prices[a],
                                   fee_rate=fee_rate, slippage_bps=slippage_bps))
        positions, previous = wanted, target
    return specs, events, weights[None, :, :], returns, costs, funding


@pytest.mark.parametrize("capital", [500.0, 100000.0])
@pytest.mark.parametrize("variant", [.5, 1.0, 1.5])
def test_three_way_exact_diagnostic_parity(capital, variant):
    specs, events, weights, returns, funding = scanner_tape(capital, variant)
    days = [f"2024-02-{day:02}" for day in range(1, 7)]
    ref = daily_pnl(run(reference, capital, specs, events), capital, days)
    seq = daily_pnl(run(ledger, capital, specs, events), capital, days)
    scan = scanner_daily(capital, weights, returns, np.zeros_like(returns), funding)
    assert_parity(seq, ref)
    assert_parity(scan, ref)
    assert_parity(scan, seq)


@pytest.mark.parametrize("fault", ["wrong_fee", "funding_at_bar_start", "lookahead_bar", "missing_delist"])
def test_scanner_seeds_cannot_pass_three_way_parity(fault):
    capital = 500.0
    specs, events, weights, returns, funding = scanner_tape(capital, 1.0)
    days = [f"2024-02-{day:02}" for day in range(1, 7)]
    ref = daily_pnl(run(reference, capital, specs, events), capital, days)
    costs = np.zeros_like(returns)
    if fault == "wrong_fee":
        costs[:] = .001
    elif fault == "funding_at_bar_start":
        # Charge tomorrow's funding at today's bar opening, before the entry.
        funding = np.roll(funding, -1, axis=0)
    elif fault == "lookahead_bar":
        weights = np.roll(weights, -1, axis=1)
    else:
        weights[:, 3:, 1] = weights[:, 2:3, 1]
    corrupted = scanner_daily(capital, weights, returns, costs, funding)
    with pytest.raises(AssertionError, match="PnL mismatch"):
        assert_parity(corrupted, ref)


@pytest.mark.parametrize("capital", [500.0, 100000.0])
@pytest.mark.parametrize("variant", [.5, 1.0, 1.5])
def test_cost_bearing_scanner_within_d41_bound(capital, variant):
    """D-41: scanner is approximate with costs; check the preregistered bound.

    Reference <-> sequential stays exact under config/parity.toml.
    """
    specs, events, weights, returns, costs, funding = cost_scanner_tape(capital, variant)
    days = [f"2024-02-{day:02}" for day in range(1, 7)]
    ref_rows = run(reference, capital, specs, events)
    seq_rows = run(ledger, capital, specs, events)
    ref, seq = daily_equity(ref_rows, capital, days), daily_equity(seq_rows, capital, days)
    assert_parity(daily_pnl(seq_rows, capital, days), daily_pnl(ref_rows, capital, days))
    result = scanner.scan(weights, returns, costs, funding)
    scan = capital * result["equity"][0]
    assert np.max(np.abs(scan - seq)) / np.max(seq) > 0, "fixture must actually bear costs"
    assert max_relative_equity_diff(scan, seq) <= SCANNER_BOUND
    assert max_relative_equity_diff(scan, ref) <= SCANNER_BOUND


@pytest.mark.parametrize("capital", [500.0, 100000.0])
def test_cost_bearing_counterexample_is_expected_approximation(capital):
    """D-41 expected approximation, not a failure.

    Flat prices, target .2 then flat, 5bps each leg. Ledger stays at .2 capital;
    scanner compounds the first fee into equity but exits the prior .2 target.
    Analytical relative gap is (0.2 * 0.0005)**2, far below the D-41 bound.
    """
    specs, _ = synthetic_tape(capital)
    events = [dict(time=f"2024-03-0{day}T12:00:01Z", type="FILL", instrument_id="AAA",
                   qty=qty * capital / 500, price=100.0, fee_rate=.0005, slippage_bps=0.0)
              for day, qty in [(1, 1.0), (2, -1.0)]]
    days = ["2024-03-01", "2024-03-02"]
    ref_rows = run(reference, capital, specs, events)
    seq_rows = run(ledger, capital, specs, events)
    ref = daily_pnl(ref_rows, capital, days)
    seq = daily_pnl(seq_rows, capital, days)
    assert_parity(seq, ref)
    weights = np.array([[[.2], [0]]], dtype=np.float64)
    zeros = np.zeros((2, 1), dtype=np.float64)
    result = scanner.scan(weights, zeros, zeros + .0005, zeros)
    scan = capital * result["equity"][0]
    seq_equity = daily_equity(seq_rows, capital, days)
    # Exact parity is intentionally not required with costs; this gap is expected.
    assert np.max(np.abs(np.diff(np.concatenate(([capital], scan))) - seq)) > TOLERANCES["daily_pnl_abs_diff"]
    gap = max_relative_equity_diff(scan, seq_equity)
    assert gap <= SCANNER_BOUND
    assert gap == pytest.approx((.2 * .0005)**2, rel=0.01, abs=0)
