import copy
import json

import pandas as pd
import pytest

from tycho import config, feasibility
from tycho.feasibility import audit
from tycho.synthetic import make_synthetic_tape
from tycho.tape import save_tape

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
    assert set(metrics["availability"]["tables"]) == {
        "instruments", "specs", "bars_1d", "funding", "mark"
    }
    assert report["prohibited_metrics_computed"] is False


def test_permissive_thresholds_pass(tape):
    assert audit(tape, PERMISSIVE)["verdict"] == "PASS"


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

    # zero history: only the missing trailing-volume window still excludes a perp
    listed = tiny.instruments[tiny.instruments["kind"] == "perp"]
    counts = []
    for month_start in expected_months:
        alive = listed[
            (listed["listed_at"] <= month_start)
            & (listed["delisted_at"].isna() | (listed["delisted_at"] >= month_start))
        ]
        bars = tiny.bars_1d
        trailing = bars[
            (bars["event_time"] >= month_start - pd.Timedelta(days=30))
            & (bars["event_time"] < month_start)
        ]
        alive = alive[alive["instrument_id"].isin(trailing["instrument_id"])]
        counts.append(len(alive))
    report0 = audit(tiny, PERMISSIVE, min_history_days=0)
    assert [m["eligible"] for m in report0["metrics"]["universe"]["months"]] == counts
    assert report0["metrics"]["universe"]["months_below_30"] == sum(c < 30 for c in counts)


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
