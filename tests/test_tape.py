import copy
import json

import pandas as pd
import pytest

from tycho import config
from tycho.feasibility import audit
from tycho.synthetic import make_synthetic_tape
from tycho.tape import TABLE_COLUMNS, Tape, load_tape, save_tape, validate

TABLES = list(TABLE_COLUMNS)


@pytest.fixture(scope="module")
def tape():
    return make_synthetic_tape(seed=7, n_perps=12, days=120)


def test_deterministic_same_seed(tape):
    again = make_synthetic_tape(seed=7, n_perps=12, days=120)
    for name in TABLES:
        assert getattr(tape, name).equals(getattr(again, name)), name
    assert tape.meta == again.meta == {"synthetic": True, "seed": 7}


def test_different_seed_differs(tape):
    other = make_synthetic_tape(seed=8, n_perps=12, days=120)
    assert not tape.bars_1d["close"].equals(other.bars_1d["close"])


def test_synthetic_contract_features_and_kinds(tape):
    instruments = tape.instruments
    assert "USDCUSDT" in set(instruments.loc[instruments["kind"] == "stablecoin", "instrument_id"])
    assert "BTCDOMUSDT" in set(instruments.loc[instruments["kind"] == "index", "instrument_id"])

    thousand = tape.specs[tape.specs["contract_multiplier"] == 1000.0]
    assert set(thousand["instrument_id"]) == {"1000SHIBUSDT"}

    perp_ids = set(instruments.loc[instruments["kind"] == "perp", "instrument_id"])
    perp_specs = tape.specs[tape.specs["instrument_id"].isin(perp_ids)]
    assert set(perp_specs["funding_interval_hours"]) == {8.0}


def test_synthetic_bar_revision_differs(tape):
    revisions = tape.bars_1d[tape.bars_1d["revision"] == 1]
    assert len(revisions) == 1
    revision = revisions.iloc[0]
    base = tape.bars_1d[
        (tape.bars_1d["instrument_id"] == revision["instrument_id"])
        & (tape.bars_1d["event_time"] == revision["event_time"])
        & (tape.bars_1d["revision"] == 0)
    ].iloc[0]
    assert revision["close"] != base["close"]
    assert revision["available_at"] > base["available_at"]


def test_synthetic_delisting_is_point_in_time(tape):
    delisted = tape.instruments[tape.instruments["revision"] == 1]
    assert len(delisted) == 3
    for _, row in delisted.iterrows():
        iid, delisted_at = row["instrument_id"], row["delisted_at"]
        assert not pd.isna(delisted_at)
        before = tape.as_of(delisted_at - pd.Timedelta(minutes=1)).instruments
        row_before = before[before["instrument_id"] == iid].iloc[0]
        assert pd.isna(row_before["delisted_at"])
        assert row_before["revision"] == 0
        after = tape.as_of(row["available_at"]).instruments
        row_after = after[after["instrument_id"] == iid].iloc[0]
        assert row_after["delisted_at"] == delisted_at


def test_threshold_failure_incomplete(tape):
    permissive = {
        "min_delisted_coverage": 0.0,
        "min_funding_coverage": 0.0,
        "min_mark_coverage": 0.0,
        "min_spec_coverage": 0.0,
        "max_months_below_30": 10**9,
    }
    assert audit(tape, permissive)["verdict"] == "PASS"
    report = audit(tape, dict(permissive, min_spec_coverage=1.1))
    assert report["verdict"] == "INCOMPLETE"
    assert report["reasons"] == ["threshold failed: min_spec_coverage"]


def test_validate_passes_and_catches_breakage(tape):
    validate(tape)
    missing = copy.copy(tape)
    missing.bars_1d = tape.bars_1d.drop(columns=["close"])
    with pytest.raises(ValueError, match="missing columns"):
        validate(missing)
    nat = copy.copy(tape)
    nat.funding = tape.funding.assign(available_at=pd.NaT)
    with pytest.raises(ValueError, match="NaT available_at"):
        validate(nat)
    early = copy.copy(tape)
    early.mark = tape.mark.assign(available_at=tape.mark["event_time"] - pd.Timedelta(minutes=1))
    with pytest.raises(ValueError, match="available_at before event_time"):
        validate(early)


def test_as_of_respects_available_at(tape):
    t = pd.Timestamp("2019-10-01", tz="UTC")
    snap = tape.as_of(t)
    for name in TABLES:
        assert (getattr(snap, name)["available_at"] <= t).all(), name

    bar = tape.bars_1d.iloc[0]
    before = bar["available_at"] - pd.Timedelta(seconds=1)
    assert bar["event_id"] not in set(tape.as_of(before).bars_1d["event_id"])
    assert bar["event_id"] in set(tape.as_of(bar["available_at"]).bars_1d["event_id"])


def test_as_of_revision_visibility(tape):
    rev1 = tape.bars_1d[tape.bars_1d["revision"] == 1].iloc[0]
    rev0 = tape.bars_1d[
        (tape.bars_1d["instrument_id"] == rev1["instrument_id"])
        & (tape.bars_1d["event_time"] == rev1["event_time"])
        & (tape.bars_1d["revision"] == 0)
    ].iloc[0]
    t0 = rev0["available_at"] + pd.Timedelta(minutes=1)
    t1 = rev1["available_at"] + pd.Timedelta(minutes=1)

    before = tape.as_of(t0).bars_1d
    same_key = (before["instrument_id"] == rev1["instrument_id"]) & (
        before["event_time"] == rev1["event_time"]
    )
    assert before.loc[same_key, "revision"].tolist() == [0]
    assert before.loc[same_key, "close"].tolist() == [rev0["close"]]

    after = tape.as_of(t1).bars_1d
    same_key = (after["instrument_id"] == rev1["instrument_id"]) & (
        after["event_time"] == rev1["event_time"]
    )
    assert after.loc[same_key, "revision"].tolist() == [1]
    assert after.loc[same_key, "close"].tolist() == [rev1["close"]]

    # taking a later view must not change what an earlier as_of returns
    assert tape.as_of(t0).bars_1d.equals(before)


def test_future_rows_do_not_leak(tape):
    t = pd.Timestamp("2019-10-15", tz="UTC")
    baseline = tape.as_of(t)

    future = tape.bars_1d.iloc[[-1]].copy()
    future["event_id"] = "bars_1d:FUTURE:1"
    future["event_time"] = pd.Timestamp("2030-01-01", tz="UTC")
    future["available_at"] = pd.Timestamp("2030-01-01 00:01", tz="UTC")
    future["revision"] = 0
    injected = Tape(
        instruments=tape.instruments,
        specs=tape.specs,
        bars_1d=pd.concat([tape.bars_1d, future], ignore_index=True),
        funding=tape.funding,
        mark=tape.mark,
        meta=tape.meta,
    )
    after = injected.as_of(t)
    for name in TABLES:
        assert getattr(after, name).equals(getattr(baseline, name)), name


def test_save_load_roundtrip(tape, tmp_path):
    save_tape(tape, tmp_path / "t")
    loaded = load_tape(tmp_path / "t", config.load())
    assert loaded.meta == tape.meta
    for name in TABLES:
        assert getattr(loaded, name).equals(getattr(tape, name)), name


def test_load_refuses_holdout_path(tape, tmp_path):
    with pytest.raises(ValueError, match="holdout"):
        load_tape(tmp_path / "holdout" / "t", config.load())


def test_load_non_synthetic_manifest_requires_dev_end(tape, tmp_path):
    save_tape(tape, tmp_path / "t")
    manifest = json.loads((tmp_path / "t" / "manifest.json").read_text())
    manifest["synthetic"] = False
    (tmp_path / "t" / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(config.PendingParameterError, match="dev_end_date"):
        load_tape(tmp_path / "t", config.load())


def test_load_clamps_to_dev_end(tape, tmp_path):
    save_tape(tape, tmp_path / "t")
    manifest = json.loads((tmp_path / "t" / "manifest.json").read_text())
    manifest["synthetic"] = False
    (tmp_path / "t" / "manifest.json").write_text(json.dumps(manifest))

    cfg = copy.deepcopy(config.load())
    cfg["periods"]["dev_end_date"] = "2030-01-01"
    load_tape(tmp_path / "t", cfg)

    cfg["periods"]["dev_end_date"] = "2019-10-01"
    with pytest.raises(ValueError, match="dev_end_date"):
        load_tape(tmp_path / "t", cfg)
