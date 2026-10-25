import pandas as pd
import pytest

from tycho.contracts import (
    ExpertManifest,
    ExpertSignal,
    FactorFrame,
    MarketStateRequest,
)
from tycho.factors import compute_factors
from tycho.marketstate import MarketStateProvider
from tycho.synthetic import make_synthetic_tape

UNIVERSE = "USDTM_TOP30_PIT"
# test convention only: the monograph value is PENDING
CONVENTION = {"kind": "span", "value": 60, "returns": "log", "min_periods": 20}


@pytest.fixture
def tape():
    return make_synthetic_tape(seed=7, n_perps=12, days=120)


@pytest.fixture
def provider(tape):
    return MarketStateProvider(tape, universe_size=50, min_history_days=20)


def request(as_of, lookback=60, max_staleness="2D", **overrides):
    fields = dict(
        expert_id="EXP-TREND-E001",
        request_version="1",
        universe_id=UNIVERSE,
        as_of=pd.Timestamp(as_of),
        timeframes=(("1d", lookback, ("close", "volume")),),
        factors=("ma_20", "ma_50"),
        cross_section=("listing_state",),
        max_staleness=pd.Timedelta(max_staleness),
    )
    fields.update(overrides)
    return MarketStateRequest(**fields)


def _bar(tape, instrument, day):
    rows = tape.bars_1d[
        (tape.bars_1d["instrument_id"] == instrument)
        & (tape.bars_1d["event_time"] == pd.Timestamp(day, tz="UTC"))
    ]
    return rows.iloc[0]


def _inject_revision(tape, instrument="BTCUSDT", day="2019-11-05", available="2019-11-10 18:00"):
    base = _bar(tape, instrument, day)
    revision = tape.bars_1d[
        (tape.bars_1d["instrument_id"] == instrument)
        & (tape.bars_1d["event_time"] == base["event_time"])
    ].iloc[[0]].copy()
    revision["revision"] = 1
    revision["close"] = base["close"] * 0.5
    revision["event_id"] = base["event_id"] + ":1"
    revision["available_at"] = pd.Timestamp(available, tz="UTC")
    revision["received_at"] = revision["available_at"]
    tape.bars_1d = pd.concat([tape.bars_1d, revision], ignore_index=True)
    return revision.iloc[0]


def _inject_future_rows(tape):
    future = pd.Timestamp("2030-01-01", tz="UTC")
    for name in ("bars_1d", "funding", "mark"):
        table = getattr(tape, name)
        row = table.iloc[[-1]].copy()
        row["event_id"] = f"future:{name}:0"
        row["event_time"] = future
        row["received_at"] = future
        row["available_at"] = future + pd.Timedelta(minutes=1)
        row["revision"] = 0
        setattr(tape, name, pd.concat([table, row], ignore_index=True))


def test_request_hash_is_canonical_and_as_of_independent():
    early = request("2019-11-10")
    late = request("2019-12-20")
    assert early.request_hash() == late.request_hash()
    assert early.request_hash() == request("2019-11-10").request_hash()
    assert early.request_hash() != request("2019-11-10", expert_id="EXP-OTHER").request_hash()
    assert early.request_hash() != request("2019-11-10", universe_id="OTHER").request_hash()
    assert early.request_hash() != request("2019-11-10", max_staleness="3D").request_hash()


def test_max_staleness_is_required():
    with pytest.raises(TypeError):
        MarketStateRequest(
            expert_id="EXP-TREND-E001",
            request_version="1",
            universe_id=UNIVERSE,
            as_of=pd.Timestamp("2019-11-10", tz="UTC"),
            timeframes=(("1d", 60, ("close",)),),
            factors=(),
            cross_section=(),
        )


def test_unknown_universe_raises(provider):
    with pytest.raises(ValueError, match="unknown universe_id"):
        provider.resolve(request("2019-11-10", universe_id="NOT_A_UNIVERSE"))


def test_universe_excludes_stablecoin_index_and_short_history(tape):
    small = MarketStateProvider(tape, universe_size=50, min_history_days=40)
    universe = small.resolve(request("2019-11-10")).universe
    assert set(universe) == {"BTCUSDT", "LTCUSDT"}
    assert "USDCUSDT" not in universe and "BTCDOMUSDT" not in universe

    # LINKUSDT is a real perp but has only 25 bars before the November month start
    wide = MarketStateProvider(tape, universe_size=50, min_history_days=20)
    wide_universe = wide.resolve(request("2019-11-10")).universe
    assert "LINKUSDT" in wide_universe
    assert "LINKUSDT" not in universe


def test_universe_membership_is_decided_at_the_month_start(tape):
    provider = MarketStateProvider(tape, universe_size=50, min_history_days=10)
    november = provider.resolve(request("2019-11-15")).universe
    # a perp listed mid-month must not appear before the next month start
    assert "ETHUSDT" not in november  # listed 2019-11-14
    december = provider.resolve(request("2019-12-05")).universe
    assert "ETHUSDT" in december


def test_survivorship_and_delisted_flag(provider):
    universe = provider.resolve(request("2019-11-10")).universe
    assert "LTCUSDT" in universe  # delisted 2019-11-04: still a member
    assert "BCHUSDT" in universe  # delisted 2019-11-17: still a member

    before = provider.resolve(request("2019-11-02"))
    after = provider.resolve(request("2019-11-10"))
    assert not bool(before.availability.loc["LTCUSDT", "delisted"])
    assert bool(after.availability.loc["LTCUSDT", "delisted"])
    assert not bool(after.availability.loc["BTCUSDT", "delisted"])


def test_bar_invisible_before_available_at(provider):
    bar = _bar(provider.tape, "BTCUSDT", "2019-11-09")
    before = bar["available_at"] - pd.Timedelta(seconds=1)
    assert bar["event_id"] not in set(provider.resolve(request(before)).bars["event_id"])
    at = provider.resolve(request(bar["available_at"]))
    assert bar["event_id"] in set(at.bars["event_id"])


def test_revision_visible_only_after_its_available_at(provider, tape):
    base = _bar(tape, "BTCUSDT", "2019-11-05")
    t0 = pd.Timestamp("2019-11-10 12:00", tz="UTC")
    snapshot = provider.resolve(request(t0))
    row = snapshot.bars[
        (snapshot.bars["instrument_id"] == "BTCUSDT")
        & (snapshot.bars["event_time"] == base["event_time"])
    ]
    assert row["revision"].tolist() == [0]
    assert row["close"].tolist() == [base["close"]]

    revision = _inject_revision(tape)
    # the cached object is untouched by the later revision
    assert provider.resolve(request(t0)) is snapshot
    provider._cache.clear()
    assert provider.resolve(request(t0)).bars.equals(snapshot.bars)

    after = provider.resolve(request(revision["available_at"]))
    row = after.bars[
        (after.bars["instrument_id"] == "BTCUSDT")
        & (after.bars["event_time"] == base["event_time"])
    ]
    assert row["revision"].tolist() == [1]
    assert row["close"].tolist() == [revision["close"]]


def test_future_tape_rows_leave_earlier_snapshot_identical(provider, tape):
    t0 = pd.Timestamp("2019-11-10 12:00", tz="UTC")
    baseline = provider.resolve(request(t0))

    _inject_future_rows(tape)
    _inject_revision(tape, available="2019-11-10 18:00")
    provider._cache.clear()

    again = provider.resolve(request(t0))
    assert again.snapshot_id == baseline.snapshot_id
    assert again.frame().equals(baseline.frame())
    assert again.availability.equals(baseline.availability)


def test_missing_and_stale_flags(provider):
    snapshot = provider.resolve(request("2019-11-10 12:00", lookback=70))
    availability = snapshot.availability
    assert availability.loc["BTCUSDT", "n_bars"] == 70
    assert not bool(availability.loc["BTCUSDT", "missing"])
    assert not bool(availability.loc["BTCUSDT", "stale"])  # 1.5 days old
    assert bool(availability.loc["LTCUSDT", "missing"])  # delisted, 57 bars
    assert bool(availability.loc["LTCUSDT", "stale"])  # last bar 2019-11-03
    assert availability.loc["BTCUSDT", "quality_flags"] == ()

    tight = provider.resolve(request("2019-11-10 12:00", lookback=70, max_staleness="6h"))
    assert bool(tight.availability.loc["BTCUSDT", "stale"])
    assert bool(tight.availability.loc["BTCUSDT", "missing"]) is False


def test_repeated_resolve_returns_the_same_object(provider):
    first = provider.resolve(request("2019-11-10"))
    assert provider.resolve(request("2019-11-10")) is first
    assert provider.resolve(request("2019-11-11")) is not first


def test_frame_returns_a_copy(provider):
    snapshot = provider.resolve(request("2019-11-10"))
    frame = snapshot.frame()
    frame["close"] = -1.0
    assert not (snapshot.bars["close"] == -1.0).any()
    assert not (snapshot.frame()["close"] == -1.0).any()

    factors = FactorFrame(snapshot.snapshot_id, "code", pd.DataFrame({"ma_20": [1.0]}))
    values = factors.frame()
    values.iloc[0, 0] = 9.0
    assert factors.values.iloc[0, 0] == 1.0


def test_snapshot_id_tracks_bars_content(provider):
    base = provider.resolve(request("2019-11-10", lookback=60))
    other = provider.resolve(request("2019-11-10", lookback=61))
    assert base.snapshot_id != other.snapshot_id
    assert base.request_hash != other.request_hash


def _manifest(**overrides):
    fields = dict(
        id="EXP-TREND-E001",
        semantic_version="1.0",
        code_hash="code",
        params_hash="params",
        owner="research",
        sleeve="T",
        research_status="DRAFT",
        request_schema_version="1",
        universe_contract=UNIVERSE,
        decision_schedule="daily_close",
        output_schema_version="1",
        pre_registration_id="TYCHO-E-001",
        evidence_ids=("E-0001",),
        requires=("bars_1d", "funding"),
        portfolio_budget_class="T",
    )
    fields.update(overrides)
    return ExpertManifest(**fields)


def test_manifest_hash_and_status_validation():
    manifest = _manifest()
    assert manifest.manifest_hash() == _manifest().manifest_hash()
    assert manifest.manifest_hash() != _manifest(params_hash="other").manifest_hash()
    with pytest.raises(ValueError, match="research_status"):
        _manifest(research_status="LIVE")


def _signal(**overrides):
    ts = pd.Timestamp("2019-11-10", tz="UTC")
    fields = dict(
        expert_id="EXP-TREND-E001",
        manifest_hash="manifest",
        snapshot_id="snapshot",
        as_of=ts,
        effective_at=ts,
        valid_until=ts + pd.Timedelta(days=1),
        instrument_id="BTCUSDT",
        score=0.5,
        direction="LONG",
        abstain_reason=None,
        diagnostics={},
        source_evidence_hash="evidence",
    )
    fields.update(overrides)
    return ExpertSignal(**fields)


def test_expert_signal_validation():
    assert _signal().direction == "LONG"
    _signal(score=-0.3, direction="SHORT")
    _signal(score=0.0, direction="FLAT")
    _signal(score=None, direction="ABSTAIN", abstain_reason="missing_bars")
    with pytest.raises(ValueError, match="ABSTAIN"):
        _signal(score=0.2, direction="ABSTAIN", abstain_reason="missing_bars")
    with pytest.raises(ValueError, match="ABSTAIN"):
        _signal(score=None, direction="ABSTAIN")
    with pytest.raises(ValueError, match="outside"):
        _signal(score=1.5)
    with pytest.raises(ValueError, match="LONG"):
        _signal(score=-0.5)
    with pytest.raises(ValueError, match="SHORT"):
        _signal(score=0.5, direction="SHORT")
    with pytest.raises(ValueError, match="FLAT"):
        _signal(score=0.1, direction="FLAT")
    with pytest.raises(ValueError, match="requires a score"):
        _signal(score=None, direction="LONG")
    with pytest.raises(ValueError, match="direction"):
        _signal(direction="MAYBE")


def test_dataset_hash_uses_only_as_of_visible_rows(tape):
    t0 = pd.Timestamp("2019-11-10 12:00", tz="UTC")
    baseline = MarketStateProvider(tape, universe_size=50, min_history_days=20).resolve(
        request(t0)
    )

    _inject_future_rows(tape)
    later = MarketStateProvider(tape, universe_size=50, min_history_days=20)
    again = later.resolve(request(t0))
    assert again.snapshot_id == baseline.snapshot_id
    assert again.dataset_hash == baseline.dataset_hash
    assert again.frame().equals(baseline.frame())


def test_late_revision_visible_at_as_of_busts_the_snapshot_cache(provider, tape):
    t0 = pd.Timestamp("2019-11-10 12:00", tz="UTC")
    baseline = provider.resolve(request(t0))

    # arrived after the tape was loaded, but its available_at is before as_of
    revision = _inject_revision(tape, available="2019-11-10 11:00")
    after = provider.resolve(request(t0))

    assert after.snapshot_id != baseline.snapshot_id
    row = after.bars[
        (after.bars["instrument_id"] == "BTCUSDT")
        & (after.bars["event_time"] == revision["event_time"])
    ]
    assert row["revision"].tolist() == [1]
    assert row["close"].tolist() == [revision["close"]]


def test_closed_bars_only_excludes_a_bar_stamped_at_as_of(provider, tape):
    as_of = pd.Timestamp("2019-11-10 12:00", tz="UTC")
    row = tape.bars_1d[
        (tape.bars_1d["instrument_id"] == "BTCUSDT")
        & (tape.bars_1d["event_time"] == pd.Timestamp("2019-11-09", tz="UTC"))
    ].iloc[[0]].copy()
    row["event_id"] = "bars_1d:BTCUSDT:stamped-at-as-of"
    row["event_time"] = as_of
    row["received_at"] = as_of
    row["available_at"] = as_of
    tape.bars_1d = pd.concat([tape.bars_1d, row], ignore_index=True)

    snapshot = provider.resolve(request(as_of))
    assert "bars_1d:BTCUSDT:stamped-at-as-of" not in set(snapshot.bars["event_id"])
    # one minute later the very same bar is a closed bar and enters the frame
    later = provider.resolve(request(as_of + pd.Timedelta(minutes=1)))
    assert "bars_1d:BTCUSDT:stamped-at-as-of" in set(later.bars["event_id"])


def test_stablecoin_with_bars_and_volume_stays_out_of_the_universe(tape):
    start = pd.Timestamp("2019-09-01", tz="UTC")
    template = tape.bars_1d.iloc[[0]].copy()
    rows = []
    for offset in range(90):
        stamp = start + pd.Timedelta(days=offset)
        row = template.copy()
        row["event_id"] = f"bars_1d:USDCUSDT:{stamp.date()}"
        row["event_time"] = stamp
        row["received_at"] = stamp + pd.Timedelta(minutes=1)
        row["available_at"] = stamp + pd.Timedelta(minutes=1)
        row["instrument_id"] = "USDCUSDT"
        row["volume"] = 1e9
        row["quote_volume"] = 1e9  # would rank first if the kind filter were gone
        rows.append(row)
    tape.bars_1d = pd.concat([tape.bars_1d, *rows], ignore_index=True)

    provider = MarketStateProvider(tape, universe_size=50, min_history_days=20)
    universe = provider.resolve(request("2019-11-10")).universe
    assert "USDCUSDT" not in universe
    assert "BTCDOMUSDT" not in universe
    assert "BTCUSDT" in universe


def test_empty_universe_raises(tape):
    provider = MarketStateProvider(tape, universe_size=0, min_history_days=20)
    with pytest.raises(ValueError, match="empty universe"):
        provider.resolve(request("2019-11-10"))


def test_factor_cache_uses_the_five_element_key(provider):
    snapshot = provider.resolve(request("2019-11-10"))
    names = ("ma_20", "ewma_vol_60d")

    first = provider.factors(snapshot, names, CONVENTION)
    assert provider.factors(snapshot, names, CONVENTION) is first
    assert first.snapshot_id == snapshot.snapshot_id
    pd.testing.assert_frame_equal(
        first.values, compute_factors(snapshot, names, CONVENTION).values
    )

    key = (
        snapshot.dataset_hash,
        snapshot.as_of,
        snapshot.universe_hash,
        snapshot.request_hash,
        first.feature_code_hash,
    )
    assert provider._factor_cache[key] is first
    # the snapshot cache keeps its own key, with the feature slot still empty
    assert (snapshot.dataset_hash, snapshot.as_of, snapshot.universe_hash,
            snapshot.request_hash, "") in provider._cache

    other = {"kind": "halflife", "value": 30.0, "returns": "simple", "min_periods": 5}
    second = provider.factors(snapshot, names, other)
    assert second is not first
    assert second.feature_code_hash != first.feature_code_hash
    assert len(provider._factor_cache) == 2
