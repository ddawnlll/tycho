"""E-001 §14 component runs and PIT BTC subperiod inputs (no gate decisions)."""

import numpy as np
import pandas as pd

from . import config, e001
from .tape import _utc, _visible


def btc_subperiod_labels(tape, decisions, btc_instrument_id) -> pd.Series:
    """Compare the latest available BTC close with its trailing 200-bar MA.

    Revisions enter only at available_at. Warmup/missing values are unclassified;
    an exact MA tie is flat, not evidence for either up or down.
    """
    dates = pd.DatetimeIndex([_utc(t) for t in decisions])
    bars = tape.bars_1d.loc[tape.bars_1d["instrument_id"] == btc_instrument_id]
    labels = pd.Series(pd.NA, index=dates, dtype="string", name="btc_subperiod")
    for i, date in enumerate(dates):
        visible = _visible(bars, date)
        closes = visible.loc[visible["event_time"] < date].sort_values(
            "event_time"
        )["close"].tail(200)
        if len(closes) != 200 or not np.isfinite(closes).all() or (closes <= 0).any():
            continue
        difference = closes.iloc[-1] - closes.mean()
        labels.iloc[i] = "up" if difference > 0 else "down" if difference < 0 else "flat"
    return labels


def net_by_subperiod(net_pnl: pd.Series, labels: pd.Series) -> dict:
    """Sum realized net P&L in each labelled observation, without resetting a book.

    The caller supplies already cost/funding-inclusive P&L and labels at its
    decision timestamps (not future return timestamps). Calendar blocks are a
    separate diagnostic, never an additional gate here.
    """
    if not net_pnl.index.equals(labels.index) or not net_pnl.index.is_unique:
        raise ValueError("net P&L and labels must have identical unique decision indexes")
    if not np.isfinite(net_pnl).all():
        raise ValueError("net P&L must be finite")
    if not labels.dropna().isin(["up", "down", "flat"]).all():
        raise ValueError("unknown BTC subperiod label")
    groups = labels.fillna("unclassified")
    return {
        label: {
            "observations": int((groups == label).sum()),
            "net_pnl": float(net_pnl[groups == label].sum())
            if (groups == label).any() else None,
        }
        for label in ("up", "down", "flat", "unclassified")
    }


def run_components(tape, params, synthetic: bool) -> dict:
    """Rerun the entire E-001 pipeline for each configured singleton K.

    Each run appends its own main and constant-notional trials through run_e001;
    the returned per-k params/trial hashes identify those actual executions.
    No component returns are sliced from a combined signal or averaged book.
    """
    periods = list(params.get("ma_periods_days", config.load()["signal"]["ma_periods_days"]))
    if (not periods or any(type(k) is not int or k <= 0 for k in periods)
            or len(set(periods)) != len(periods)):
        raise ValueError("MA periods must be distinct positive integers")
    return {
        k: e001.run_e001(tape, {**params, "ma_periods_days": [k]}, synthetic=synthetic)
        for k in periods
    }
