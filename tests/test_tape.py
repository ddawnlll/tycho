import copy
import json

import pandas as pd
import pytest

from tycho import config
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
