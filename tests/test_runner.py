"""E-001 DEV runner: synthetic run, ledger identities, trial log, PENDING refusal."""

import json

import pandas as pd
import pytest

from tycho import config
from tycho import e001
from tycho.e001 import main, run_e001
from tycho.synthetic import make_synthetic_tape
from tycho.trials import verify_chain

# Synthetic test parameters only: every monograph PENDING value stays PENDING.
PARAMS = {
    "ewma_convention": {"kind": "span", "value": 60, "returns": "log", "min_periods": 20},
    "decision_lag": "1min",
    "executable_latency": "1D",
    "synthetic_execution_policy": "SYNTHETIC_PUBLISHED_DAILY_MARK",
    "on_abstain": "flat",
    "max_staleness": "2D",
    "absolute_band_nav": 0.01,
    "partial_ratio": 1.0,
    "research_nominal_usd": 1_000_000.0,
}


@pytest.fixture(scope="module")
def tape():
    # Explicit mechanism fixture, NOT an intraday/production execution contract:
    # one perpetual, daily-only funding and instantaneous synthetic payment marks.
    tape = make_synthetic_tape(seed=3, n_perps=1, days=500)
    tape.funding = tape.funding[tape.funding["event_time"].dt.hour == 0].copy()
    tape.funding["available_at"] = tape.funding["event_time"]
    tape.mark["available_at"] = tape.mark["event_time"]
    return tape


@pytest.fixture(scope="module")
def result(tape, tmp_path_factory):
    path = tmp_path_factory.mktemp("trials") / "trials.jsonl"
    return run_e001(tape, {**PARAMS, "trials_path": str(path)}, synthetic=True), path


def test_run_completes_with_metrics(result, tape):
    out, _ = result
    assert out["kind"] == "SYNTHETIC"
    assert out["execution_policy"] == PARAMS["synthetic_execution_policy"]
    assert out["dataset_hash"] and out["params_hash"]
    assert set(out["metrics"]) == {"main", "constant_notional"}
    for metrics in out["metrics"].values():
        assert metrics["days"] == len(tape.bars_1d["event_time"].unique())
        assert metrics["trade_count"] > 0
        assert metrics["fees"] > 0.0
        assert metrics["max_drawdown"] >= 0.0
        assert metrics["sharpe"] is None or isinstance(metrics["sharpe"], float)


def test_wallet_equity_identities_hold(result):
    out, _ = result
    assert out["identity_max_abs_error"] < 1e-9
    # scan parity is milestone M6: this is a diagnostic, not a tolerance
    assert out["scanner_max_abs_diff"] >= 0.0


def test_trial_log_gets_two_chained_records(result):
    out, path = result
    assert len(out["trial_hashes"]) == 2
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["variant"] for line in lines] == ["main", "constant_notional"]
    assert {line["kind"] for line in lines} == {"SYNTHETIC"}
    assert verify_chain(path)
    assert lines[1]["prev_hash"] == lines[0]["hash"]
    assert all(line["params_hash"] == out["params_hash"] for line in lines)
    assert all(line["dataset_hash"] == out["dataset_hash"] for line in lines)
    assert all(line["git_head"] for line in lines)
    assert all(line["execution_policy"] == PARAMS["synthetic_execution_policy"] for line in lines)


@pytest.mark.parametrize("missing", ["absolute_band_nav", "decision_lag", "executable_latency", "on_abstain"])
def test_missing_pending_parameter_is_refused(tape, tmp_path, missing):
    params = {key: value for key, value in PARAMS.items() if key != missing}
    params["trials_path"] = str(tmp_path / "trials.jsonl")
    with pytest.raises(config.PendingParameterError):
        run_e001(tape, params, synthetic=True)


def test_non_synthetic_run_raises_pending(tape, tmp_path):
    params = {**PARAMS, "trials_path": str(tmp_path / "trials.jsonl")}
    with pytest.raises(config.PendingParameterError):
        run_e001(tape, params, synthetic=False)


def test_cli_writes_result_json(tape, tmp_path, monkeypatch):
    params_path = tmp_path / "params.json"
    params_path.write_text(json.dumps({**PARAMS, "trials_path": str(tmp_path / "t.jsonl")}))
    out = tmp_path / "out"
    import tycho.e001 as module

    monkeypatch.setattr(
        module, "make_synthetic_tape", lambda seed: tape
    )
    main(["--synthetic-seed", "3", "--params", str(params_path), "--out", str(out)])
    written = json.loads((out / "result.json").read_text())
    assert written["kind"] == "SYNTHETIC"
    assert set(written["metrics"]) == {"main", "constant_notional"}


@pytest.mark.parametrize("latency", ["0s", "-1s", "NaT"])
def test_executable_latency_must_be_positive(latency):
    with pytest.raises(ValueError, match="strictly positive"):
        e001._settings({**PARAMS, "executable_latency": latency}, True)


@pytest.mark.parametrize("latency", ["1ns", "1min", "1h", "2D"])
def test_unsupported_daily_tape_latency_is_refused(latency):
    with pytest.raises(ValueError, match="supports only 1D executable_latency"):
        e001._settings({**PARAMS, "executable_latency": latency}, True)


def test_daily_prices_are_not_implicitly_executable():
    params = {k: v for k, v in PARAMS.items() if k != "synthetic_execution_policy"}
    with pytest.raises(ValueError, match="no executable prices"):
        e001._settings(params, True)


@pytest.mark.parametrize("latency", [None, "", "PENDING"])
def test_pending_executable_latency_is_not_defaulted(latency):
    with pytest.raises(config.PendingParameterError, match="executable_latency"):
        e001._settings({**PARAMS, "executable_latency": latency}, True)


def _execution_case():
    """Explicit fixed-price synthetic mechanism fixture; no inferred intraday marks."""
    tape = make_synthetic_tape(seed=3, n_perps=1, days=34)
    tape.funding = tape.funding.iloc[:0].copy()
    tape.mark["mark_price"] = 100.0
    tape.bars_1d["quote_volume"] = 1e9
    dates = pd.DatetimeIndex(sorted(tape.bars_1d["event_time"].unique())[-3:])
    panels = {
        "as_of": pd.Series(dates + pd.Timedelta("1min"), index=dates),
        "close": pd.DataFrame(100.0, index=dates, columns=["BTCUSDT"]),
        "vol": pd.DataFrame(0.2, index=dates, columns=["BTCUSDT"]),
    }
    targets = pd.DataFrame(0.5, index=dates, columns=["BTCUSDT"])
    settings = e001._settings({
        **PARAMS, "research_nominal_usd": 1000.0, "absolute_band_nav": 0.0,
        "taker_fee": 0.0, "slippage_min_bps": 0.0, "slippage_y": 0.0,
    }, True)
    return tape, dates, panels, targets, settings


@pytest.mark.parametrize("constraint", ["available_at", "valid_from"])
def test_historical_contracts_do_not_use_future_specs(constraint):
    tape, dates, panels, _, _ = _execution_case()
    future = tape.specs.iloc[[0]].copy()
    future["event_time"] = dates[0] - pd.Timedelta("1D")
    future["valid_from"] = future["event_time"]
    future["available_at"] = future["event_time"]
    future[constraint] = panels["as_of"].iloc[1]
    future["step_size"], future["min_notional"], future["revision"] = 7.0, 999.0, 2
    tape.specs = pd.concat([tape.specs, future], ignore_index=True)
    prep = e001._prepare(tape, dates, panels)
    assert prep["instruments"][0]["BTCUSDT"].step_size == 0.001
    assert prep["instruments"][0]["BTCUSDT"].min_notional == 5.0
    assert prep["instruments"][1]["BTCUSDT"].step_size == 7.0
    assert prep["instruments"][1]["BTCUSDT"].min_notional == 999.0


def test_historical_adv_and_marks_ignore_future_corrections():
    tape, dates, panels, _, _ = _execution_case()
    original = e001._prepare(tape, dates, panels)
    correction = tape.bars_1d[tape.bars_1d["event_time"] == dates[0]].copy()
    correction["quote_volume"] = 1e12
    correction["revision"] = 2
    correction["available_at"] = panels["as_of"].iloc[1]
    tape.bars_1d = pd.concat([tape.bars_1d, correction], ignore_index=True)
    correction = tape.mark[tape.mark["event_time"] == dates[0]].copy()
    correction["mark_price"], correction["revision"] = 999.0, 2
    correction["available_at"] = panels["as_of"].iloc[1]
    tape.mark = pd.concat([tape.mark, correction], ignore_index=True)
    revised = e001._prepare(tape, dates, panels)
    assert revised["adv"].loc[dates[0], "BTCUSDT"] == original["adv"].loc[dates[0], "BTCUSDT"]
    assert revised["adv"].loc[dates[1], "BTCUSDT"] > original["adv"].loc[dates[1], "BTCUSDT"]
    assert revised["marks"].loc[dates[0], "BTCUSDT"] == 100.0


def test_fixture_fill_respects_decision_and_latency_not_old_open(monkeypatch):
    tape, dates, panels, targets, settings = _execution_case()
    tape.bars_1d["open"] = 999.0
    settings["taker_fee"] = 0.001
    events = []
    ledger = e001.run_ledger

    def capture(wallet, instruments, daily):
        events.extend(daily)
        return ledger(wallet, instruments, daily)

    monkeypatch.setattr(e001, "run_ledger", capture)
    run = e001._simulate(e001._prepare(tape, dates, panels), targets, settings)
    fills = [event for event in events if event["type"] == "FILL" and event["fee_rate"] > 0]
    assert fills
    assert pd.Timestamp(fills[0]["time"]) == panels["as_of"].iloc[0] + pd.Timedelta(settings["executable_latency"])
    assert fills[0]["price"] == 100.0
    assert run["identity_max_abs_error"] < 1e-9


def test_unavailable_execution_price_is_not_backfilled():
    tape, dates, panels, targets, settings = _execution_case()
    tape.mark.loc[tape.mark["event_time"] == dates[1], "available_at"] = dates[2]
    with pytest.raises(ValueError, match="executable price unavailable"):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)


@pytest.mark.parametrize("day,error", [(0, "decision mark unavailable"), (2, "valuation mark unavailable")])
def test_unavailable_marks_do_not_imply_no_trade_or_use_a_stale_price(day, error):
    tape, dates, panels, targets, settings = _execution_case()
    tape.mark.loc[tape.mark["event_time"] == dates[day], "available_at"] = dates[day] + pd.Timedelta("1D")
    with pytest.raises(ValueError, match=error):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)


def test_unknown_decision_contract_is_refused():
    tape, dates, panels, targets, settings = _execution_case()
    tape.specs["available_at"] = dates[1]
    with pytest.raises(ValueError, match="missing decision contract spec"):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)


@pytest.mark.parametrize("missing", ["adv", "vol"])
def test_unknown_cost_inputs_do_not_fall_back_to_minimum_slippage(missing):
    tape, dates, panels, targets, settings = _execution_case()
    if missing == "adv":
        tape.bars_1d = tape.bars_1d[tape.bars_1d["event_time"] >= dates[0]]
    else:
        panels["vol"].iloc[0] = float("nan")
    with pytest.raises(ValueError, match="PIT execution cost inputs unavailable"):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)


@pytest.mark.parametrize("field,value", [("step_size", 3.0), ("min_notional", 1000.0)])
def test_queued_order_is_checked_against_execution_time_contract(field, value):
    tape, dates, panels, targets, settings = _execution_case()
    changed = tape.specs.iloc[[0]].copy()
    changed["event_time"] = changed["valid_from"] = dates[1]
    changed["available_at"] = panels["as_of"].iloc[1]
    changed["revision"], changed[field] = 2, value
    tape.specs = pd.concat([tape.specs, changed], ignore_index=True)
    with pytest.raises(ValueError, match="queued order violates executable contract"):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)


def test_contract_multiplier_change_cannot_revalue_an_open_position():
    tape, dates, panels, targets, settings = _execution_case()
    changed = tape.specs.iloc[[0]].copy()
    changed["event_time"] = changed["valid_from"] = dates[2]
    changed["available_at"] = panels["as_of"].iloc[2]
    changed["revision"], changed["contract_multiplier"] = 2, 1000.0
    tape.specs = pd.concat([tape.specs, changed], ignore_index=True)
    with pytest.raises(ValueError, match="migration policy required"):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)


def _publish_delist(tape, last_trade_day, available_at):
    notice = tape.instruments[tape.instruments["instrument_id"] == "BTCUSDT"].iloc[[0]].copy()
    notice["last_trade_day"] = last_trade_day
    notice["delisted_at"] = last_trade_day + pd.Timedelta("1D")
    notice["available_at"], notice["revision"] = available_at, 1
    tape.instruments = pd.concat([tape.instruments, notice], ignore_index=True)


def test_delist_notice_cannot_retroactively_liquidate():
    tape, dates, panels, targets, settings = _execution_case()
    _publish_delist(tape, dates[1], panels["as_of"].iloc[2])
    prep = e001._prepare(tape, dates, panels)
    assert prep["delists"][1] == {}
    assert prep["delists"][2] == {"BTCUSDT": dates[1]}
    with pytest.raises(ValueError, match="delist published after liquidation day"):
        e001._simulate(prep, targets, settings)


def test_queued_order_overlapping_delist_requires_owner_policy():
    tape, dates, panels, targets, settings = _execution_case()
    _publish_delist(tape, dates[1], panels["as_of"].iloc[1])
    with pytest.raises(ValueError, match="queued order overlaps delist"):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)


@pytest.mark.parametrize("mark_available", [True, False])
def test_delist_requires_a_visible_liquidation_mark(mark_available, monkeypatch):
    tape, dates, panels, targets, settings = _execution_case()
    _publish_delist(tape, dates[2], panels["as_of"].iloc[2])
    if not mark_available:
        tape.mark.loc[tape.mark["event_time"] == dates[2], "available_at"] = dates[2] + pd.Timedelta("1D")
    events = []
    ledger = e001.run_ledger

    def capture(wallet, instruments, daily):
        events.extend(daily)
        return ledger(wallet, instruments, daily)

    monkeypatch.setattr(e001, "run_ledger", capture)
    prep = e001._prepare(tape, dates, panels)
    if mark_available:
        e001._simulate(prep, targets, settings)
        exits = [event for event in events if event["type"] == "DELIST"]
        assert len(exits) == 1
        assert exits[0]["mark_price"] == 100.0
        assert pd.Timestamp(exits[0]["time"]) == panels["as_of"].iloc[2]
    else:
        with pytest.raises(ValueError, match="delist liquidation mark unavailable"):
            e001._simulate(prep, targets, settings)


@pytest.mark.parametrize("publication_lag", ["0s", "1min", "2D"])
def test_intraday_funding_does_not_invent_a_mark(publication_lag):
    tape, dates, panels, targets, settings = _execution_case()
    funding = make_synthetic_tape(seed=3, n_perps=1, days=1).funding.iloc[[0]].copy()
    funding["event_time"] = dates[1] + pd.Timedelta("12h")
    funding["available_at"] = funding["event_time"] + pd.Timedelta(publication_lag)
    tape.funding = funding
    error = "funding mark unavailable" if publication_lag == "0s" else "funding unavailable at payment time"
    with pytest.raises(ValueError, match=error):
        e001._simulate(e001._prepare(tape, dates, panels), targets, settings)
