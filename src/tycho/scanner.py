"""§10 scan formula: vectorised approximate net PnL over variants (CPU numpy, D-04).

The §07 sequential event ledger is authoritative; this is the scan approximation.
All inputs are float64 and must come from the caller (no monograph parameter is
guessed here).
"""

import numpy as np


def scan(w, r, c, f):
    """Net PnL for target weights w [V,T,A], returns r, cost rates c, funding f [T,A].

    w[v,t,a] is the target weight decided at the end of period t (NaN -> 0),
    r[t,a] the simple return over t, c[t,a] the proportional cost rate per unit
    traded weight and f[t,a] the funding rate accumulated over t (positive =
    long pays).
    """
    w = np.asarray(w, dtype=np.float64)
    if w.ndim != 3:
        raise ValueError(f"scan: w must be [V,T,A], got shape {w.shape}")
    w = np.where(np.isnan(w), 0.0, w)
    r = np.asarray(r, dtype=np.float64)
    c = np.asarray(c, dtype=np.float64)
    f = np.asarray(f, dtype=np.float64)
    if w.shape[1:] != r.shape or r.shape != c.shape or r.shape != f.shape:
        raise ValueError(
            f"scan: r/c/f must be [T,A]={w.shape[1:]}, got {r.shape}, {c.shape}, {f.shape}"
        )
    V, T, A = w.shape
    if T == 0:
        raise ValueError("scan: at least one period is required")

    out = {
        key: np.zeros((V, T), dtype=np.float64)
        for key in ("pnl", "gross", "cost", "funding", "turnover")
    }

    # t = 0: enter from flat, so there is trading cost but no gross or funding.
    held = np.abs(w[:, 0, :])
    out["turnover"][:, 0] = np.sum(held, axis=1)
    out["cost"][:, 0] = np.sum(c[0] * held, axis=1)
    out["pnl"][:, 0] = -out["cost"][:, 0]

    for t in range(1, T):
        held = w[:, t - 1, :]
        gross_t = np.sum(held * r[t], axis=1)
        # w̃: the weight price drift leaves before any trade (why cost uses it).
        drifted = held * (1.0 + r[t]) / (1.0 + gross_t)[:, None]
        trade = np.abs(w[:, t, :] - drifted)
        out["gross"][:, t] = gross_t
        out["turnover"][:, t] = np.sum(trade, axis=1)
        out["cost"][:, t] = np.sum(c[t] * trade, axis=1)
        out["funding"][:, t] = np.sum(held * f[t], axis=1)
        out["pnl"][:, t] = gross_t - out["cost"][:, t] - out["funding"][:, t]

    out["equity"] = np.cumprod(1.0 + out["pnl"], axis=1)
    return out


def annualized_sharpe(pnl_row, periods_per_year=365):
    """Annualised mean/std of a net PnL row; risk-free is 0 (crypto, 365 days)."""
    x = np.asarray(pnl_row, dtype=np.float64)
    sd = x.std(ddof=1)
    if not sd > 0:
        return float("nan")
    return float(x.mean() / sd * np.sqrt(periods_per_year))
