"""T3.1 EXP-TREND-E001: hand-built score cases, ABSTAIN rules, end-to-end run."""

import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from tycho.config import load
from tycho.contracts import FactorFrame, SnapshotRef, canonical_hash
from tycho.exp_trend import TrendExpert
from tycho.factors import compute_factors
from tycho.marketstate import MarketStateProvider
from tycho.runtime import run_expert
from tycho.synthetic import make_synthetic_tape

UNIVERSE = "USDTM_TOP30_PIT"
AS_OF = pd.Timestamp("2020-03-01", tz="UTC")
# Test-only values: ewma_convention is PENDING in the monograph.
CONVENTION = {"kind": "span", "value": 60, "returns": "log", "min_periods": 20}
MAX_STALENESS = pd.Timedelta("2D")
MA_PERIODS = load()["signal"]["ma_periods_days"]
MA_NAMES = [f"ma_{k}" for k in MA_PERIODS]


def make_snapshot(closes, missing=(), stale=(), delisted=(), snapshot_id="snap-1"):
    rows = []
    for iid, close in closes.items():
        rows.append(
            {
                "instrument_id": iid,
                "event_time": AS_OF - pd.Timedelta(days=1),
                "close": close,
            }
        )
    availability = pd.DataFrame(
        {
            "missing": [iid in missing for iid in closes],
            "stale": [iid in stale for iid in closes],
            "delisted": [iid in delisted for iid in closes],
        },
        index=pd.Index(list(closes), name="instrument_id"),
    )
    return SnapshotRef(
        snapshot_id=snapshot_id,
        as_of=AS_OF,
        dataset_hash="dataset",
        universe_hash="universe",
        request_hash="request",
        universe=tuple(closes),
        bars=pd.DataFrame(rows),
        availability=availability,
    )


def make_frame(closes, mas_by_id, snapshot_id="snap-1"):
    values = pd.DataFrame(
        {name: [mas_by_id[iid][name] for iid in closes] for name in MA_NAMES},
        index=pd.Index(list(closes), name="instrument_id"),
    )
    return FactorFrame(snapshot_id=snapshot_id, feature_code_hash="features", values=values)


def signal_for(expert, snapshot, frame, instrument_id):
    signals = {s.instrument_id: s for s in expert.signal(snapshot, frame)}
    return signals[instrument_id]


@pytest.fixture
def expert():
    return TrendExpert(MA_PERIODS, MAX_STALENESS)


def test_scores_one_minus_one_half_and_zero(expert):
    closes = {"UP": 110.0, "DOWN": 90.0, "HALF": 110.0, "EQUAL": 100.0}
    mas = {
        "UP": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0},
        "DOWN": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0},
        "HALF": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 120.0},
        # exact equality every period: np.sign(0) = 0, not a guess at direction
        "EQUAL": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0},
    }
    snapshot = make_snapshot(closes)
    frame = make_frame(closes, mas)

    up = signal_for(expert, snapshot, frame, "UP")
    down = signal_for(expert, snapshot, frame, "DOWN")
    half = signal_for(expert, snapshot, frame, "HALF")
    equal = signal_for(expert, snapshot, frame, "EQUAL")

    assert up.score == 1.0 and up.direction == "LONG"
    assert down.score == -1.0 and down.direction == "SHORT"
    assert half.score == 0.5 and half.direction == "LONG"
    assert equal.score == 0.0 and equal.direction == "FLAT"
    assert up.diagnostics == {"ma_20": 1, "ma_50": 1, "ma_100": 1, "ma_200": 1}
    assert equal.diagnostics == {"ma_20": 0, "ma_50": 0, "ma_100": 0, "ma_200": 0}
    assert up.effective_at == AS_OF
    assert up.valid_until == AS_OF + pd.Timedelta(days=1)
    assert up.snapshot_id == "snap-1"
    assert up.manifest_hash == expert.manifest.manifest_hash()


def test_abstain_on_nan_ma(expert):
    closes = {"AAA": 110.0, "BBB": 110.0}
    mas = {
        "AAA": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": math.nan},
        "BBB": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0},
    }
    snapshot = make_snapshot(closes)
    frame = make_frame(closes, mas)
    assert signal_for(expert, snapshot, frame, "AAA").direction == "ABSTAIN"
    assert signal_for(expert, snapshot, frame, "BBB").direction == "LONG"


def test_abstain_on_missing_stale_and_delisted(expert):
    closes = {"GOOD": 110.0, "MISS": 110.0, "STALE": 110.0, "GONE": 110.0}
    mas = {
        iid: {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0}
        for iid in closes
    }
    snapshot = make_snapshot(
        closes, missing=("MISS",), stale=("STALE",), delisted=("GONE",)
    )
    frame = make_frame(closes, mas)

    signals = {s.instrument_id: s for s in expert.signal(snapshot, frame)}
    for iid in ("MISS", "STALE", "GONE"):
        assert signals[iid].direction == "ABSTAIN"
        assert signals[iid].score is None
        assert signals[iid].abstain_reason
    assert signals["GOOD"].direction == "LONG"


def test_abstain_on_nan_close(expert):
    closes = {"AAA": 110.0}
    mas = {"AAA": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0}}
    snapshot = make_snapshot(closes)
    # universe lists the instrument but no visible bar carries a close
    snapshot = replace(snapshot, bars=snapshot.bars.iloc[0:0])
    signal = signal_for(expert, snapshot, make_frame(closes, mas), "AAA")
    assert signal.direction == "ABSTAIN"
    assert signal.abstain_reason == "nan_close"


def test_latest_nan_close_does_not_reuse_previous_signal(expert):
    closes = {"AAA": 110.0}
    mas = {"AAA": dict.fromkeys(MA_NAMES, 100.0)}
    snapshot = make_snapshot(closes)
    latest = snapshot.bars.copy()
    latest["event_time"] = AS_OF - pd.Timedelta(hours=1)
    latest["close"] = np.nan
    # Unsorted input must still select the latest actual row, including its NaN.
    snapshot = replace(snapshot, bars=pd.concat([latest, snapshot.bars], ignore_index=True))
    signal = signal_for(expert, snapshot, make_frame(closes, mas), "AAA")
    assert signal.direction == "ABSTAIN"
    assert signal.score is None
    assert signal.abstain_reason == "nan_close"


def test_one_signal_per_universe_instrument(expert):
    closes = {"AAA": 110.0, "BBB": 90.0, "CCC": 100.0}
    mas = {
        "AAA": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0},
        "BBB": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0},
        "CCC": {"ma_20": 100.0, "ma_50": 100.0, "ma_100": 100.0, "ma_200": 100.0},
    }
    signals = expert.signal(make_snapshot(closes), make_frame(closes, mas))
    assert [s.instrument_id for s in signals] == ["AAA", "BBB", "CCC"]


def test_request_matches_08a3_contract(expert):
    request = expert.request(AS_OF)
    assert request.expert_id == "EXP-TREND-E001"
    assert request.request_version == "1"
    assert request.universe_id == UNIVERSE
    assert request.as_of == AS_OF
    assert request.timeframes == (("1d", 260, ("close", "volume")),)
    assert request.factors == ("ma_20", "ma_50", "ma_100", "ma_200", "ewma_vol_60d")
    assert request.cross_section == (
        "listing_state",
        "contract_spec",
        "trailing_30d_liquidity",
    )
    assert request.max_staleness == MAX_STALENESS
    assert request.point_in_time and request.closed_bars_only
    assert request.on_missing == "ABSTAIN_AND_REPORT"


def test_manifest_params_hash_stable():
    first = TrendExpert(MA_PERIODS, MAX_STALENESS)
    second = TrendExpert(list(MA_PERIODS), MAX_STALENESS)
    other = TrendExpert(MA_PERIODS[:2], MAX_STALENESS)

    assert first.params == {"ma_periods": MA_PERIODS}
    assert first.manifest.params_hash == canonical_hash({"ma_periods": MA_PERIODS})
    assert first.manifest.params_hash == second.manifest.params_hash
    assert first.manifest.params_hash != other.manifest.params_hash
    assert first.manifest.id == "EXP-TREND-E001"
    assert first.manifest.semantic_version == "1.0-draft"
    assert first.manifest.research_status == "DRAFT"
    assert first.manifest.sleeve == "T"
    assert first.manifest.universe_contract == UNIVERSE
    assert first.manifest.decision_schedule == "1d after closed bar"


def test_end_to_end_through_run_expert():
    tape = make_synthetic_tape(seed=5, n_perps=12, days=600)
    provider = MarketStateProvider(tape, universe_size=50, min_history_days=20)
    expert = TrendExpert(MA_PERIODS, MAX_STALENESS)
    as_of = pd.Timestamp("2021-01-15 12:00", tz="UTC")

    def factor_fn(snapshot):
        return compute_factors(snapshot, ("close", *MA_NAMES, "ewma_vol_60d"), CONVENTION)

    signals = run_expert(expert, provider, as_of, factor_fn)
    snapshot = provider.resolve(expert.request(as_of))

    assert signals == run_expert(expert, provider, as_of, factor_fn)
    assert {s.instrument_id for s in signals} == set(snapshot.universe)
    assert all(s.as_of == snapshot.as_of for s in signals)
    available = [s for s in signals if s.direction != "ABSTAIN"]
    assert available  # the synthetic tape has enough history for some instruments
    for signal in available:
        assert -1.0 <= signal.score <= 1.0
        assert abs(signal.score * 4 - round(signal.score * 4)) < 1e-12
        expected = "LONG" if signal.score > 0 else "SHORT" if signal.score < 0 else "FLAT"
        assert signal.direction == expected
        assert set(signal.diagnostics) == set(MA_NAMES)
    for signal in signals:
        if signal.direction == "ABSTAIN":
            assert signal.score is None and signal.abstain_reason
