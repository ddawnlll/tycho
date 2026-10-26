"""#25 contract checks; 1h delayed execution remains delegated to BLOCKED #51."""

import json

import pandas as pd
import pytest

from test_runner import PARAMS
from tycho import e001
from tycho.ledger import Instrument
from tycho.marketstate import MarketStateProvider
from tycho.synthetic import make_synthetic_tape
from tycho.trials import verify_chain


def _diagnostic_tape():
    tape = make_synthetic_tape(seed=3, n_perps=1, days=260)
    # Explicit post-#50 synthetic payment-time fixture: daily funding only, with
    # marks/rates published at their own event time. Not a production fill model.
    tape.funding = tape.funding[tape.funding["event_time"].dt.hour == 0].copy()
    tape.funding["available_at"] = tape.funding["event_time"]
    tape.mark["available_at"] = tape.mark["event_time"]
    return tape


def _diagnostic_params(nominal=100_000.0):
    return {
        **PARAMS,
        "universe_size": 4,
        "min_history_days": 30,
        "ma_periods_days": [5, 10],
        "research_nominal_usd": nominal,
    }


@pytest.fixture(scope="module")
def diagnostics(tmp_path_factory):
    tape = _diagnostic_tape()
    root = tmp_path_factory.mktemp("diagnostics")
    params = _diagnostic_params()
    panels = e001.trend_panels(
        tape, params["ma_periods_days"], params["ewma_convention"],
        decision_lag=params["decision_lag"],
    )
    universe = e001.universe_panel(MarketStateProvider(tape, 4, 30), panels["as_of"])
    universe.index = panels["score"].index
    member = universe.reindex_like(panels["score"]).fillna(False).astype(bool)
    stale = panels["close"].ffill(limit=2).isna()
    expected = panels["score"].where(member, 0).fillna(0).mask(stale, 0) / 4
    runs = []
    simulate = e001._simulate
    for target_vol in (0.2, 0.4):
        captured = []

        def capture(prep, targets, settings):
            captured.append(targets.copy())
            return simulate(prep, targets, settings)

        path = root / f"{target_vol}.jsonl"
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(e001, "_simulate", capture)
            result = e001.run_e001(
                tape, {**params, "target_vol": target_vol, "trials_path": path},
                synthetic=True,
            )
        records = [json.loads(line) for line in path.read_text().splitlines()]
        runs.append((result, captured, records, path))
    return expected, runs


def test_constant_notional_uses_same_signal_without_risk_scaling(diagnostics):
    expected, runs = diagnostics
    assert (expected > 0).any().any()
    assert (expected < 0).any().any()
    assert (expected == 0).any().any()
    for result, captured, _, _ in runs:
        pd.testing.assert_frame_equal(captured[1], expected)
        assert result["metrics"]["constant_notional"]["trade_count"] > 0
        assert result["identity_max_abs_error"] < 1e-9
    assert not runs[0][1][0].equals(runs[1][1][0])
    assert runs[0][0]["metrics"]["constant_notional"] == runs[1][0]["metrics"]["constant_notional"]


def test_constant_notional_has_own_counted_hash_chained_trial(diagnostics):
    _, runs = diagnostics
    for result, _, records, path in runs:
        assert verify_chain(path)
        assert [r["hash"] for r in records] == result["trial_hashes"]
        diagnostic = [r for r in records if r["variant"] == "constant_notional"]
        assert len(diagnostic) == 1
        assert diagnostic[0]["metrics"] == result["metrics"]["constant_notional"]
        assert diagnostic[0]["params_hash"] == result["params_hash"]
        assert diagnostic[0]["dataset_hash"] == result["dataset_hash"]


def test_diagnostic_trial_is_explicitly_flagged(diagnostics):
    _, runs = diagnostics
    for _, _, records, _ in runs:
        diagnostic = next(r for r in records if r["variant"] == "constant_notional")
        assert diagnostic.get("diagnostic") is True
        assert not next(r for r in records if r["variant"] == "main").get("diagnostic", False)


def test_annual_turnover_has_own_diagnostic_trial(diagnostics):
    _, runs = diagnostics
    for _, _, records, _ in runs:
        # #25 requires each diagnostic to be counted, not just an embedded metric.
        turnover = [r for r in records if r["variant"] == "turnover"]
        assert len(turnover) == 1
        assert turnover[0].get("diagnostic") is True
        assert turnover[0]["metrics"]["turnover_by_year"]


@pytest.fixture
def turnover_case(tmp_path):
    # Buy 5 at 100 on Dec 31 (NAV 1000), exit 5 at 200 (NAV 1500).
    # No gap/cost/funding: decision- and execution-time NAV agree for both trades.
    dates = pd.date_range("2024-12-30", periods=4, tz="UTC")
    prices = pd.DataFrame({"A": [100.0, 100.0, 200.0, 200.0]}, index=dates)
    prep = {
        "dates": dates,
        "as_of": pd.Series(dates + pd.Timedelta("1min"), index=dates),
        "closes": prices,
        "marks": prices,
        "adv": pd.DataFrame(1_000_000.0, index=dates, columns=["A"]),
        "daily_vol": pd.DataFrame(0.01, index=dates, columns=["A"]),
        "instruments": {
            i: {"A": Instrument("A", 1, 1, 0)} for i in range(len(dates))
        },
        "funding": {},
        "delists": {i: {} for i in range(len(dates))},
    }
    settings = e001._settings({
        **PARAMS, "research_nominal_usd": 1000,
        "taker_fee": 0, "slippage_min_bps": 0, "slippage_y": 0,
        "delist_exit_slippage_bps": 0, "band_relative": 0,
        "absolute_band_nav": 0, "trials_path": tmp_path / "unused.jsonl",
        "synthetic_execution_policy": "SYNTHETIC_PUBLISHED_DAILY_MARK",
        "executable_latency": "1D",
    }, synthetic=True)
    targets = pd.DataFrame({"A": [0.5, 0.5, 0.0, 0.0]}, index=dates)
    return prep, targets, settings


def test_turnover_sums_trade_weights_not_notional_over_mean_equity(turnover_case):
    metrics = e001._simulate(*turnover_case)["metrics"]
    assert metrics["trade_count"] == 2
    assert metrics["total_return"] == pytest.approx(0.5)
    assert metrics["turnover"] == pytest.approx(0.5 + 2 / 3)


def test_turnover_is_grouped_by_execution_year(turnover_case):
    metrics = e001._simulate(*turnover_case)["metrics"]
    # JSON-safe output: raw yearly sums of traded weights, no partial-year extrapolation.
    assert "turnover_by_year" in metrics
    assert metrics["turnover_by_year"] == pytest.approx({"2024": 0.5, "2025": 2 / 3})


def test_turnover_year_uses_fill_timestamp_with_1d_decision_lag(turnover_case):
    # Dec 31 decision -> Jan 1 fill; the whole round trip executes in 2025.
    prep, targets, settings = turnover_case
    prep["as_of"] = prep["as_of"] + pd.Timedelta("1D")
    settings = {**settings, "decision_lag": str(pd.Timedelta("1D"))}
    metrics = e001._simulate(prep, targets, settings)["metrics"]
    assert metrics["turnover"] == pytest.approx(0.5 + 2 / 3)
    assert metrics["turnover_by_year"] == pytest.approx({"2025": 0.5 + 2 / 3})


def test_large_nominal_exact_step_order_executes(tmp_path):
    # #69: this scale used to false-reject an exact -1_069_802_886-step order.
    path = tmp_path / "large-nominal.jsonl"
    result = e001.run_e001(
        _diagnostic_tape(),
        {**_diagnostic_params(1_000_000.0), "target_vol": 0.2, "trials_path": path},
        synthetic=True,
    )
    metrics = result["metrics"]["main"]
    assert metrics["trade_count"] > 0
    assert metrics["turnover"] > 0
    assert sum(metrics["turnover_by_year"].values()) == pytest.approx(metrics["turnover"])


def test_off_step_queued_order_is_still_rejected(turnover_case):
    prep, targets, settings = turnover_case
    # Day 1 executes the day-0 5-contract order; a step of 3 makes it off-step.
    prep["instruments"][1] = {"A": Instrument("A", 1, 3.0, 0)}
    with pytest.raises(ValueError, match="queued order violates executable contract"):
        e001._simulate(prep, targets, settings)


def test_turnover_includes_forced_delist_exit(turnover_case):
    prep, targets, settings = turnover_case
    prep["delists"][2] = {"A": prep["dates"][2]}
    metrics = e001._simulate(prep, targets, settings)["metrics"]
    assert metrics["turnover"] == pytest.approx(0.5 + 2 / 3)
    assert metrics.get("turnover_by_year") == pytest.approx({"2024": 0.5, "2025": 2 / 3})
