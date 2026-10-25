"""Section 08 core rule: apply the pure C-02...C-05 code to full history, keep the last row."""

import numpy as np
import pandas as pd

from .factors import _ANNUALIZATION_DAYS, _ewma, _returns, _validate
from .marketstate import _utc
from .portfolio import target_weights


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


def trend_panels(tape, ma_periods, ewma_convention) -> dict:
    """Full-history close, trend score and EWMA vol at every bar close date."""
    convention = _validate(ewma_convention)
    # Anchor at the tape end: a bar stamped at t is known at t + available lag,
    # and every row reads only bars stamped at or before its own date.
    # shortcut: a revision that lands days after its bar stamp is applied from the
    # stamp row, so rows before its available_at are not PIT-exact
    view = tape.as_of(tape.bars_1d["available_at"].max())
    bars = view.bars_1d.sort_values(["instrument_id", "event_time"], kind="stable")
    # rolling/ewm per instrument keep the bar-count semantics of compute_factors
    for k in ma_periods:
        bars[f"ma_{k}"] = bars.groupby("instrument_id", sort=False)["close"].transform(
            lambda closes, k=k: closes.rolling(k).mean()
        )
    bars["ewma_vol"] = bars.groupby("instrument_id", sort=False)["close"].transform(
        lambda closes: _vol_series(closes, convention)
    )

    def wide(column):
        return bars.pivot(
            index="event_time", columns="instrument_id", values=column
        ).sort_index()

    panels = {"close": wide("close")}
    signs = [np.sign(panels["close"] / wide(f"ma_{k}") - 1.0) for k in ma_periods]
    # any undefined MA turns its sign into NaN, and the mean into NaN
    panels["score"] = sum(signs) / len(signs)
    panels["vol"] = wide("ewma_vol")
    return panels


def weights_panel(
    panels, universe, cov_fn, target_vol, rho, n, vol_cap_multiple, gross_cap
) -> pd.DataFrame:
    """One target_weights call per date; non-members and NaN scores get weight 0.

    cov_fn(date, instruments) returns the covariance over the instruments that
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
        scores = score.loc[date].where(member.loc[date], 0.0).fillna(0.0)
        active = list(scores.index[scores != 0.0])
        weights, _ = target_weights(
            scores,
            vol.loc[date],
            cov_fn(date, active),
            target_vol,
            rho,
            n,
            vol_cap_multiple,
            gross_cap,
        )
        rows[date] = weights
    return pd.DataFrame.from_dict(rows, orient="index", columns=instruments)


def _vol_series(closes, convention) -> np.ndarray:
    # same zero-mean variance recursion as factors._ewma_vol, one value per bar
    variance = _ewma(_returns(closes, convention) ** 2, convention)
    return np.sqrt(variance.to_numpy() * _ANNUALIZATION_DAYS)
