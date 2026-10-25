"""C-03 expert runtime: purity, isolation and ABSTAIN enforcement (08A.3-08A.8)."""

import hashlib
import json

import pandas as pd
import pytest

from tycho.contracts import (
    ExpertSignal,
    FactorFrame,
    MarketStateRequest,
    SnapshotRef,
    canonical_hash,
)
from tycho.factors import compute_factors
from tycho.marketstate import MarketStateProvider
from tycho.runtime import make_manifest, run_expert
from tycho.synthetic import make_synthetic_tape

UNIVERSE = "USDTM_TOP30_PIT"
AS_OF = pd.Timestamp("2019-11-10 12:00", tz="UTC")
PARAMS = {"lookback": 70, "threshold": 0.0}
SOURCE = "def signal(snapshot, factor_frame):\n    ...\n"
# test convention only: the monograph value is PENDING
CONVENTION = {"kind": "span", "value": 60, "returns": "log", "min_periods": 20}
FACTORS = ("ma_20", "ma_50")


@pytest.fixture
def provider():
    tape = make_synthetic_tape(seed=7, n_perps=12, days=120)
    return MarketStateProvider(tape, universe_size=50, min_history_days=20)


def _manifest():
    return make_manifest(
        SOURCE,
        PARAMS,
        id="EXP-TREND-E001",
        semantic_version="1.0",
        owner="research",
        sleeve="T",
        research_status="DRAFT",
        request_schema_version="1",
        universe_contract=UNIVERSE,
        decision_schedule="daily_close",
        output_schema_version="1",
        pre_registration_id="TYCHO-E-001",
        evidence_ids=("E-0001",),
        requires=("bars_1d",),
        portfolio_budget_class="T",
    )


def trivial_factor_fn(snapshot):
    return FactorFrame(
        snapshot_id=snapshot.snapshot_id,
        feature_code_hash="stub",
        values=snapshot.bars.groupby("instrument_id")["close"].last().to_frame("last_close"),
    )


def _signal(snapshot, manifest, instrument_id, direction, score, reason=None):
    return ExpertSignal(
        expert_id=manifest.id,
        manifest_hash=manifest.manifest_hash(),
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        effective_at=snapshot.as_of,
        valid_until=snapshot.as_of + pd.Timedelta(days=1),
        instrument_id=instrument_id,
        score=score,
        direction=direction,
        abstain_reason=reason,
        diagnostics={},
        source_evidence_hash="synthetic",
    )


def _abstain(snapshot, manifest, instrument_id):
    return _signal(snapshot, manifest, instrument_id, "ABSTAIN", None, "missing_bars")


def _trend(snapshot, manifest):
    """LONG when the last close is above the first, SHORT below, FLAT on a tie."""
    out = []
    for instrument_id in snapshot.universe:
        row = snapshot.availability.loc[instrument_id]
        if bool(row["missing"] or row["stale"] or row["delisted"]):
            out.append(_abstain(snapshot, manifest, instrument_id))
            continue
        closes = snapshot.bars.loc[
            snapshot.bars["instrument_id"] == instrument_id, "close"
        ]
        delta = float(closes.iloc[-1] - closes.iloc[0])
        if delta > 0:
            out.append(_signal(snapshot, manifest, instrument_id, "LONG", 0.5))
        elif delta < 0:
            out.append(_signal(snapshot, manifest, instrument_id, "SHORT", -0.5))
        else:
            out.append(_signal(snapshot, manifest, instrument_id, "FLAT", 0.0))
    return out


class DummyExpert:
    def __init__(self, manifest, signal_impl=None):
        self.manifest = manifest
        self.params = dict(PARAMS)
        self._signal_impl = signal_impl or _trend

    def request(self, as_of):
        return MarketStateRequest(
            expert_id=self.manifest.id,
            request_version="1",
            universe_id=UNIVERSE,
            as_of=pd.Timestamp(as_of),
            timeframes=(("1d", 70, ("close",)),),
            factors=(),
            cross_section=(),
            max_staleness=pd.Timedelta("2D"),
        )

    def signal(self, snapshot, factor_frame):
        return self._signal_impl(snapshot, self.manifest)


def test_manifest_hashes_source_and_canonical_params():
    manifest = _manifest()
    assert manifest.code_hash == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert manifest.params_hash == hashlib.sha256(
        json.dumps(PARAMS, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert manifest.params_hash == canonical_hash(PARAMS)


def test_same_as_of_twice_gives_identical_signals(provider):
    first = run_expert(DummyExpert(_manifest()), provider, AS_OF, trivial_factor_fn)
    second = run_expert(DummyExpert(_manifest()), provider, AS_OF, trivial_factor_fn)
    assert first == second


def test_params_changed_after_hashing_raises(provider):
    expert = DummyExpert(_manifest())
    expert.params["lookback"] = 30
    with pytest.raises(ValueError, match="params changed"):
        run_expert(expert, provider, AS_OF, trivial_factor_fn)


def test_signal_for_non_universe_instrument_raises(provider):
    def impl(snapshot, manifest):
        return _trend(snapshot, manifest) + [
            _signal(snapshot, manifest, "NOTINUNIVERSE", "LONG", 0.5)
        ]

    with pytest.raises(ValueError, match="outside universe"):
        run_expert(DummyExpert(_manifest(), impl), provider, AS_OF, trivial_factor_fn)


def test_non_abstain_on_missing_or_stale_instrument_raises(provider):
    snapshot = provider.resolve(DummyExpert(_manifest()).request(AS_OF))
    unavailable = [
        iid
        for iid in snapshot.universe
        if bool(snapshot.availability.loc[iid, ["missing", "stale"]].any())
    ]
    assert unavailable  # the synthetic tape has gap and delisted instruments here

    def impl(snap, manifest):
        return [
            _signal(snap, manifest, iid, "LONG", 0.5)
            if iid in unavailable
            else _abstain(snap, manifest, iid)
            for iid in snap.universe
        ]

    with pytest.raises(ValueError, match="non-ABSTAIN"):
        run_expert(DummyExpert(_manifest(), impl), provider, AS_OF, trivial_factor_fn)


def test_missing_instrument_abstain_passes(provider):
    def impl(snapshot, manifest):
        out = []
        for iid in snapshot.universe:
            row = snapshot.availability.loc[iid]
            if bool(row["missing"] or row["stale"] or row["delisted"]):
                out.append(_abstain(snapshot, manifest, iid))
            else:
                out.append(_signal(snapshot, manifest, iid, "LONG", 0.5))
        return out

    signals = run_expert(DummyExpert(_manifest(), impl), provider, AS_OF, trivial_factor_fn)
    snapshot = provider.resolve(DummyExpert(_manifest()).request(AS_OF))
    assert {s.instrument_id for s in signals} == set(snapshot.universe)
    assert any(s.direction == "ABSTAIN" for s in signals)


def test_signal_missing_for_a_universe_instrument_raises(provider):
    def impl(snapshot, manifest):
        return _trend(snapshot, manifest)[:-1]

    with pytest.raises(ValueError, match="no signal for universe"):
        run_expert(DummyExpert(_manifest(), impl), provider, AS_OF, trivial_factor_fn)


def test_expert_receives_only_snapshot_and_factor_frame(provider):
    seen = []

    class SpyExpert(DummyExpert):
        def signal(self, snapshot, factor_frame):
            seen.append((type(snapshot), type(factor_frame)))
            return _trend(snapshot, self.manifest)

    signals = run_expert(SpyExpert(_manifest()), provider, AS_OF, trivial_factor_fn)
    assert seen == [(SnapshotRef, FactorFrame)]
    assert signals


def test_malicious_expert_cannot_poison_the_cached_snapshot(provider):
    fresh = MarketStateProvider(provider.tape, universe_size=50, min_history_days=20)
    expert_request = DummyExpert(_manifest()).request(AS_OF)
    cached = fresh.resolve(expert_request)
    original_bars = cached.frame()
    original_availability = cached.availability.copy()
    assert [
        iid
        for iid in cached.universe
        if bool(cached.availability.loc[iid, ["missing", "stale"]].any())
    ]

    class MaliciousExpert(DummyExpert):
        def signal(self, snapshot, factor_frame):
            # flip the flags and blank the bars the expert was handed
            snapshot.availability.loc[:, ["missing", "stale", "delisted"]] = False
            snapshot.bars.loc[:, "close"] = -1.0
            return [
                _signal(snapshot, self.manifest, iid, "LONG", 0.5)
                for iid in snapshot.universe
            ]

    # enforcement reads the pre-call availability, not the expert's edits
    with pytest.raises(ValueError, match="non-ABSTAIN"):
        run_expert(MaliciousExpert(_manifest()), fresh, AS_OF, trivial_factor_fn)

    later = fresh.resolve(expert_request)
    assert later is cached
    assert later.frame().equals(original_bars)
    assert later.availability.equals(original_availability)
    assert not (later.bars["close"] == -1.0).any()


def _factor_expert(impl):
    class FactorExpert(DummyExpert):
        def signal(self, snapshot, factor_frame):
            return impl(snapshot, factor_frame, self.manifest)

    return FactorExpert


def _factor_trend(snapshot, factor_frame, manifest):
    """Direction from the frame's MA values; ABSTAIN wherever the data is bad."""
    out = []
    for iid in snapshot.universe:
        row = snapshot.availability.loc[iid]
        if bool(
            row["missing"] or row["stale"] or row["delisted"] or row["bad_quality"]
        ):
            out.append(_abstain(snapshot, manifest, iid))
            continue
        values = factor_frame.values.loc[iid]
        delta = float(values["ma_50"] - values["ma_20"])
        if delta > 0:
            out.append(_signal(snapshot, manifest, iid, "LONG", 0.5))
        elif delta < 0:
            out.append(_signal(snapshot, manifest, iid, "SHORT", -0.5))
        else:
            out.append(_signal(snapshot, manifest, iid, "FLAT", 0.0))
    return out


def test_hostile_factor_frame_write_does_not_poison_a_later_run(provider):
    frames = {}

    def factor_fn(snapshot):
        # a caller-level cache that would leak without run_expert's copy
        return frames.setdefault(
            snapshot.snapshot_id, compute_factors(snapshot, FACTORS, CONVENTION)
        )

    expert = _factor_expert(_factor_trend)
    baseline = run_expert(expert(_manifest()), provider, AS_OF, factor_fn)
    abstained = [s for s in baseline if s.direction == "ABSTAIN"]
    assert abstained  # the synthetic tape must exercise the ABSTAIN path

    class HostileExpert(DummyExpert):
        def signal(self, snapshot, factor_frame):
            factor_frame.values.iloc[:, :] = 1e9
            return _factor_trend(snapshot, factor_frame, self.manifest)

    run_expert(HostileExpert(_manifest()), provider, AS_OF, factor_fn)

    after = run_expert(expert(_manifest()), provider, AS_OF, factor_fn)
    assert after == baseline
    assert [s.instrument_id for s in after if s.direction == "ABSTAIN"] == [
        s.instrument_id for s in abstained
    ]


def test_bad_quality_bar_requires_abstain(provider):
    tape = provider.tape
    bar = tape.bars_1d[
        (tape.bars_1d["instrument_id"] == "BTCUSDT")
        & (tape.bars_1d["event_time"] == pd.Timestamp("2019-11-09", tz="UTC"))
    ].iloc[0]
    tape.bars_1d.loc[bar.name, "quality"] = "gap"

    snapshot = provider.resolve(DummyExpert(_manifest()).request(AS_OF))
    assert bool(snapshot.availability.loc["BTCUSDT", "bad_quality"])

    def impl(snap, manifest):
        return [
            _signal(snap, manifest, iid, "LONG", 0.5)
            if iid == "BTCUSDT"
            else _abstain(snap, manifest, iid)
            for iid in snap.universe
        ]

    with pytest.raises(ValueError, match="non-ABSTAIN"):
        run_expert(DummyExpert(_manifest(), impl), provider, AS_OF, trivial_factor_fn)

    def abstaining(snap, manifest):
        return [_abstain(snap, manifest, iid) for iid in snap.universe]

    signals = run_expert(
        DummyExpert(_manifest(), abstaining), provider, AS_OF, trivial_factor_fn
    )
    assert all(s.direction == "ABSTAIN" for s in signals)
