"""Deterministic synthetic market tape (DEV only; never real data)."""

import numpy as np
import pandas as pd

from .tape import (
    BAR_COLUMNS,
    FUNDING_COLUMNS,
    INSTRUMENT_COLUMNS,
    MARK_COLUMNS,
    SPEC_COLUMNS,
    Tape,
    validate,
)

# BTC first and SHIB second so both exist for any n_perps >= 2.
_TICKERS = [
    "BTC", "SHIB", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT", "LTC",
    "LINK", "BCH", "XLM", "EOS", "TRX", "ETC", "XMR", "ZEC", "DASH", "ATOM",
    "NEO", "IOTA", "VET", "THETA", "ALGO", "AVAX", "FTM", "SAND", "MANA", "CHZ",
    "ENJ", "GRT", "ONE", "ZIL", "QTUM", "IOST", "WAVES", "KSM", "EGLD", "HBAR",
]


def make_synthetic_tape(seed=0, n_perps=40, start="2019-09-01", days=900, rho=0.5) -> Tape:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(start, tz="UTC")
    stamps = pd.date_range(start, periods=days, freq="D")

    symbols = ["1000SHIBUSDT" if t == "SHIB" else f"{t}USDT" for t in _TICKERS[:n_perps]]
    offsets = np.zeros(n_perps, dtype=int)
    offsets[1:] = rng.integers(1, max(2, days - 2), size=n_perps - 1)
    # last three non-BTC perps delist midway through the tape
    delisted = list(range(max(1, n_perps - 3), n_perps))
    last_idx = {i: int(offsets[i] + (days - 1 - offsets[i]) // 2) for i in delisted}

    annual_vol = rng.uniform(0.30, 1.20, size=n_perps)
    daily_vol = annual_vol / np.sqrt(365)
    base_price = 10.0 ** rng.uniform(-2.0, 4.0, size=n_perps)
    common = rng.standard_normal(days)
    idio = rng.standard_normal((n_perps, days))
    volumes = rng.lognormal(10.0, 1.0, size=(n_perps, days))
    root = np.sqrt(rho)
    resid = np.sqrt(1.0 - rho)

    slots = pd.date_range(start, periods=3 * days, freq="8h")

    instrument_rows, spec_rows, bar_frames, funding_frames, mark_frames = [], [], [], [], []
    for i, sym in enumerate(symbols):
        first = int(offsets[i])
        end = last_idx.get(i, days - 1)
        listed_at = stamps[first]
        delisted_at = stamps[end] + pd.Timedelta(days=1) if i in last_idx else pd.NaT
        last_trade_day = stamps[end] if i in last_idx else pd.NaT
        multiplier = 1000.0 if sym == "1000SHIBUSDT" else 1.0

        instrument_rows.append(_common_row(
            f"instruments:{sym}:0", listed_at, listed_at + pd.Timedelta(minutes=1)
        ) | {
            "instrument_id": sym,
            "symbol": sym,
            "kind": "perp",
            "listed_at": listed_at,
            # the delisting is published by a later revision: a pre-delisting view
            # must not already know it
            "delisted_at": pd.NaT,
            "last_trade_day": pd.NaT,
        })
        if i in last_idx:
            instrument_rows.append(_common_row(
                f"instruments:{sym}:1",
                listed_at,
                delisted_at,
                revision=1,
            ) | {
                "instrument_id": sym,
                "symbol": sym,
                "kind": "perp",
                "listed_at": listed_at,
                "delisted_at": delisted_at,
                "last_trade_day": last_trade_day,
            })
        spec_rows.append(_common_row(
            f"specs:{sym}:1", listed_at, listed_at + pd.Timedelta(minutes=1)
        ) | {
            "instrument_id": sym,
            "instrument_spec_version": 1,
            "valid_from": listed_at,
            "tick_size": 0.01,
            "step_size": 0.001,
            "min_notional": 5.0,
            "contract_multiplier": multiplier,
            "funding_interval_hours": 8.0,
        })
        if i % 4 == 1:
            mid = stamps[first + (end - first) // 2]
            spec_rows.append(_common_row(
                f"specs:{sym}:2", mid, mid + pd.Timedelta(minutes=1)
            ) | {
                "instrument_id": sym,
                "instrument_spec_version": 2,
                "valid_from": mid,
                "tick_size": 0.001,
                "step_size": 1.0,
                "min_notional": 10.0,
                "contract_multiplier": multiplier,
                "funding_interval_hours": 8.0,
            })

        n = end - first + 1
        rets = daily_vol[i] * (root * common[first:end + 1] + resid * idio[i, first:end + 1])
        close = base_price[i] * np.exp(np.cumsum(rets))
        open_ = np.concatenate([[base_price[i]], close[:-1]])
        wick = rng.uniform(0.0, 0.02, size=n)
        high = np.maximum(open_, close) * (1.0 + wick)
        low = np.minimum(open_, close) * (1.0 - wick)
        volume = volumes[i, first:end + 1]
        event_times = stamps[first:end + 1]
        available = event_times + pd.Timedelta(minutes=1)

        bars = pd.DataFrame({
            "event_id": [f"bars_1d:{sym}:{ts.isoformat()}:0" for ts in event_times],
            "event_time": event_times,
            "received_at": available,
            "received_at_is_observed": False,
            "available_at": available,
            "source": "synthetic",
            "revision": 0,
            "quality": "ok",
            "instrument_id": sym,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "quote_volume": volume * close,
        })
        # one corrected bar revision: same event, newer revision, available later
        if i == 0:
            j = min(5, n - 1)
            correction = bars.iloc[[j]].copy()
            correction["revision"] = 1
            correction["close"] = correction["close"] * 0.999
            correction["event_id"] = f"bars_1d:{sym}:{event_times[j].isoformat()}:1"
            correction["available_at"] = event_times[j] + pd.Timedelta(days=2)
            correction["received_at"] = correction["available_at"]
            bars = pd.concat([bars, correction], ignore_index=True)
        bar_frames.append(bars[list(BAR_COLUMNS)])

        active = slots[(slots >= listed_at) & (slots <= event_times[-1])]
        rates = np.clip(rng.normal(0.0, 1e-4, size=len(active)), -1e-3, 1e-3)
        funding_frames.append(pd.DataFrame({
            "event_id": [f"funding:{sym}:{ts.isoformat()}:0" for ts in active],
            "event_time": active,
            "received_at": active + pd.Timedelta(minutes=1),
            "received_at_is_observed": False,
            "available_at": active + pd.Timedelta(minutes=1),
            "source": "synthetic",
            "revision": 0,
            "quality": "ok",
            "instrument_id": sym,
            "rate": rates,
        })[list(FUNDING_COLUMNS)])

        mark_price = close * (1.0 + rng.normal(0.0, 2e-4, size=n))
        mark_frames.append(pd.DataFrame({
            "event_id": [f"mark:{sym}:{ts.isoformat()}:0" for ts in event_times],
            "event_time": event_times,
            "received_at": available,
            "received_at_is_observed": False,
            "available_at": available,
            "source": "synthetic",
            "revision": 0,
            "quality": "ok",
            "instrument_id": sym,
            "mark_price": mark_price,
        })[list(MARK_COLUMNS)])

    for sym, kind in (("USDCUSDT", "stablecoin"), ("BTCDOMUSDT", "index")):
        instrument_rows.append(_common_row(
            f"instruments:{sym}:0", start, start + pd.Timedelta(minutes=1)
        ) | {
            "instrument_id": sym,
            "symbol": sym,
            "kind": kind,
            "listed_at": start,
            "delisted_at": pd.NaT,
            "last_trade_day": pd.NaT,
        })
        spec_rows.append(_common_row(
            f"specs:{sym}:1", start, start + pd.Timedelta(minutes=1)
        ) | {
            "instrument_id": sym,
            "instrument_spec_version": 1,
            "valid_from": start,
            "tick_size": 0.0001,
            "step_size": 1.0,
            "min_notional": 5.0,
            "contract_multiplier": 1.0,
            "funding_interval_hours": np.nan,
        })

    tape = Tape(
        instruments=_frame(instrument_rows, INSTRUMENT_COLUMNS,
                           ("event_time", "received_at", "available_at",
                            "listed_at", "delisted_at", "last_trade_day")),
        specs=_frame(spec_rows, SPEC_COLUMNS,
                     ("event_time", "received_at", "available_at", "valid_from")),
        bars_1d=pd.concat(bar_frames, ignore_index=True)[list(BAR_COLUMNS)],
        funding=pd.concat(funding_frames, ignore_index=True)[list(FUNDING_COLUMNS)],
        mark=pd.concat(mark_frames, ignore_index=True)[list(MARK_COLUMNS)],
        meta={"synthetic": True, "seed": seed},
    )
    validate(tape)
    return tape


def _common_row(event_id, event_time, available_at, revision=0):
    return {
        "event_id": event_id,
        "event_time": event_time,
        "received_at": available_at,
        "received_at_is_observed": False,
        "available_at": available_at,
        "source": "synthetic",
        "revision": revision,
        "quality": "ok",
    }


def _frame(rows, columns, datetime_columns):
    df = pd.DataFrame(rows)
    df = df.reindex(columns=list(columns))
    for name in datetime_columns:
        df[name] = pd.to_datetime(df[name], utc=True)
    df["revision"] = df["revision"].astype("int64")
    df["received_at_is_observed"] = df["received_at_is_observed"].astype(bool)
    return df
