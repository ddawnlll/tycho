"""Synthetic replay of the production simulator, with disjoint accounting windows."""

import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from tycho import e001, trials
from tycho.contracts import canonical_hash
from tycho.synthetic import make_synthetic_tape


def _case():
    # Explicit #50 mechanism fixture: daily payment-time marks, not inferred
    # intraday executable prices. Thirty prior bars warm up production ADV.
    tape = make_synthetic_tape(seed=36, n_perps=1, days=40)
    tape.funding = tape.funding[tape.funding.event_time.dt.hour == 0].copy()
    tape.funding["available_at"] = tape.funding["event_time"]
    tape.funding["rate"] = 0.001
    tape.mark["available_at"] = tape.mark["event_time"]
    tape.mark["mark_price"] = 100.0 + np.arange(len(tape.mark))
    tape.bars_1d["quote_volume"] = 1e9
    dates = pd.DatetimeIndex(sorted(tape.bars_1d.event_time.unique())[-10:], name="event_time")
    closes = tape.mark.set_index("event_time")[["mark_price"]].rename(
        columns={"mark_price": "BTCUSDT"}
    ).loc[dates]
    panels = {
        "as_of": pd.Series(dates + pd.Timedelta("1min"), index=dates, name="event_time"),
        "close": closes,
        "vol": pd.DataFrame(0.2, index=dates, columns=["BTCUSDT"]),
    }
    targets = pd.DataFrame(
        {"BTCUSDT": [0.5, 0.8, 0.2, -0.4, -0.7, 0.0, 0.6, 0.9, 0.2, 0.0]},
        index=dates,
    )
    # Test parameters only; all monograph PENDING values remain untouched.
    settings = e001._settings({
        "ewma_convention": {"kind": "span", "value": 60, "returns": "log", "min_periods": 20},
        "decision_lag": "1min", "executable_latency": "1D",
        "synthetic_execution_policy": "SYNTHETIC_PUBLISHED_DAILY_MARK",
        "on_abstain": "flat", "max_staleness": "2D",
        "absolute_band_nav": 0.0, "band_relative": 0.0, "partial_ratio": 1.0,
        "research_nominal_usd": 1000.0,
    }, True)
    return e001._prepare(tape, dates, panels), targets, settings


def _run(prep, targets, settings, **kwargs):
    records = []
    ledger = e001.run_ledger

    def observe(wallet, instruments, events):
        snapshots = ledger(wallet, instruments, events)
        records.append({"events": events, "snapshots": snapshots})
        return snapshots

    with patch.object(e001, "run_ledger", observe):
        run = e001._simulate(prep, targets, settings, **kwargs)
    return {"run": run, "records": records}


def _evidence(path, records, input_hash):
    for record in records:
        last = trials.append_trial(path, {"input_hash": input_hash, **record})
    assert trials.verify_chain(path)
    return last["hash"]


def _assert_equal(full, parts, prep, tmp_path):
    expected, actual = full["run"], parts[-1]["run"]
    assert [value for part in parts for value in part["run"]["equity"]] == expected["equity"]
    for key in ("checkpoint", "evidence_hash", "metrics", "identity_max_abs_error", "scanner_max_abs_diff"):
        assert actual[key] == expected[key]
    records = [record for part in parts for record in part["records"]]
    # Every real day is executed exactly once, in order; no full-prefix reruns.
    assert len(records) == len(prep["dates"])
    assert [max(event["time"] for event in r["events"]) for r in records] == [
        date.isoformat() for date in prep["as_of"]
    ]
    assert records == full["records"]
    identity = actual["checkpoint"]["state"]["input_hash"]
    assert _evidence(tmp_path / "full.jsonl", full["records"], identity) == _evidence(
        tmp_path / "windows.jsonl", records, identity
    )
    assert expected["metrics"]["trade_count"] > 5
    assert expected["metrics"]["fees"] > 0
    assert expected["checkpoint"]["state"]["orders"] == {}


@pytest.mark.parametrize("stops", [(2, 5, 9, 10), tuple(range(1, 11))])
def test_full_equals_state_carried_windows(stops, tmp_path):
    prep, targets, settings = _case()
    full = _run(prep, targets, settings)
    parts, checkpoint = [], None
    for stop in stops:
        part = _run(prep, targets, settings, checkpoint=checkpoint, stop=stop)
        parts.append(part)
        path = tmp_path / "checkpoint.json"
        path.write_text(json.dumps(part["run"]["checkpoint"], allow_nan=False))
        checkpoint = json.loads(path.read_text())
    _assert_equal(full, parts, prep, tmp_path)


def test_funding_at_window_boundary_uses_carried_position_before_pending_fill():
    prep, targets, settings = _case()
    first = _run(prep, targets, settings, stop=2)
    checkpoint = first["run"]["checkpoint"]
    before = json.dumps(checkpoint, sort_keys=True)
    state = checkpoint["state"]
    assert state["positions"]["BTCUSDT"] > 0
    assert state["entry"]["BTCUSDT"] > 0
    assert state["orders"]["BTCUSDT"] != 0
    second = _run(prep, targets, settings, checkpoint=checkpoint, stop=3)
    assert json.dumps(checkpoint, sort_keys=True) == before
    timestamp, iid, rate, mark, _ = prep["funding"][2][0]
    assert timestamp == prep["dates"][2]
    assert mark == prep["marks"].loc[timestamp, iid]
    expected_funding = -state["positions"][iid] * prep["instruments"][2][iid].contract_multiplier * mark * rate
    after = second["run"]["checkpoint"]["state"]
    assert after["funding_total"] - state["funding_total"] == expected_funding != 0
    snapshots = second["records"][0]["snapshots"]
    funding = [s for s in snapshots if s["type"] == "FUNDING"]
    assert len(funding) == 1
    assert funding[0]["positions"] == state["positions"]
    assert after["positions"][iid] == state["positions"][iid] + state["orders"][iid]
    assert any(s["type"] == "FILL" and s["time"] > funding[0]["time"] for s in snapshots)


def test_restart_in_fresh_process_after_durable_checkpoint(tmp_path):
    prep, targets, settings = _case()
    full = _run(prep, targets, settings)
    first_path, last_path = tmp_path / "first.json", tmp_path / "last.json"
    # The first interpreter exits abruptly after closing its checkpoint file.
    first = subprocess.run(
        [sys.executable, __file__, "-", str(first_path), "2"],
        capture_output=True, text=True, timeout=60,
    )
    assert first.returncode == 73, first.stderr
    last = subprocess.run(
        [sys.executable, __file__, str(first_path), str(last_path), "10"],
        capture_output=True, text=True, timeout=60,
    )
    assert last.returncode == 0, last.stderr
    parts = [json.loads(path.read_text()) for path in (first_path, last_path)]
    assert parts[0]["run"]["checkpoint"]["state"]["orders"]
    _assert_equal(full, parts, prep, tmp_path)


@pytest.mark.parametrize("change", [
    "checksum", "input", "next_day", "watermark", "history", "entry", "nan", "wallet",
    "orders", "turnover_negative", "turnover_key", "traded_weight_negative",
])
def test_invalid_checkpoint_is_refused_before_any_ledger_event(change):
    prep, targets, settings = _case()
    checkpoint = _run(prep, targets, settings, stop=2)["run"]["checkpoint"]
    state = checkpoint["state"]
    if change == "checksum":
        state["wallet"] += 1
    elif change == "input":
        targets.iloc[-1, 0] = 0.9
    else:
        if change == "next_day":
            state["next_day"] = -1
        elif change == "watermark":
            state["watermark"] = prep["as_of"].iloc[2].isoformat()
        elif change == "history":
            state["equity_series"].pop()
        elif change == "entry":
            state["entry"] = {}
        elif change == "nan":
            state["wallet"] = float("nan")
        elif change == "wallet":
            state["wallet"] = "1000.0"
        elif change == "orders":
            state["orders"]["UNKNOWN"] = 1.0
        elif change == "turnover_negative":
            state["turnover_by_year"] = {"2025": -0.1}
        elif change == "turnover_key":
            state["turnover_by_year"] = {"25": 0.1}
        elif change == "traded_weight_negative":
            state["traded_weight"] = -0.1
        checkpoint["hash"] = canonical_hash(state)
    with patch.object(e001, "run_ledger") as ledger:
        with pytest.raises(ValueError, match="invalid replay checkpoint"):
            e001._simulate(prep, targets, settings, checkpoint=checkpoint)
        ledger.assert_not_called()


@pytest.mark.parametrize("changed", ["settings", "marks", "funding", "clock", "head"])
def test_checkpoint_is_bound_to_execution_inputs_and_code(changed, monkeypatch):
    prep, targets, settings = _case()
    checkpoint = _run(prep, targets, settings, stop=2)["run"]["checkpoint"]
    if changed == "settings":
        settings["taker_fee"] *= 2
    elif changed == "marks":
        prep["marks"].iloc[-1, 0] += 1
    elif changed == "funding":
        prep["funding"][2] = []
    elif changed == "clock":
        prep["as_of"].iloc[-1] += pd.Timedelta("1min")
    else:
        monkeypatch.setattr(e001, "_git_head", lambda: "different-code")
    with pytest.raises(ValueError, match="checkpoint hash or replay inputs differ"):
        e001._simulate(prep, targets, settings, checkpoint=checkpoint)


@pytest.mark.parametrize("stop", [0, 2, 10])
def test_checkpoint_boundary_is_idempotent(stop):
    prep, targets, settings = _case()
    checkpoint = _run(prep, targets, settings, stop=stop)["run"]["checkpoint"]
    repeated = _run(prep, targets, settings, checkpoint=checkpoint, stop=stop)
    assert repeated["records"] == []
    assert repeated["run"]["equity"] == []
    assert repeated["run"]["checkpoint"] == checkpoint


@pytest.mark.parametrize("stop", [-1, 1, 11, 2.5, True])
def test_invalid_stop_cannot_replay_or_skip_a_checkpoint_boundary(stop):
    prep, targets, settings = _case()
    checkpoint = _run(prep, targets, settings, stop=2)["run"]["checkpoint"]
    with pytest.raises(ValueError, match="stop must be a day boundary"):
        e001._simulate(prep, targets, settings, checkpoint=checkpoint, stop=stop)


@pytest.mark.parametrize("missing", ["mark", "publication"])
def test_resume_keeps_fail_closed_funding_contract(missing):
    prep, targets, settings = _case()
    timestamp, iid, rate, mark, available = prep["funding"][2][0]
    if missing == "mark":
        mark = None
    else:
        available = timestamp + pd.Timedelta("1min")
    prep["funding"][2] = [(timestamp, iid, rate, mark, available)]
    checkpoint = _run(prep, targets, settings, stop=2)["run"]["checkpoint"]
    with pytest.raises(ValueError, match="funding .*unavailable"):
        e001._simulate(prep, targets, settings, checkpoint=checkpoint)


if __name__ == "__main__":
    source, destination, stop = sys.argv[1:]
    checkpoint = None if source == "-" else json.loads(Path(source).read_text())["run"]["checkpoint"]
    result = _run(*_case(), checkpoint=checkpoint, stop=int(stop))
    Path(destination).write_text(json.dumps(result, allow_nan=False))
    if source == "-":
        os._exit(73)
