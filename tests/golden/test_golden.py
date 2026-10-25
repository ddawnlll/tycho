"""Golden hand-calculated cases for the sequential ledger and rebalance rules.

Cases are written blind from docs/ledger-spec.md; if the ledger disagrees, fix
the ledger, never the case (tests/golden is write-once).
"""

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = Path(__file__).resolve().parent / "cases"
LEDGER_PATH = ROOT / "src" / "tycho" / "ledger.py"
TOL = 1e-9

CASES = sorted(CASE_DIR.glob("*.json"))


def load_case(path):
    return json.loads(Path(path).read_text())


def ledger_module():
    if not LEDGER_PATH.exists():
        pytest.skip("ledger not implemented")
    return importlib.import_module("tycho.ledger")


def close_enough(actual, expected, label):
    assert abs(actual - expected) <= TOL, f"{label}: {actual!r} != {expected!r}"


def nonzero(mapping):
    return {key: qty for key, qty in mapping.items() if qty != 0}


def check_positions(actual, expected, label):
    assert nonzero(actual) == nonzero(expected), f"{label}: {actual!r} != {expected!r}"


@pytest.mark.parametrize("path", CASES, ids=[case.stem for case in CASES])
def test_golden_case(path):
    case = load_case(path)
    ledger = ledger_module()
    fn = getattr(ledger, case["function"])
    inputs = case["inputs"]
    expected = case["expected"]

    if case["function"] == "run_ledger":
        instruments = {
            key: ledger.Instrument(**spec) for key, spec in inputs["instruments"].items()
        }
        snapshots = fn(
            initial_wallet=inputs["initial_wallet"],
            instruments=instruments,
            events=inputs["events"],
        )
        final = snapshots[-1]
        for key in ("wallet", "equity", "unrealized", "realized_cum", "fees_cum", "funding_cum"):
            close_enough(final[key], expected[key], key)
        check_positions(final["positions"], expected["positions"], "positions")
        if "equity_series" in expected:
            got = [snapshot["equity"] for snapshot in snapshots]
            assert len(got) == len(expected["equity_series"]), got
            for index, (actual, want) in enumerate(zip(got, expected["equity_series"])):
                close_enough(actual, want, f"equity_series[{index}]")
        return

    if case["function"] == "rebalance_orders":
        instruments = {
            key: ledger.Instrument(**spec) for key, spec in inputs["instruments"].items()
        }
        orders = fn(
            target_weights=inputs["target_weights"],
            positions=inputs["positions"],
            marks=inputs["marks"],
            equity=inputs["equity"],
            instruments=instruments,
            band_relative=inputs["band_relative"],
            absolute_band_nav=inputs["absolute_band_nav"],
            partial_ratio=inputs["partial_ratio"],
        )
        check_positions(orders, expected["orders"], "orders")
        return

    if case["function"] == "slippage_bps":
        got = fn(**inputs)
        close_enough(got, expected["bps"], "bps")
        return

    raise AssertionError(f"unknown function {case['function']!r}")
