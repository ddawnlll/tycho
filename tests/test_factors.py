import math

import numpy as np
import pandas as pd
import pytest

from tycho.config import PendingParameterError
from tycho.contracts import MarketStateRequest, SnapshotRef
from tycho.factors import compute_factors, ewma_cov
from tycho.marketstate import MarketStateProvider
from tycho.synthetic import make_synthetic_tape

CONVENTION = {"kind": "span", "value": 60, "returns": "log", "min_periods": 20}


def tiny_snapshot(closes_by_id, liquidity_by_id=None, snapshot_id="snap-1"):
    """Bars starting 2020-01-01, one bar per day, close/quote_volume supplied."""
    liquidity_by_id = liquidity_by_id or {}
    frames = []
    for iid, closes in closes_by_id.items():
        stamps = pd.date_range("2020-01-01", periods=len(closes), freq="D", tz="UTC")
        frames.append(pd.DataFrame({
            "instrument_id": iid,
            "event_time": stamps,
            "close": np.asarray(closes, dtype=float),
            "quote_volume": np.asarray(
                liquidity_by_id.get(iid, np.ones(len(closes))), dtype=float
            ),
        }))
    bars = pd.concat(frames, ignore_index=True)
    return SnapshotRef(
        snapshot_id=snapshot_id,
        as_of=pd.Timestamp("2020-06-01", tz="UTC"),
        dataset_hash="dataset",
        universe_hash="universe",
        request_hash="request",
        universe=tuple(closes_by_id),
        bars=bars,
        availability=pd.DataFrame(index=pd.Index(list(closes_by_id), name="instrument_id")),
    )


def test_ma_and_close_hand_computed():
    closes = list(range(1, 36))  # 35 bars, 1..35
    liquidity = [2.0 * i for i in range(1, 36)]
    snapshot = tiny_snapshot({"AAA": closes}, {"AAA": liquidity})

    frame = compute_factors(
        snapshot, ["ma_20", "ma_50", "close", "trailing_30d_liquidity"], CONVENTION
    )

    assert frame.snapshot_id == snapshot.snapshot_id
    assert list(frame.values.columns) == [
        "ma_20", "ma_50", "close", "trailing_30d_liquidity"
    ]
    row = frame.values.loc["AAA"]
    assert row["ma_20"] == pytest.approx(np.mean(closes[-20:]))  # mean(16..35) = 25.5
    assert math.isnan(row["ma_50"])  # only 35 bars
    assert row["close"] == 35.0
    assert row["trailing_30d_liquidity"] == pytest.approx(np.mean(liquidity[-30:]))


def test_nan_when_not_enough_bars():
    snapshot = tiny_snapshot({"AAA": [1.0] * 19}, {"AAA": [1.0] * 19})

    frame = compute_factors(
        snapshot, ["ma_20", "trailing_30d_liquidity"], CONVENTION
    )

    assert math.isnan(frame.values.loc["AAA", "ma_20"])
    assert math.isnan(frame.values.loc["AAA", "trailing_30d_liquidity"])


def test_unknown_factor_raises():
    snapshot = tiny_snapshot({"AAA": [1.0] * 5})
    with pytest.raises(ValueError, match="unknown factor"):
        compute_factors(snapshot, ["ma_21"], CONVENTION)


def test_ewma_vol_matches_direct_recursion():
    rng = np.random.default_rng(3)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, size=80)))
    snapshot = tiny_snapshot({"AAA": closes})

    frame = compute_factors(snapshot, ["ewma_vol_60d"], CONVENTION)
    vol = frame.values.loc["AAA", "ewma_vol_60d"]

    returns = np.diff(np.log(closes))
    alpha = 2.0 / (60.0 + 1.0)
    variance = returns[0] ** 2
    for r in returns[1:]:
        variance = (1.0 - alpha) * variance + alpha * r**2
    expected = np.sqrt(variance * 365.0)

    assert vol == pytest.approx(expected, rel=1e-12)
    # min_periods=20 returns: the first 20 closes have fewer, so NaN there.
    short = compute_factors(tiny_snapshot({"AAA": closes[:20]}), ["ewma_vol_60d"], CONVENTION)
    assert math.isnan(short.values.loc["AAA", "ewma_vol_60d"])


def test_ewma_convention_none_or_missing_key_raises():
    snapshot = tiny_snapshot({"AAA": [1.0] * 30})
    with pytest.raises(PendingParameterError):
        compute_factors(snapshot, ["ma_20"], None)
    missing = {"kind": "span", "value": 60, "returns": "log"}
    with pytest.raises(PendingParameterError):
        compute_factors(snapshot, ["ma_20"], missing)
    with pytest.raises(PendingParameterError):
        ewma_cov(snapshot, ["AAA"], None)


def test_same_snapshot_and_convention_are_identical():
    tape = make_synthetic_tape(seed=5, n_perps=4, days=80)
    provider = MarketStateProvider(tape, universe_size=10, min_history_days=20)
    snapshot = provider.resolve(MarketStateRequest(
        expert_id="EXP-TREND-E001",
        request_version="1",
        universe_id="USDTM_TOP30_PIT",
        as_of=pd.Timestamp("2019-11-01", tz="UTC"),
        timeframes=(("1d", 60, ("close", "volume")),),
        factors=("ma_20",),
        cross_section=(),
        max_staleness=pd.Timedelta("2D"),
    ))
    names = ["ma_20", "ma_50", "close", "ewma_vol_60d"]

    first = compute_factors(snapshot, names, CONVENTION)
    second = compute_factors(snapshot, names, CONVENTION)

    assert first.snapshot_id == snapshot.snapshot_id
    pd.testing.assert_frame_equal(first.values, second.values)
    assert first.feature_code_hash == second.feature_code_hash


def test_different_convention_changes_hash():
    snapshot = tiny_snapshot({"AAA": list(np.linspace(1.0, 2.0, 30))})
    names = ["ma_20", "ewma_vol_60d"]

    span = compute_factors(snapshot, names, CONVENTION)
    halflife = compute_factors(
        snapshot,
        names,
        {"kind": "halflife", "value": 30.0, "returns": "simple", "min_periods": 5},
    )

    assert span.feature_code_hash != halflife.feature_code_hash
    # Different conventions can also change values; the hash is what identifies code+params.
    assert span.feature_code_hash == compute_factors(snapshot, names, dict(CONVENTION)).feature_code_hash


def test_ewma_cov_diagonal_equals_ewma_vol_squared():
    rng = np.random.default_rng(11)
    days = 70
    common = rng.normal(0.0, 0.02, size=days)
    closes = {
        "AAA": 100.0 * np.exp(np.cumsum(common)),
        "BBB": 50.0 * np.exp(np.cumsum(common + rng.normal(0.0, 0.01, size=days))),
    }
    snapshot = tiny_snapshot(closes)

    factors = compute_factors(snapshot, ["ewma_vol_60d"], CONVENTION)
    cov = ewma_cov(snapshot, ["AAA", "BBB"], CONVENTION)

    for iid in ("AAA", "BBB"):
        vol_squared = factors.values.loc[iid, "ewma_vol_60d"] ** 2
        assert cov.loc[iid, iid] == pytest.approx(vol_squared, rel=1e-12)
    assert cov.loc["AAA", "BBB"] == pytest.approx(cov.loc["BBB", "AAA"])
