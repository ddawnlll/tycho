"""Acceptance tests for the synthetic joint-power study (#32)."""

import builtins
import inspect

import numpy as np
import pytest

from tycho import power

EXPECTED_SCENARIOS = {
    "zero",
    "weak",
    "strong",
    "cost_heavy",
    "fat_tailed",
    "correlated",
}


@pytest.fixture(scope="module")
def study():
    return power.run_study()


def tiny_study(**overrides):
    options = {"seed": 7, "trials": 4, "scenarios": ["zero", "strong"]}
    options.update(overrides)
    return power.run_study(**options)


def test_default_study_reports_sample_sizes_and_exact_cis(study):
    assert study["synthetic"] is True
    assert study["n_trials_per_scenario"] == power.N_TRIALS
    assert study["n_days"] == power.DAYS
    assert study["null_reps"] == power.NULL_REPS
    assert set(study["scenarios"]) == EXPECTED_SCENARIOS
    for name, item in study["scenarios"].items():
        assert item["n"] == power.N_TRIALS, name
        assert 0 <= item["passes"] <= item["n"], name
        assert item["pass_rate"] == item["passes"] / item["n"], name
        low, high = item["ci95"]
        assert 0.0 <= low <= item["pass_rate"] <= high <= 1.0, name
        assert set(item["criterion_pass_counts"]) == {
            "dsr",
            "alpha",
            "null_top1",
            "ma_components",
            "subperiods",
            "holdout",
        }


def test_zero_edge_rate_is_lower_than_strong_edge_and_rates_are_reported(study):
    zero = study["scenarios"]["zero"]
    strong = study["scenarios"]["strong"]
    assert zero["pass_rate"] < strong["pass_rate"]
    assert zero["passes"] < strong["passes"]
    assert study["false_positive_rate"] == zero["pass_rate"]
    assert study["false_negative_rate"] == 1.0 - strong["pass_rate"]
    # The reported intervals, not an invented scientific cutoff, carry "low".
    assert zero["ci95"][1] < 1.0
    assert strong["ci95"][0] <= strong["pass_rate"] <= strong["ci95"][1]


def test_seeded_determinism():
    first = tiny_study()
    second = tiny_study()
    assert first == second
    assert first["seed"] == 7
    assert first["scenarios"]["zero"] == second["scenarios"]["zero"]


def test_gate_is_consulted_and_holdout_is_never_simulated_as_pass(monkeypatch):
    calls = []
    original = power.gates.evaluate

    def spy(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(power.gates, "evaluate", spy)
    summary = power.run_study(seed=5, trials=3, scenarios=["zero", "strong"])

    assert len(calls) == 6
    for call in calls:
        assert call["holdout_attestation"] is None
        assert call["dsr_threshold"] == 0.95
        assert call["alpha_level"] == 0.90
        assert call["null_top_fraction"] == 0.01
        assert call["component_periods"] == [20, 50, 100, 200]
        assert call["null_reps"] == power.NULL_REPS
        assert len(call["null_metrics"]["scramble"]) == power.NULL_REPS
        assert len(call["null_metrics"]["random_entry"]) == power.NULL_REPS
        assert {"up", "down"} <= set(call["subperiod_net_pnl"])

    for item in summary["scenarios"].values():
        assert item["n"] == 3
        assert item["holdout_status_counts"] == {"BLOCKED": 3}
        assert item["verdict_counts"] == {"BLOCKED": 3}
        assert item["criterion_pass_counts"]["holdout"] == 0
    assert summary["pass_definition"] == (
        "criteria 1-5 PASS; criterion 6 holdout is never simulated"
    )


def test_cost_heavy_fat_tailed_and_correlated_scenarios_run(study):
    strong = study["scenarios"]["strong"]["synthetic_parameters"]
    cost = study["scenarios"]["cost_heavy"]
    fat = study["scenarios"]["fat_tailed"]
    correlated = study["scenarios"]["correlated"]

    assert cost["synthetic_parameters"]["cost_bps"] > strong["cost_bps"]
    assert fat["synthetic_parameters"]["t_df"] is not None
    assert correlated["synthetic_parameters"]["benchmark_correlation"] < 1.0
    for item in (cost, fat, correlated):
        assert item["n"] == power.N_TRIALS
        assert np.isfinite(item["mean_realized_strategy_sharpe"])
        assert item["ci95"][0] <= item["pass_rate"] <= item["ci95"][1]


def test_synthetic_values_are_labelled_and_no_pending_value_is_guessed(study):
    assert study["kind"] == "SYNTHETIC_LOCAL_JOINT_POWER"
    assert study["synthetic_inputs"]["kind"] == "SYNTHETIC_LOCAL_POWER"
    assert study["synthetic_inputs"]["dsr_n_trials_eff"] == power.DSR_N_TRIALS_EFF
    assert study["synthetic_inputs"]["hac_maxlags"] == power.HAC_MAXLAGS
    assert study["synthetic_inputs"]["null_reps"] == power.NULL_REPS
    assert study["synthetic_inputs"]["planted_sharpe_definition"]
    assert study["synthetic_inputs"]["null_analogue"]
    for name, item in study["scenarios"].items():
        parameters = item["synthetic_parameters"]
        assert "not a monograph PENDING value" in parameters["label"], name
        assert all(value != "PENDING" for value in parameters.values()), name
    assert "PENDING" not in study["limitation"]


def test_study_does_not_open_protected_paths_or_any_file(monkeypatch):
    source = inspect.getsource(power)
    for marker in ("data/holdout", "secrets/", "prereg/locked", "tests/golden"):
        assert marker not in source

    def deny_open(*args, **kwargs):
        raise AssertionError("power study must not open files")

    monkeypatch.setattr(builtins, "open", deny_open)
    summary = power.run_study(seed=9, trials=2, scenarios=["correlated"])
    assert summary["scenarios"]["correlated"]["n"] == 2
