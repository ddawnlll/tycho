"""Acceptance tests for the E-001 six-criteria gate evaluator (#31)."""

import copy
import inspect
import json

import pytest

from tycho.gates import evaluate


def gate_inputs(**overrides):
    payload = {
        "dsr": 0.97,
        "alpha": {"alpha": 0.0005, "p_one_sided": 0.05},
        "strategy_metric": 2.0,
        "null_metrics": {
            "scramble": [-0.2, 0.0, 0.1, 0.3, 0.5],
            "random_entry": [-0.1, 0.0, 0.2, 0.4, 0.6],
        },
        "component_net_pnl": {20: 1.0, 50: 2.0, 100: 3.0, 200: 4.0},
        "subperiod_net_pnl": {"up": 1.0, "down": 2.0},
        "holdout_attestation": {
            "owner": "owner-a",
            "signed": True,
            "net_sharpe_positive": True,
        },
        "dsr_threshold": 0.95,
        "alpha_level": 0.90,
        "null_top_fraction": 0.01,
    }
    payload.update(overrides)
    return payload


def statuses(report):
    return {name: item["status"] for name, item in report["criteria"].items()}


def test_all_criteria_pass_and_report_is_json_safe():
    report = evaluate(**gate_inputs())
    assert report["verdict"] == "PASS"
    assert statuses(report) == {
        "dsr": "PASS",
        "alpha": "PASS",
        "null_top1": "PASS",
        "ma_components": "PASS",
        "subperiods": "PASS",
        "holdout": "OWNER",
    }
    assert report["thresholds"] == {
        "dsr": 0.95,
        "alpha_level": 0.90,
        "null_top_fraction": 0.01,
    }
    assert json.loads(json.dumps(report, allow_nan=False)) == report


def test_thresholds_have_no_defaults_and_are_required():
    parameters = inspect.signature(evaluate).parameters
    assert set(parameters) == {
        "dsr",
        "alpha",
        "strategy_metric",
        "null_metrics",
        "component_net_pnl",
        "subperiod_net_pnl",
        "holdout_attestation",
        "dsr_threshold",
        "alpha_level",
        "null_top_fraction",
    }
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())


def test_dsr_boundary_is_inclusive():
    at = evaluate(**gate_inputs(dsr=0.95))
    assert at["verdict"] == "PASS"
    assert at["criteria"]["dsr"]["status"] == "PASS"
    below = evaluate(**gate_inputs(dsr=0.949999))
    assert below["verdict"] == "FAIL"
    assert below["criteria"]["dsr"]["status"] == "FAIL"


def test_alpha_must_be_strictly_positive_and_significant():
    zero = evaluate(**gate_inputs(alpha={"alpha": 0.0, "p_one_sided": 0.0}))
    assert zero["verdict"] == "FAIL"
    assert zero["criteria"]["alpha"]["status"] == "FAIL"

    tiny = evaluate(**gate_inputs(alpha={"alpha": 1e-12, "p_one_sided": 0.05}))
    assert tiny["verdict"] == "PASS"

    boundary_p = evaluate(**gate_inputs(alpha={"alpha": 0.001, "p_one_sided": 0.10}))
    assert boundary_p["verdict"] == "FAIL"
    assert boundary_p["criteria"]["alpha"]["status"] == "FAIL"


def test_top_one_percent_boundary_is_strict_for_both_nulls():
    equal = {"scramble": [0.0, 1.0], "random_entry": [0.0, 1.0]}
    at_cutoff = evaluate(**gate_inputs(strategy_metric=0.99, null_metrics=equal))
    assert at_cutoff["verdict"] == "FAIL"
    assert at_cutoff["criteria"]["null_top1"]["status"] == "FAIL"

    above = evaluate(**gate_inputs(strategy_metric=0.990001, null_metrics=equal))
    assert above["verdict"] == "PASS"
    assert above["criteria"]["null_top1"]["status"] == "PASS"


def test_both_null_distributions_are_required():
    only_scramble = gate_inputs()
    del only_scramble["null_metrics"]["random_entry"]
    report = evaluate(**only_scramble)
    assert report["verdict"] == "BLOCKED"
    assert report["criteria"]["null_top1"]["status"] == "BLOCKED"

    only_random = gate_inputs()
    del only_random["null_metrics"]["scramble"]
    report = evaluate(**only_random)
    assert report["verdict"] == "BLOCKED"
    assert report["criteria"]["null_top1"]["status"] == "BLOCKED"

    below_one = evaluate(
        **gate_inputs(
            strategy_metric=0.1,
            null_metrics={"scramble": [0.0, 1.0], "random_entry": [-1.0, 0.0]},
        )
    )
    assert below_one["verdict"] == "FAIL"
    assert below_one["criteria"]["null_top1"]["status"] == "FAIL"


def test_missing_input_is_blocked_even_when_other_criteria_pass():
    report = evaluate(**gate_inputs(dsr=None))
    assert report["verdict"] == "BLOCKED"
    assert report["criteria"]["dsr"]["status"] == "BLOCKED"
    assert report["criteria"]["alpha"]["status"] == "PASS"
    assert report["criteria"]["holdout"]["status"] == "OWNER"


@pytest.mark.parametrize(
    "field",
    [
        "dsr",
        "alpha",
        "strategy_metric",
        "null_metrics",
        "component_net_pnl",
        "subperiod_net_pnl",
        "holdout_attestation",
    ],
)
def test_absent_inputs_are_blocked(field):
    report = evaluate(**gate_inputs(**{field: None}))
    assert report["verdict"] == "BLOCKED"


def test_missing_nested_input_is_blocked():
    for overrides in (
        {"alpha": {"alpha": 0.001}},
        {"null_metrics": {"scramble": [0.0, 1.0]}},
        {"subperiod_net_pnl": {"up": 1.0}},
        {"holdout_attestation": {"owner": "a", "signed": True}},
    ):
        report = evaluate(**gate_inputs(**overrides))
        assert report["verdict"] == "BLOCKED"


@pytest.mark.parametrize(
    "field",
    [
        "dsr",
        "alpha",
        "strategy_metric",
        "null_metrics",
        "component_net_pnl",
        "subperiod_net_pnl",
        "holdout_attestation",
        "alpha_level",
    ],
)
def test_pending_input_is_blocked(field):
    report = evaluate(**gate_inputs(**{field: "PENDING"}))
    assert report["verdict"] == "BLOCKED"


def test_blocked_takes_precedence_over_fail():
    report = evaluate(**gate_inputs(dsr=0.10, alpha=None))
    assert report["verdict"] == "BLOCKED"
    assert report["criteria"]["dsr"]["status"] == "FAIL"
    assert report["criteria"]["alpha"]["status"] == "BLOCKED"


def test_invalid_takes_precedence_over_blocked_and_fail():
    report = evaluate(
        **gate_inputs(dsr=0.10, alpha={"alpha": float("nan"), "p_one_sided": 0.05})
    )
    assert report["verdict"] == "INVALID"
    assert report["criteria"]["dsr"]["status"] == "FAIL"
    assert report["criteria"]["alpha"]["status"] == "INVALID"

    both = evaluate(**gate_inputs(dsr=None, alpha={"alpha": float("nan"), "p_one_sided": 0.05}))
    assert both["verdict"] == "INVALID"


@pytest.mark.parametrize(
    "field,value",
    [
        ("dsr", float("nan")),
        ("dsr", float("inf")),
        ("dsr", -0.01),
        ("dsr", 1.01),
        ("dsr", "high"),
        ("dsr", True),
        ("alpha", {"alpha": float("nan"), "p_one_sided": 0.05}),
        ("alpha", {"alpha": float("inf"), "p_one_sided": 0.05}),
        ("alpha", {"alpha": 0.001, "p_one_sided": -0.1}),
        ("alpha", {"alpha": 0.001, "p_one_sided": 1.1}),
        ("alpha", {"alpha": "x", "p_one_sided": 0.05}),
        ("alpha", {"alpha": 0.001, "p_one_sided": 0.05, "extra": 1}),
        ("strategy_metric", float("nan")),
        ("strategy_metric", float("inf")),
        ("strategy_metric", "high"),
        ("null_metrics", {"scramble": [], "random_entry": [0.0]}),
        ("null_metrics", {"scramble": [float("nan")], "random_entry": [0.0]}),
        ("null_metrics", {"scramble": [float("inf")], "random_entry": [0.0]}),
        ("null_metrics", {"scramble": "x", "random_entry": [0.0]}),
        ("null_metrics", {"scramble": [0.0], "random_entry": [0.0], "other": [0.0]}),
        ("component_net_pnl", {}),
        ("component_net_pnl", [(20, 1.0), (20, 2.0)]),
        ("component_net_pnl", {20.0: 1.0}),
        ("component_net_pnl", {-20: 1.0}),
        ("component_net_pnl", {20: float("nan")}),
        ("subperiod_net_pnl", {"up": 1.0, "down": 2.0, "flat": 3.0}),
        ("subperiod_net_pnl", {"up": float("nan"), "down": 2.0}),
        ("subperiod_net_pnl", {"up": "x", "down": 2.0}),
        ("holdout_attestation", ["owner-a", True, True]),
        ("holdout_attestation", {"owner": "", "signed": True, "net_sharpe_positive": True}),
        ("holdout_attestation", {"owner": 3, "signed": True, "net_sharpe_positive": True}),
        ("holdout_attestation", {"owner": "a", "signed": 1, "net_sharpe_positive": True}),
        (
            "holdout_attestation",
            {"owner": "a", "signed": True, "net_sharpe_positive": True, "extra": 1},
        ),
        ("dsr_threshold", float("nan")),
        ("dsr_threshold", 1.5),
        ("alpha_level", 1.0),
        ("null_top_fraction", 0.0),
        ("null_top_fraction", 1.0),
    ],
)
def test_malformed_inputs_are_invalid(field, value):
    report = evaluate(**gate_inputs(**{field: value}))
    assert report["verdict"] == "INVALID"
    json.dumps(report, allow_nan=False)


def test_threshold_none_is_blocked_not_invalid():
    report = evaluate(**gate_inputs(alpha_level=None))
    assert report["verdict"] == "BLOCKED"
    assert report["criteria"]["alpha"]["status"] == "BLOCKED"


def test_holdout_unsigned_is_never_pass_even_with_one_to_five_passing():
    for attestation in (
        None,
        {"owner": "owner-a", "signed": False, "net_sharpe_positive": True},
        {"owner": "owner-a", "signed": True, "net_sharpe_positive": False},
    ):
        report = evaluate(**gate_inputs(holdout_attestation=attestation))
        assert report["verdict"] == "BLOCKED"
        assert report["criteria"]["holdout"]["status"] == "BLOCKED"
        for name in ("dsr", "alpha", "null_top1", "ma_components", "subperiods"):
            assert report["criteria"][name]["status"] == "PASS"


def test_holdout_attested_passes_and_stays_owner():
    report = evaluate(**gate_inputs())
    assert report["verdict"] == "PASS"
    assert report["criteria"]["holdout"]["status"] == "OWNER"
    assert "data not read" in report["criteria"]["holdout"]["reason"]


@pytest.mark.parametrize(
    "field,value,criterion",
    [
        ("dsr", 0.50, "dsr"),
        ("alpha", {"alpha": 0.0, "p_one_sided": 0.01}, "alpha"),
        ("strategy_metric", -1.0, "null_top1"),
        ("component_net_pnl", {20: 1.0, 50: 0.0}, "ma_components"),
        ("subperiod_net_pnl", {"up": 1.0, "down": -1.0}, "subperiods"),
    ],
)
def test_any_fail_among_criteria_one_to_five_is_fail(field, value, criterion):
    report = evaluate(**gate_inputs(**{field: value}))
    assert report["verdict"] == "FAIL"
    assert report["criteria"][criterion]["status"] == "FAIL"


def test_component_pairs_input_is_accepted():
    report = evaluate(
        **gate_inputs(component_net_pnl=[(20, 1.0), (50, 2.0), (100, 3.0), (200, 4.0)])
    )
    assert report["verdict"] == "PASS"
    assert report["criteria"]["ma_components"]["status"] == "PASS"


def test_repeated_calls_are_deterministic_and_do_not_mutate_inputs():
    payload = gate_inputs()
    snapshot = copy.deepcopy(payload)
    first = evaluate(**payload)
    second = evaluate(**payload)
    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert payload == snapshot


def test_evaluator_never_reads_a_file(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("gate evaluator must not read holdout or any file")

    monkeypatch.setattr("builtins.open", forbidden)
    report = evaluate(**gate_inputs())
    assert report["verdict"] == "PASS"
