"""Synthetic-only evidence for §14 subperiod/component inputs."""

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tycho import config, e001
from tycho.subperiods import btc_subperiod_labels, net_by_subperiod, run_components
from tycho.synthetic import make_synthetic_tape
from tycho.trials import verify_chain


def btc_bars():
    dates = pd.date_range("2020-01-01", periods=202, tz="UTC")
    return pd.DataFrame({
        "instrument_id": "BTC", "event_time": dates,
        "available_at": dates + pd.Timedelta(hours=1),
        "revision": 0, "close": np.arange(1, 203, dtype=float),
    })


def test_labels_use_available_closed_bars_and_revisions_only():
    bars = btc_bars()
    decisions = bars.available_at.iloc[[198, 199, 200, 201]]
    original = btc_subperiod_labels(SimpleNamespace(bars_1d=bars), decisions, "BTC")
    assert pd.isna(original.iloc[0])
    assert original.iloc[1:].tolist() == ["up", "up", "up"]
    revision = bars.iloc[[199]].copy()
    revision["revision"] = 1
    revision["close"] = 0.5
    revision["available_at"] = decisions.iloc[2] - pd.Timedelta(minutes=30)
    revised = pd.concat([bars, revision], ignore_index=True)
    times = [decisions.iloc[1], revision.available_at.iloc[0], decisions.iloc[2]]
    labels = btc_subperiod_labels(SimpleNamespace(bars_1d=revised), times, "BTC")
    assert labels.tolist() == ["up", "down", "up"]
    # Appending future bars/revisions cannot change earlier labels.
    prefix = revised[revised.available_at <= times[1]]
    pd.testing.assert_series_equal(
        labels.iloc[:2], btc_subperiod_labels(SimpleNamespace(bars_1d=prefix), times[:2], "BTC")
    )
    before_close = bars.event_time.iloc[199]
    assert pd.isna(btc_subperiod_labels(SimpleNamespace(bars_1d=bars), [before_close], "BTC").iloc[0])


def test_same_stamp_available_bar_cannot_change_decision_label():
    bars = btc_bars()
    decision = bars.event_time.iloc[200]
    bars.loc[200, "available_at"] = decision
    bars.loc[200, "close"] = 0.5
    prefix = bars.iloc[:200]
    expected = btc_subperiod_labels(SimpleNamespace(bars_1d=prefix), [decision], "BTC")
    assert expected.iloc[0] == "up"
    actual = btc_subperiod_labels(SimpleNamespace(bars_1d=bars), [decision], "BTC")
    pd.testing.assert_series_equal(actual, expected)
    # Once the timestamp is strictly past the bar, that available close can enter.
    later = btc_subperiod_labels(
        SimpleNamespace(bars_1d=bars), [decision + pd.Timedelta(seconds=1)], "BTC"
    )
    assert later.iloc[0] == "down"


def test_ties_missing_btc_and_invalid_history_are_not_up_or_down():
    bars = btc_bars()
    dates = [bars.available_at.iloc[-1]]
    bars["close"] = 100.0
    assert btc_subperiod_labels(SimpleNamespace(bars_1d=bars), dates, "BTC").iloc[0] == "flat"
    assert btc_subperiod_labels(SimpleNamespace(bars_1d=bars), dates, "absent").isna().all()
    bars.loc[100, "close"] = np.nan
    assert btc_subperiod_labels(SimpleNamespace(bars_1d=bars), dates, "BTC").isna().all()


def test_net_inputs_keep_costs_unknowns_and_absent_subperiods_explicit():
    pnl = pd.Series([10.0, -12.0, -3.0, 7.0])
    labels = pd.Series(["up", "up", "down", pd.NA], dtype="string")
    result = net_by_subperiod(pnl, labels)
    assert result["up"] == {"observations": 2, "net_pnl": -2.0}
    assert result["down"]["net_pnl"] == -3.0
    assert result["flat"] == {"observations": 0, "net_pnl": None}
    assert result["unclassified"]["net_pnl"] == 7.0
    with pytest.raises(ValueError, match="indexes"):
        net_by_subperiod(pnl, labels.iloc[::-1])
    with pytest.raises(ValueError, match="finite"):
        net_by_subperiod(pnl * np.nan, labels)
    with pytest.raises(ValueError, match="unknown"):
        net_by_subperiod(pnl, labels.fillna("calendar"))


def test_components_execute_pipeline_and_log_each_actual_trial(tmp_path, monkeypatch):
    tape = make_synthetic_tape(seed=3, n_perps=2, days=350)
    # Synthetic fixture values only; all research PENDING values remain untouched.
    params = {
        "ewma_convention": {"kind": "span", "value": 60, "returns": "log", "min_periods": 20},
        "max_staleness": "2D", "absolute_band_nav": 0.01,
        "partial_ratio": 1.0, "research_nominal_usd": 1_000_000.0,
        "trials_path": tmp_path / "trials.jsonl",
    }
    periods_seen = []
    original = e001.trend_panels

    def observe(tape, periods, convention):
        periods_seen.append(periods)
        return original(tape, periods, convention)

    monkeypatch.setattr(e001, "trend_panels", observe)
    result = run_components(tape, params, synthetic=True)
    periods = config.load()["signal"]["ma_periods_days"]
    assert list(result) == periods
    assert periods_seen == [[k] for k in periods]
    assert "ma_periods_days" not in params
    records = [json.loads(line) for line in params["trials_path"].read_text().splitlines()]
    assert len(records) == 2 * len(periods)
    assert verify_chain(params["trials_path"])
    assert len({out["params_hash"] for out in result.values()}) == len(periods)
    for i, k in enumerate(periods):
        out = result[k]
        pair = records[2*i:2*i+2]
        assert out["trial_hashes"] == [record["hash"] for record in pair]
        for record in pair:
            assert record["params_hash"] == out["params_hash"]
            assert record["metrics"] == out["metrics"][record["variant"]]
        assert out["metrics"]["main"]["trade_count"] > 0
        assert out["metrics"]["main"]["fees"] > 0


@pytest.mark.parametrize("periods", [[], [20, 20], [0], [20.5], [True]])
def test_invalid_components_refused_before_any_run(periods):
    with pytest.raises(ValueError, match="MA periods"):
        run_components(None, {"ma_periods_days": periods}, synthetic=True)


def test_component_runs_preserve_real_data_and_pending_refusals():
    with pytest.raises(config.PendingParameterError):
        run_components(None, {}, synthetic=False)
    with pytest.raises(config.PendingParameterError):
        run_components(None, {}, synthetic=True)
