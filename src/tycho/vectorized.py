"""Section 08 core rule: apply the pure C-02...C-05 code to full history, keep the last row."""

import numpy as np
import pandas as pd

from .factors import _ANNUALIZATION_DAYS, _ewma, _returns, _validate
from .marketstate import _daily_close_before, _utc
from .portfolio import target_weights
from .tape import _visible


def universe_panel(provider, dates) -> pd.DataFrame:
    """Boolean date x instrument membership from the provider's monthly PIT universe."""
    dates = pd.DatetimeIndex([_utc(date) for date in dates])
    months = {date: date.normalize().replace(day=1) for date in dates}
    # reuse the provider's monthly PIT ranking: eligibility rules live there
    membership = {month: set(provider._universe(month)) for month in set(months.values())}
    instruments = sorted(set().union(*membership.values()))
    return pd.DataFrame(
        {iid: [iid in membership[months[date]] for date in dates] for iid in instruments},
        index=dates,
        dtype=bool,
    )


def trend_panels(tape, ma_periods, ewma_convention, *, decision_lag) -> dict:
    """PIT close, score and vol, indexed by bar close; as_of is the decision clock."""
    convention = _validate(ewma_convention)
    lag = pd.Timedelta(decision_lag)
    # shortcut: daily row labels support lag <= 1D, use explicit schedules for longer lags
    if pd.isna(lag) or not pd.Timedelta(0) < lag <= pd.Timedelta(days=1):
        raise ValueError("decision_lag must be positive and at most one day")
    source = tape.bars_1d
    dates = pd.DatetimeIndex(source["event_time"].unique(), name="event_time").sort_values()
    instruments = pd.Index(sorted(source["instrument_id"].unique()), name="instrument_id")
    panels = {
        name: pd.DataFrame(np.nan, index=dates, columns=instruments)
        for name in ("close", "score", "vol")
    }
    panels["as_of"] = pd.Series(dates + lag, index=dates)
    for date, as_of in panels["as_of"].items():
        # Recompute from the visible revisions: correcting an old close changes
        # subsequent returns and EWMA state, never previously emitted rows.
        bars = _visible(source, as_of)
        bars = bars[bars["event_time"] < as_of].sort_values(
            ["instrument_id", "event_time"], kind="stable"
        )
        expected_close = _daily_close_before(as_of)
        for iid, group in bars.groupby("instrument_id", sort=False):
            current = group[group["event_time"] == expected_close]
            if current.empty:
                continue
            closes = group["close"]
            close = float(current["close"].iloc[-1])
            mas = [
                closes.tail(k).mean(skipna=False) if len(closes) >= k else np.nan
                for k in ma_periods
            ]
            panels["close"].at[date, iid] = close
            panels["score"].at[date, iid] = np.mean(np.sign(close / np.asarray(mas) - 1.0))
            panels["vol"].at[date, iid] = _vol_series(closes, convention)[-1]
    return panels


def weights_panel(
    panels, universe, cov_fn, target_vol, rho, n, vol_cap_multiple, gross_cap, *, on_abstain
) -> pd.DataFrame:
    """One target_weights call per date; the caller chooses the ABSTAIN policy.

    cov_fn(as_of, instruments) returns the covariance over the instruments that
    the row's non-zero scores make active.
    """
    score = panels["score"]
    vol = panels["vol"]
    instruments = list(score.columns)
    member = universe.reindex(
        index=score.index, columns=instruments, fill_value=False
    ).astype(bool)
    rows = {}
    for date in score.index:
        scores = score.loc[date].where(member.loc[date], 0.0)
        vols = vol.loc[date]
        active = list(scores.index[scores.notna() & (scores != 0.0) & vols.notna() & (vols > 0)])
        weights, _ = target_weights(
            scores,
            vols,
            cov_fn(panels["as_of"].loc[date], active),
            target_vol,
            rho,
            n,
            vol_cap_multiple,
            gross_cap,
            on_abstain=on_abstain,
        )
        rows[date] = weights
    return pd.DataFrame.from_dict(rows, orient="index", columns=instruments)


def _vol_series(closes, convention) -> np.ndarray:
    # same zero-mean variance recursion as factors._ewma_vol, one value per bar
    variance = _ewma(_returns(closes, convention) ** 2, convention)
    return np.sqrt(variance.to_numpy() * _ANNUALIZATION_DAYS)
