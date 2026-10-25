"""Section 08 core rule: full history and a trailing window must give the same last-row weights."""

import dataclasses

import numpy as np
import pandas as pd
import pytest

from tycho.contracts import SnapshotRef
from tycho.exp_trend import TrendExpert
from tycho.factors import compute_factors, ewma_cov
from tycho.marketstate import MarketStateProvider
from tycho.portfolio import target_weights
from tycho.runtime import run_expert
from tycho.synthetic import make_synthetic_tape
from tycho.vectorized import trend_panels, universe_panel, weights_panel

MA_PERIODS = (20, 50, 100, 200)
# synthetic test convention only: the monograph value is PENDING
CONVENTION = {"kind": "span", "value": 60, "returns": "log", "min_periods": 20}
MAX_STALENESS = pd.Timedelta("2D")
TARGET_VOL, RHO, N, VOL_CAP, GROSS_CAP = 0.20, 0.7, 30, 2.0, 2.0
WINDOWS = (260, 400)  # test parameter only: the binding live window is BLOCKED (X3.a)
FACTORS = tuple(f"ma_{k}" for k in MA_PERIODS) + ("ewma_vol_60d",)
# panel row at bar close t is the decision on that close, i.e. one minute later
CLOSE_LAG = pd.Timedelta(minutes=1)
# dates where the 400-bar window still holds every listed bar, so the window
# path abstains on nothing: a short window's own missing bars are a window
# artifact, not a difference between the two code paths
CLOSES = [pd.Timestamp(d, tz="UTC") for d in ("2021-06-01", "2021-07-01", "2021-08-02")]


def ewma_tolerance(window):
    """Relative tolerance from the EWMA decay over the window.

    adjust=False recursion: v_n - v'_n = (1-a)^(n-s) (v_s - v'_s), with s the
    window's first return (window - 1 bars before the last). That power of
    (1-a) bounds the decay; 10x covers the ratio of the seed difference to the
    final variance and the min_periods masking of the first bars.
    """
    a = 2.0 / (CONVENTION["value"] + 1.0)
    return 10.0 * (1.0 - a) ** (window - CONVENTION["min_periods"])


class WindowExpert(TrendExpert):
    """TrendExpert whose request lookback is the window under test."""

    def __init__(self, ma_periods, max_staleness, window):
        super().__init__(ma_periods, max_staleness)
        self.window = int(window)

    def request(self, as_of):
        base = super().request(as_of)
        return dataclasses.replace(
            base, timeframes=(("1d", self.window, ("close", "volume")),)
        )


@pytest.fixture(scope="module")
def tape():
    return make_synthetic_tape(seed=7, n_perps=12, days=900)


@pytest.fixture(scope="module")
def provider(tape):
    return MarketStateProvider(tape, universe_size=30, min_history_days=260)


@pytest.fixture(scope="module")
def panels(tape):
    return trend_panels(tape, MA_PERIODS, CONVENTION)


@pytest.fixture(scope="module")
def universe(provider, panels):
    return universe_panel(provider, panels["score"].index)


def factor_fn(snapshot):
    return compute_factors(snapshot, FACTORS, CONVENTION)


def snapshot_path(provider, window, as_of):
    expert = WindowExpert(MA_PERIODS, MAX_STALENESS, window)
    signals = run_expert(expert, provider, as_of, factor_fn)
    snapshot = provider.resolve(expert.request(as_of))
    frame = factor_fn(snapshot)
    return signals, snapshot, frame


def full_history_cov(tape, date, instruments):
    """EWMA covariance over every bar available by date (the research path)."""
    instruments = list(instruments)
    if not instruments:
        return pd.DataFrame(index=instruments, columns=instruments, dtype=float)
    view = tape.as_of(date)
    bars = view.bars_1d[view.bars_1d["instrument_id"].isin(instruments)]
    snapshot = SnapshotRef(
        snapshot_id="full-history",
        as_of=date,
        dataset_hash="",
        universe_hash="",
        request_hash="",
        universe=tuple(instruments),
        bars=bars.reset_index(drop=True),
        availability=pd.DataFrame(index=pd.Index(instruments, name="instrument_id")),
    )
    return ewma_cov(snapshot, instruments, CONVENTION)


@pytest.mark.parametrize("window", WINDOWS)
def test_score_and_vol_rows_match_full_history(provider, panels, window):
    checked = 0
    for close in CLOSES:
        as_of = close + CLOSE_LAG
        signals, _, frame = snapshot_path(provider, window, as_of)
        for signal in signals:
            if signal.direction == "ABSTAIN":
                continue
            iid = signal.instrument_id
            assert panels["score"].loc[close, iid] == signal.score
            expected = float(frame.values.at[iid, "ewma_vol_60d"])
            np.testing.assert_allclose(
                panels["vol"].loc[close, iid],
                expected,
                rtol=ewma_tolerance(window),
                equal_nan=False,
            )
            checked += 1
    assert checked >= 3  # the synthetic tape must exercise the comparison


@pytest.mark.parametrize("window", WINDOWS)
def test_weights_rows_match_snapshot_path(tape, provider, panels, universe, window):
    rows = weights_panel(
        panels,
        universe,
        lambda date, active: full_history_cov(tape, date, active),
        TARGET_VOL,
        RHO,
        N,
        VOL_CAP,
        GROSS_CAP,
    )
    checked = 0
    for close in CLOSES:
        as_of = close + CLOSE_LAG
        signals, snapshot, frame = snapshot_path(provider, window, as_of)
        iids = list(snapshot.universe)
        scores = pd.Series(
            [s.score for s in signals], index=iids, dtype=float
        )  # ABSTAIN is None -> NaN
        vols = frame.values["ewma_vol_60d"].reindex(iids)
        active = list(scores.index[scores.notna() & vols.notna() & (vols > 0)])
        expected, _ = target_weights(
            scores,
            vols,
            ewma_cov(snapshot, active, CONVENTION),
            TARGET_VOL,
            RHO,
            N,
            VOL_CAP,
            GROSS_CAP,
        )
        got = rows.loc[close].reindex(iids).fillna(0.0)
        assert (expected != 0.0).any()  # all-zero rows would make the equality vacuous
        np.testing.assert_allclose(
            got.to_numpy(dtype=float),
            expected.to_numpy(dtype=float),
            rtol=ewma_tolerance(window),
            atol=0.0,
        )
        checked += 1
    assert checked == len(CLOSES)


def test_window_too_short_abstains_instead_of_reweighting(provider, panels):
    # 150 bars cannot define MA200: the windowed Expert must abstain, not size
    close = CLOSES[0]
    signals, _, _ = snapshot_path(provider, 150, close + CLOSE_LAG)
    assert signals
    assert all(signal.direction == "ABSTAIN" for signal in signals)
    assert any(s.abstain_reason == "nan_ma" for s in signals)
    # the same row on the full-history panel still has a defined score, so the
    # abstain comes from the short window and not from the data
    members = universe_panel(provider, [close]).loc[close]
    assert panels["score"].loc[close, members[members].index].notna().any()
