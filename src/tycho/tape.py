"""Market tape: schema, point-in-time view and DEV-clamped parquet loader."""

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import config

COMMON_COLUMNS = (
    "event_id",
    "event_time",
    "received_at",
    "received_at_is_observed",
    "available_at",
    "source",
    "revision",
    "quality",
)
INSTRUMENT_COLUMNS = COMMON_COLUMNS + (
    "instrument_id",
    "symbol",
    "kind",
    "listed_at",
    "delisted_at",
    "last_trade_day",
)
SPEC_COLUMNS = COMMON_COLUMNS + (
    "instrument_id",
    "instrument_spec_version",
    "valid_from",
    "tick_size",
    "step_size",
    "min_notional",
    "contract_multiplier",
    "funding_interval_hours",
)
BAR_COLUMNS = COMMON_COLUMNS + (
    "instrument_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
)
FUNDING_COLUMNS = COMMON_COLUMNS + ("instrument_id", "rate")
MARK_COLUMNS = COMMON_COLUMNS + ("instrument_id", "mark_price")

TABLE_COLUMNS = {
    "instruments": INSTRUMENT_COLUMNS,
    "specs": SPEC_COLUMNS,
    "bars_1d": BAR_COLUMNS,
    "funding": FUNDING_COLUMNS,
    "mark": MARK_COLUMNS,
}


@dataclass
class Tape:
    instruments: pd.DataFrame
    specs: pd.DataFrame
    bars_1d: pd.DataFrame
    funding: pd.DataFrame
    mark: pd.DataFrame
    meta: dict = field(default_factory=dict)

    def as_of(self, t) -> "Tape":
        ts = _utc(t)
        return Tape(
            **{name: _visible(getattr(self, name), ts) for name in TABLE_COLUMNS},
            meta=dict(self.meta),
        )


def _utc(t) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts


def _visible(df: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    vis = df[df["available_at"] <= t]
    if vis.empty:
        return vis.reset_index(drop=True)
    # revisions never delete history: at t only the newest visible revision wins
    keep = vis.groupby(["instrument_id", "event_time"], sort=False)["revision"].idxmax()
    return vis.loc[keep.sort_values()].reset_index(drop=True)


def validate(tape: Tape) -> None:
    for name, columns in TABLE_COLUMNS.items():
        df = getattr(tape, name)
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise ValueError(f"{name}: missing columns {missing}")
        if df["available_at"].isna().any():
            raise ValueError(f"{name}: NaT available_at")
        if (df["available_at"] < df["event_time"]).any():
            raise ValueError(f"{name}: available_at before event_time")


def load_tape(path, cfg) -> Tape:
    root = Path(path)
    if "holdout" in root.parts or "holdout" in root.resolve().parts:
        raise ValueError(f"holdout data refused: {root}")
    manifest = json.loads((root / "manifest.json").read_text())
    dev_end = None
    if not manifest.get("synthetic", False):
        dev_end = _utc(config.require(cfg["periods"]["dev_end_date"], "dev_end_date"))
    tape = Tape(
        **{name: pd.read_parquet(root / f"{name}.parquet") for name in TABLE_COLUMNS},
        meta=manifest,
    )
    if dev_end is not None:
        for name in TABLE_COLUMNS:
            if (getattr(tape, name)["event_time"] >= dev_end).any():
                raise ValueError(f"{name}: event_time at/after dev_end_date {dev_end}")
    validate(tape)
    return tape


def save_tape(tape: Tape, path) -> None:
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    for name in TABLE_COLUMNS:
        getattr(tape, name).to_parquet(root / f"{name}.parquet", index=False)
    (root / "manifest.json").write_text(json.dumps(tape.meta, indent=2, sort_keys=True))
