import copy
import json
import math

import numpy as np
import pandas as pd
import pytest

from tycho import config, feasibility
from tycho.feasibility import audit
from tycho.synthetic import make_synthetic_tape
from tycho.tape import TABLE_COLUMNS, save_tape

PERMISSIVE = {
    "min_delisted_coverage": 0.0,
    "min_funding_coverage": 0.0,
    "min_mark_coverage": 0.0,
    "min_spec_coverage": 0.0,
    "max_months_below_30": 10**9,
}
PROHIBITED = ("return", "pnl", "sharpe")


def _keys(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield str(key).lower()
            yield from _keys(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _keys(value)


@pytest.fixture(scope="module")
def tape():
    return make_synthetic_tape(seed=7, n_perps=12, days=120)


def test_pending_thresholds_blocked(tape):
    report = audit(tape, "PENDING")
    assert report["verdict"] == "BLOCKED"
    assert report["reasons"] == ["G0 sufficiency thresholds PENDING"]
    metrics = report["metrics"]
    assert metrics["listings"]["perps"] == 12
    assert metrics["listings"]["delisted_perps"] == 3
    assert metrics["listings"]["delisted_coverage"] == 1.0
    assert set(metrics["availability"]["late_over_1d_share"]) == set(TABLE_COLUMNS)
    assert report["prohibited_metrics_computed"] is False


def test_permissive_thresholds_pass(tape):
    assert audit(tape, PERMISSIVE)["verdict"] == "PASS"


def test_threshold_failure_incomplete(tape):
    strict = dict(PERMISSIVE, min_delisted_coverage=1.1)
    report = audit(tape, strict)
    assert report["verdict"] == "INCOMPLETE"
    assert report["reasons"] == ["threshold failed: min_delisted_coverage"]


def test_delisted_perp_without_bars_incomplete(tape):
    broken = copy.copy(tape)
    victim = broken.instruments[
        broken.instruments["delisted_at"].notna()
    ]["instrument_id"].iloc[0]
    broken.bars_1d = tape.bars_1d[tape.bars_1d["instrument_id"] != victim]
    report = audit(broken, PERMISSIVE)
    assert report["verdict"] == "INCOMPLETE"
    assert any("delisted perps without bars" in reason for reason in report["reasons"])


def test_early_available_at_incomplete(tape):
    broken = copy.copy(tape)
    broken.mark = tape.mark.copy()
    broken.mark.loc[broken.mark.index[0], "available_at"] = (
        broken.mark.loc[broken.mark.index[0], "event_time"] - pd.Timedelta(minutes=1)
    )
    report = audit(broken, PERMISSIVE)
    assert report["verdict"] == "INCOMPLETE"
    assert any("available_at before event_time" in r for r in report["reasons"])


def _tiny_tape(n_perps=3, days=50):
    return make_synthetic_tape(seed=3, n_perps=n_perps, days=days)


def test_universe_months_below_30():
    tiny = _tiny_tape()
    # hand-known: 3 perps, listed over the first days; no month can reach 30
    report = audit(tiny, PERMISSIVE, min_history_days=10)
    months = report["metrics"]["universe"]["months"]
    expected_months = pd.date_range(
        tiny.bars_1d["event_time"].min().normalize().replace(day=1),
        tiny.bars_1d["event_time"].max(),
        freq="MS",
    )
    assert [m["month"] for m in months] == [ts.strftime("%Y-%m") for ts in expected_months]
    assert all(m["eligible"] <= 3 for m in months)
    assert report["metrics"]["universe"]["months_below_30"] == len(months)
    assert report["metrics"]["universe"]["first_month_with_30"] is None

    # zero history: only the missing trailing-volume window still excludes a perp,
    # counted from the month-start view
    counts = []
    for month_start in expected_months:
        view = tiny.as_of(month_start)
        latest = view.instruments.sort_values(["event_time", "revision"]).groupby(
            "instrument_id"
        ).tail(1)
        trailing = view.bars_1d[
            (view.bars_1d["event_time"] >= month_start - pd.Timedelta(days=30))
            & (view.bars_1d["event_time"] < month_start)
        ]
        alive = latest[
            (latest["kind"] == "perp")
            & (latest["listed_at"] <= month_start)
            & (latest["delisted_at"].isna() | (latest["delisted_at"] >= month_start))
        ]
        counts.append(int(alive["instrument_id"].isin(trailing["instrument_id"]).sum()))
    report0 = audit(tiny, PERMISSIVE, min_history_days=0)
    assert [m["eligible"] for m in report0["metrics"]["universe"]["months"]] == counts
    assert report0["metrics"]["universe"]["months_below_30"] == sum(c < 30 for c in counts)


def test_universe_is_point_in_time(tape):
    btc = tape.bars_1d["instrument_id"] == "BTCUSDT"
    lagged = copy.copy(tape)
    bars = tape.bars_1d.copy()
    # BTC bars exist before the month start but only become available much later
    bars.loc[btc, "available_at"] += pd.Timedelta(days=200)
    lagged.bars_1d = bars
    removed = copy.copy(tape)
    removed.bars_1d = tape.bars_1d[~btc]

    def november(t):
        report = audit(t, PERMISSIVE, min_history_days=0)
        return {m["month"]: m["eligible"] for m in report["metrics"]["universe"]["months"]}["2019-11"]

    # at the November month start the lagged BTC bars are invisible, so it must
    # count exactly as if they did not exist at all
    assert november(lagged) == november(removed)


def test_late_arrival_share_replaces_tautological_ratio(tape):
    report = audit(tape, PERMISSIVE)
    late = report["metrics"]["availability"]["late_over_1d_share"]
    # the synthetic bar revision arrives two days after its event
    assert late["bars_1d"] > 0.0
    assert late["funding"] == 0.0


def _survivor_tape(tape):
    survivors = copy.copy(tape)
    survivors.instruments = tape.instruments.assign(
        delisted_at=pd.NaT, last_trade_day=pd.NaT
    )
    return survivors


def test_survivor_only_tape_incomplete(tape):
    report = audit(_survivor_tape(tape), PERMISSIVE)
    assert report["verdict"] == "INCOMPLETE"
    assert "no delisted perps: survivorship cannot be audited" in report["reasons"]


def test_delisted_coverage_nan_without_delisted_perps(tape):
    listings = audit(_survivor_tape(tape), PERMISSIVE)["metrics"]["listings"]
    assert listings["delisted_perps"] == 0
    assert math.isnan(listings["delisted_coverage"])


def test_perp_without_funding_interval_is_structural_defect(tape):
    broken = copy.copy(tape)
    specs = tape.specs.copy()
    specs.loc[specs["instrument_id"] == "BTCUSDT", "funding_interval_hours"] = np.nan
    broken.specs = specs
    report = audit(broken, PERMISSIVE)
    assert report["verdict"] == "INCOMPLETE"
    assert any(
        "funding_interval_hours" in reason and "BTCUSDT" in reason
        for reason in report["reasons"]
    )


@pytest.mark.parametrize("field", ["tick_size", "step_size", "min_notional",
                                   "contract_multiplier", "funding_interval_hours"])
def test_spec_coverage_requires_complete_contract_filters(tape, field):
    broken = copy.copy(tape)
    specs = tape.specs.copy()
    specs.loc[specs["instrument_id"] == "BTCUSDT", field] = np.nan
    broken.specs = specs
    report = audit(broken, PERMISSIVE)
    btc_bars = int((tape.bars_1d["instrument_id"] == "BTCUSDT").sum())
    assert report["metrics"]["spec_coverage"] == pytest.approx(
        1.0 - btc_bars / len(tape.bars_1d)
    )


def test_mark_coverage_is_per_instrument_min(tape):
    broken = copy.copy(tape)
    broken.mark = tape.mark[tape.mark["instrument_id"] != "BTCUSDT"]
    report = audit(broken, PERMISSIVE)
    assert report["metrics"]["mark_coverage"]["min"] == 0.0
    assert report["metrics"]["mark_coverage"]["median"] == 1.0

    strict = dict(PERMISSIVE, min_mark_coverage=0.5)
    strict_report = audit(broken, strict)
    assert strict_report["verdict"] == "INCOMPLETE"
    assert "threshold failed: min_mark_coverage" in strict_report["reasons"]


def test_no_prohibited_keys(tape):
    report = audit(tape, PERMISSIVE)
    assert not [k for k in _keys(report) if any(p in k for p in PROHIBITED)]


def test_cli_writes_blocked_json(tape, tmp_path, monkeypatch):
    save_tape(tape, tmp_path / "tape")
    out = tmp_path / "report.json"
    feasibility.main(["--tape", str(tmp_path / "tape"), "--out", str(out)])
    report = json.loads(out.read_text())
    assert report["verdict"] == "BLOCKED"
    assert report["metrics"]["listings"]["perps"] == 12
    assert config.load()["g0"]["data_sufficiency_thresholds"] == "PENDING"
