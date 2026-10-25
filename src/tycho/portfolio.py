"""C-04/C-05 portfolio construction: constant risk budget, forecast-vol and gross caps."""

import math

import pandas as pd


def risk_budget(target_vol, n, rho) -> float:
    """Constant per-asset risk budget b that hits target_vol at average correlation rho."""
    return target_vol / (n * math.sqrt(rho + (1.0 - rho) / n))


def target_weights(scores, vols, cov, target_vol, rho, n, vol_cap_multiple, gross_cap):
    """Raw w_i = score_i * b / vol_i, then one proportional shrink for the binding cap.

    ``n`` is the configured universe size, not the current number of instruments.
    Returns (weights, diag); diag holds b, raw gross, forecast vol, scale s and cap.
    """
    b = risk_budget(target_vol, n, rho)
    vol = vols.reindex(scores.index)
    weights = pd.Series(0.0, index=scores.index)
    ok = scores.notna() & vol.notna() & (vol > 0)
    weights[ok] = scores[ok] * b / vol[ok]

    raw_gross = float(weights.abs().sum())
    active = weights.index[weights != 0.0]
    forecast = 0.0
    if len(active):
        sub = cov.loc[active, active].to_numpy(dtype=float)
        w = weights.loc[active].to_numpy(dtype=float)
        variance = float(w @ sub @ w)
        # ewma_cov has no PSD repair; bad or missing cov is input error, never a shrink
        if not variance >= 0.0:
            raise ValueError(f"forecast variance is invalid: {variance!r}")
        forecast = math.sqrt(variance)

    s_vol = vol_cap_multiple * target_vol / forecast if forecast > 0.0 else 1.0
    s_gross = gross_cap / raw_gross if raw_gross > 0.0 else 1.0
    s = min(1.0, s_vol, s_gross)  # D-06: shrink only, never lever up
    cap = "none" if s == 1.0 else ("vol_cap" if s_vol <= s_gross else "gross_cap")
    return weights * s, {
        "b": b,
        "raw_gross": raw_gross,
        "forecast_vol": forecast,
        "s": s,
        "cap": cap,
    }


def net_targets(weights_list) -> pd.Series:
    """Net one target per instrument across experts (one-way position mode, C-04)."""
    return pd.concat(list(weights_list), axis=1).fillna(0.0).sum(axis=1)
