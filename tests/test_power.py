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

# Fixed default seed, 40 trials per scenario.  These are regression pins, not
# scientific pass bounds: any generator, gate or conjunction change must show
# up here and be explained.
EXPECTED_PASSES = {
    "zero": 0,
    "weak": 0,
    "strong": 4,
    "cost_heavy": 4,
    "fat_tailed": 6,
    "correlated": 5,
}


@pytest.fixture(scope="module")
def study():
    return power.run_study()


@pytest.fixture(scope="module")
def mutation_baseline():
    return power.run_study(seed=7, trials=8, scenarios=["strong"])


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


def test_seeded_pass_counts_are_pinned(study):
    observed = {name: item["passes"] for name, item in study["scenarios"].items()}
    assert observed == EXPECTED_PASSES


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


def test_planted_sharpe_is_annualized_in_the_generator():
    daily_vol = 0.6 / np.sqrt(365.0)
    for planted in (0.0, 1.0, 6.0):
        drift = power._daily_drift(planted, 0.6)
        assert drift == pytest.approx(planted * daily_vol / np.sqrt(365.0))
        assert drift / daily_vol * np.sqrt(365.0) == pytest.approx(planted)


def test_measured_oracle_sharpe_matches_planted_annualized_value():
    daily_vol = 0.6 / np.sqrt(365.0)
    planted = 6.0
    oracle_means = []
    for seed in range(40):
        returns, _, state = power._daily_returns(
            np.random.default_rng(seed),
            planted_sharpe=planted,
            annual_vol=0.6,
            regime_switch_prob=0.0,
            t_df=None,
            benchmark_correlation=1.0,
        )
        oracle_means.append(float(np.mean(state * returns)))
    annualized = float(np.mean(oracle_means) / daily_vol * np.sqrt(365.0))
    assert annualized == pytest.approx(planted, rel=0.05)


def test_seeded_determinism():
    first = tiny_study()
    second = tiny_study()
    assert first == second
    assert first["seed"] == 7
    assert first["scenarios"]["zero"] == second["scenarios"]["zero"]


def test_gate_is_consulted_and_holdout_is_never_simulated_as_pass(monkeypatch):
    calls = []
    reports = []
    original = power.gates.evaluate

    def spy(**kwargs):
        calls.append(kwargs)
        report = original(**kwargs)
        reports.append(report)
        return report

    monkeypatch.setattr(power.gates, "evaluate", spy)
    summary = power.run_study(seed=5, trials=3, scenarios=["zero", "strong"])

    assert len(calls) == 6
    assert len(reports) == 6
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

    # The count is the five-way conjunction of the gate's own statuses.
    expected = sum(
        all(
            report["criteria"][criterion]["status"] == power.gates.PASS
            for criterion in power.DEV_CRITERIA
        )
        for report in reports
    )
    observed = sum(item["passes"] for item in summary["scenarios"].values())
    assert observed == expected

    for item in summary["scenarios"].values():
        assert item["n"] == 3
        assert item["holdout_status_counts"] == {"BLOCKED": 3}
        assert item["verdict_counts"] == {"BLOCKED": 3}
        assert item["criterion_pass_counts"]["holdout"] == 0
    assert summary["pass_definition"] == (
        "criteria 1-5 PASS; criterion 6 holdout is never simulated"
    )


@pytest.mark.parametrize(
    "criterion", ["dsr", "alpha", "null_top1", "ma_components", "subperiods"]
)
def test_dev_pass_is_the_five_way_conjunction(
    monkeypatch, criterion, mutation_baseline
):
    baseline = mutation_baseline["scenarios"]["strong"]["passes"]
    assert baseline > 0

    original = power.gates.evaluate

    def forced(**kwargs):
        report = original(**kwargs)
        report["criteria"][criterion]["status"] = power.gates.FAIL
        return report

    monkeypatch.setattr(power.gates, "evaluate", forced)
    mutated = power.run_study(seed=7, trials=8, scenarios=["strong"])
    assert mutated["scenarios"]["strong"]["passes"] < baseline
    assert mutated["scenarios"]["strong"]["passes"] == 0


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
        assert np.isfinite(item["mean_realized_annualized_strategy_sharpe"])
        assert item["ci95"][0] <= item["pass_rate"] <= item["ci95"][1]


def test_synthetic_values_are_labelled_and_no_pending_value_is_guessed(study):
    assert study["kind"] == "SYNTHETIC_LOCAL_JOINT_POWER"
    assert study["synthetic_inputs"]["kind"] == "SYNTHETIC_LOCAL_POWER"
    assert study["synthetic_inputs"]["dsr_n_trials_eff"] == power.DSR_N_TRIALS_EFF
    assert study["synthetic_inputs"]["hac_maxlags"] == power.HAC_MAXLAGS
    assert study["synthetic_inputs"]["null_reps"] == power.NULL_REPS
    assert "annualized" in study["synthetic_inputs"]["units"]
    assert "optimistic" in study["synthetic_inputs"]["null_reps_note"]
    assert study["synthetic_inputs"]["planted_sharpe_definition"]
    assert study["synthetic_inputs"]["null_analogue"]
    assert "not E-001 joint power" in study["scope"]
    assert "raw MA ensemble" in study["approximations"]
    assert "not the D-40 equal-risk" in study["approximations"]
    for name, item in study["scenarios"].items():
        parameters = item["synthetic_parameters"]
        assert "not a monograph PENDING value" in parameters["label"], name
        assert all(value != "PENDING" for value in parameters.values()), name
        assert "mean_realized_annualized_strategy_sharpe" in item
    assert "PENDING" not in study["limitation"]


def test_scope_and_units_are_disclosed_in_the_report(study):
    text = power.report(study)
    assert "realized SR (ann.)" in text
    assert "units:" in text
    assert "scope:" in text
    assert "approximations:" in text
    assert "slightly optimistic" in text
    assert "raw buy-and-hold proxy" in text


def test_study_does_not_open_protected_paths_or_any_file(monkeypatch):
    source = inspect.getsource(power)
    for marker in ("data/holdout", "secrets/", "prereg/locked", "tests/golden"):
        assert marker not in source

    def deny_open(*args, **kwargs):
        raise AssertionError("power study must not open files")

    monkeypatch.setattr(builtins, "open", deny_open)
    summary = power.run_study(seed=9, trials=2, scenarios=["correlated"])
    assert summary["scenarios"]["correlated"]["n"] == 2
