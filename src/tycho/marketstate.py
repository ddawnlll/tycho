"""C-01 MarketState provider: PIT universe, immutable snapshot, shared cache (08A)."""

import pandas as pd

from .contracts import SnapshotRef, canonical_hash, frame_hash, snapshot_id_for
from .tape import TABLE_COLUMNS

UNIVERSE_ID = "USDTM_TOP30_PIT"
_RANK_WINDOW = pd.Timedelta(days=30)
_AVAILABILITY_COLUMNS = [
    "last_bar_time",
    "last_available_at",
    "n_bars",
    "missing",
    "stale",
    "delisted",
    "quality_flags",
]


class MarketStateProvider:
    def __init__(self, tape, universe_size, min_history_days):
        self.tape = tape
        self.universe_size = int(universe_size)
        self.min_history_days = int(min_history_days)
        # Computed once on purpose: rows appended later cannot move the hash of
        # an already-taken snapshot, and the hash is no longer recomputed.
        self.dataset_hash = canonical_hash(
            {name: frame_hash(getattr(tape, name)) for name in TABLE_COLUMNS}
        )
        self._cache = {}

    def resolve(self, request) -> SnapshotRef:
        if request.universe_id != UNIVERSE_ID:
            raise ValueError(f"unknown universe_id: {request.universe_id!r}")
        lookback = _lookback(request)
        as_of = _utc(request.as_of)

        universe = self._universe(as_of.normalize().replace(day=1))
        universe_hash = canonical_hash(
            {"universe_id": request.universe_id, "instruments": list(universe)}
        )
        request_hash = request.request_hash()
        # feature_code_hash is an empty placeholder at the snapshot stage (C-02 fills it)
        key = (self.dataset_hash, as_of, universe_hash, request_hash, "")
        if key in self._cache:
            return self._cache[key]

        view = self.tape.as_of(as_of)
        bars = view.bars_1d[view.bars_1d["instrument_id"].isin(universe)]
        if request.closed_bars_only:
            # a bar stamped at or after as_of is still forming, so it is not closed
            bars = bars[bars["event_time"] < as_of]
        bars = (
            bars.sort_values(["instrument_id", "event_time"], kind="stable")
            .groupby("instrument_id", sort=True)
            .tail(lookback)
            .reset_index(drop=True)
        )

        availability = _availability(
            bars, universe, as_of, lookback, request.max_staleness,
            _delisted_at(view.instruments),
        )
        snapshot = SnapshotRef(
            snapshot_id=snapshot_id_for(
                self.dataset_hash, as_of, universe_hash, request_hash, bars
            ),
            as_of=as_of,
            dataset_hash=self.dataset_hash,
            universe_hash=universe_hash,
            request_hash=request_hash,
            universe=universe,
            bars=bars,
            availability=availability,
        )
        self._cache[key] = snapshot
        return snapshot

    def _universe(self, month_start) -> tuple:
        # Rank at the month start with the month-start view only: no later data.
        view = self.tape.as_of(month_start)
        bars = view.bars_1d
        before = bars[bars["event_time"] < month_start]
        history = before.groupby("instrument_id").size()
        trailing = before[before["event_time"] >= month_start - _RANK_WINDOW]
        liquidity = trailing.groupby("instrument_id")["quote_volume"].mean()

        latest = _latest(view.instruments)
        ranked = []
        for _, row in latest[latest["kind"] == "perp"].iterrows():
            iid = row["instrument_id"]
            if pd.isna(row["listed_at"]) or row["listed_at"] > month_start:
                continue
            if not pd.isna(row["delisted_at"]) and row["delisted_at"] < month_start:
                continue
            if int(history.get(iid, 0)) < self.min_history_days:
                continue
            liquidity_value = liquidity.get(iid)
            if liquidity_value is None or pd.isna(liquidity_value):
                continue
            ranked.append((iid, float(liquidity_value)))
        ranked.sort(key=lambda item: (-item[1], item[0]))
        return tuple(iid for iid, _ in ranked[: self.universe_size])


def _lookback(request) -> int:
    if any(tf != "1d" for tf, _, _ in request.timeframes):
        raise ValueError("the tape only provides 1d bars")
    try:
        return next(int(n) for tf, n, _ in request.timeframes if tf == "1d")
    except StopIteration:
        raise ValueError("request must declare a '1d' timeframe") from None


def _utc(t) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _latest(instruments):
    return (
        instruments.sort_values(["event_time", "revision"])
        .groupby("instrument_id", sort=False)
        .tail(1)
    )


def _delisted_at(instruments) -> dict:
    latest = _latest(instruments)
    return dict(zip(latest["instrument_id"], latest["delisted_at"]))


def _availability(bars, universe, as_of, lookback, max_staleness, delisted_at) -> pd.DataFrame:
    max_staleness = pd.Timedelta(max_staleness)
    records = {}
    for iid in universe:
        group = bars[bars["instrument_id"] == iid]
        n_bars = int(len(group))
        last_bar_time = group["event_time"].max() if n_bars else pd.NaT
        last_available_at = group["available_at"].max() if n_bars else pd.NaT
        missing = n_bars < lookback
        stale = bool(pd.isna(last_bar_time)) or (as_of - last_bar_time) > max_staleness
        end = delisted_at.get(iid)
        delisted = bool(not pd.isna(end) and end <= as_of)
        flags = tuple(
            sorted({q for q in group["quality"].astype(str) if q != "ok"})
        )
        records[iid] = {
            "last_bar_time": last_bar_time,
            "last_available_at": last_available_at,
            "n_bars": n_bars,
            "missing": missing,
            "stale": stale,
            "delisted": delisted,
            "quality_flags": flags,
        }
    availability = pd.DataFrame.from_dict(
        records, orient="index", columns=_AVAILABILITY_COLUMNS
    )
    availability.index.name = "instrument_id"
    for column in ("missing", "stale", "delisted"):
        availability[column] = availability[column].astype(bool)
    availability["n_bars"] = availability["n_bars"].astype("int64")
    return availability
