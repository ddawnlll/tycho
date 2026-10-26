"""E-001 DEV runner: synthetic run, ledger identities, trial log, PENDING refusal."""

import json

import pandas as pd
import pytest

from tycho import config
from tycho.e001 import main, run_e001
from tycho.synthetic import make_synthetic_tape
from tycho.trials import verify_chain

# Synthetic test parameters only: every monograph PENDING value stays PENDING.
PARAMS = {
    "ewma_convention": {"kind": "span", "value": 60, "returns": "log", "min_periods": 20},
    "decision_lag": "1min",
    "on_abstain": "flat",
    "max_staleness": "2D",
    "absolute_band_nav": 0.01,
    "partial_ratio": 1.0,
    "research_nominal_usd": 1_000_000.0,
}


@pytest.fixture(scope="module")
def tape():
    return make_synthetic_tape(seed=3, n_perps=6, days=500)


@pytest.fixture(scope="module")
def result(tape, tmp_path_factory):
    path = tmp_path_factory.mktemp("trials") / "trials.jsonl"
    return run_e001(tape, {**PARAMS, "trials_path": str(path)}, synthetic=True), path


def test_run_completes_with_metrics(result, tape):
    out, _ = result
    assert out["kind"] == "SYNTHETIC"
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


@pytest.mark.parametrize("missing", ["absolute_band_nav", "decision_lag", "on_abstain"])
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
        module, "make_synthetic_tape", lambda seed: make_synthetic_tape(seed=seed, n_perps=6, days=500)
    )
    main(["--synthetic-seed", "3", "--params", str(params_path), "--out", str(out)])
    written = json.loads((out / "result.json").read_text())
    assert written["kind"] == "SYNTHETIC"
    assert set(written["metrics"]) == {"main", "constant_notional"}
