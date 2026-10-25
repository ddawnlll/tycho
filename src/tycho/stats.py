"""Section 11/14 statistics: deflated Sharpe, stationary block bootstrap, HAC alpha."""

import math

import numpy as np
import statsmodels.api as sm
from arch.bootstrap import StationaryBootstrap
from scipy.stats import norm

from .config import PendingParameterError

_EULER_GAMMA = 0.5772156649015329  # Bailey & Lopez de Prado (2014)
_DAYS_PER_YEAR = 365
_ALPHA_LEVEL = 0.10  # one-sided 90% for the beta-adjusted alpha gate


def _require(value, name):
    # None is what an unset config entry looks like after load(); both mean PENDING.
    if value is None or value == "PENDING" or value == "":
        raise PendingParameterError(f"{name} is PENDING")
    return value


def _sharpe_variance(sr, skew, kurtosis):
    variance = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr**2
    if variance <= 0:
        raise ValueError("skew/kurtosis make the Sharpe variance term non-positive")
    return variance


def deflated_sharpe(sr, n_obs, skew, kurtosis, n_trials_eff, trial_sr_variance):
    """Bailey & Lopez de Prado (2014) deflated Sharpe ratio.

    sr and SR0 are per-period (non-annualized) Sharpe; kurtosis is non-excess
    (normal = 3). n_trials_eff and trial_sr_variance are PENDING in the
    monograph, so they are required arguments.
    """
    n_eff = _require(n_trials_eff, "dsr_trial_effective_n")
    var = _require(trial_sr_variance, "dsr_trial_sharpe_variance")
    if n_eff < 1:
        raise ValueError("n_trials_eff must be >= 1")
    scale = math.sqrt(n_obs - 1) / math.sqrt(_sharpe_variance(sr, skew, kurtosis))
    psr_vs_zero = float(norm.cdf(sr * scale))
    if n_eff == 1:
        # Expected max Sharpe over one trial is 0, so DSR is just the PSR.
        return {"dsr": psr_vs_zero, "sr0": 0.0, "psr_vs_zero": psr_vs_zero}
    sr0 = math.sqrt(var) * (
        (1.0 - _EULER_GAMMA) * norm.ppf(1.0 - 1.0 / n_eff)
        + _EULER_GAMMA * norm.ppf(1.0 - 1.0 / (n_eff * math.e))
    )
    return {
        "dsr": float(norm.cdf((sr - sr0) * scale)),
        "sr0": float(sr0),
        "psr_vs_zero": psr_vs_zero,
    }


def mean_holding_period(w):
    """Mean length in days of nonzero same-sign runs, pooled across assets."""
    w = np.asarray(w, dtype=float)
    if w.ndim == 1:
        w = w[:, None]
    lengths = []
    for column in w.T:
        run = 0
        previous = 0.0
        for value in column:
            sign = float(np.sign(value))
            if sign != 0.0 and sign == previous:
                run += 1
            elif sign != 0.0:
                if run:
                    lengths.append(run)
                run = 1
            else:
                if run:
                    lengths.append(run)
                run = 0
            previous = sign
        if run:
            lengths.append(run)
    return float(np.mean(lengths)) if lengths else 0.0


def _annualized_sharpe(values):
    values = np.asarray(values, dtype=float)
    sd = values.std(ddof=1)
    if sd == 0.0:
        return float("nan")
    return float(values.mean() / sd * math.sqrt(_DAYS_PER_YEAR))


def block_bootstrap_sharpe(pnl, mean_block, reps, seed):
    """Stationary bootstrap distribution of annualized Sharpe (365 days).

    mean_block is PENDING in the monograph (rule: >= mean holding period,
    frozen before looking), so the caller must pass it.
    """
    block = math.ceil(_require(mean_block, "bootstrap_block_rule"))  # keep block >= frozen mean
    values = np.asarray(pnl, dtype=float).ravel()
    bs = StationaryBootstrap(block, values, seed=seed)
    dist = np.array(
        [_annualized_sharpe(sample[0][0]) for sample in bs.bootstrap(reps)],
        dtype=float,
    )
    low, high = np.quantile(dist, [0.05, 0.95])
    return {
        "sharpe_dist": dist,
        "p_le_zero": float(np.mean(dist <= 0.0)),
        "interval_90": (float(low), float(high)),
    }


def beta_adjusted_alpha(strategy_pnl, benchmark_pnl, hac_maxlags):
    """HAC OLS alpha of strategy on benchmark; one-sided 90% test for alpha > 0.

    hac_maxlags is not specified in the monograph, so it is a required argument.
    """
    maxlags = int(_require(hac_maxlags, "hac_maxlags"))
    y = np.asarray(strategy_pnl, dtype=float).ravel()
    x = np.asarray(benchmark_pnl, dtype=float).ravel()
    fit = sm.OLS(y, sm.add_constant(x)).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags}
    )
    alpha = float(fit.params[0])
    t_stat = float(fit.tvalues[0])
    p_one_sided = float(norm.sf(t_stat))  # HAC p-values use the normal distribution
    return {
        "alpha": alpha,
        "alpha_annualized": alpha * _DAYS_PER_YEAR,
        "beta": float(fit.params[1]),
        "t": t_stat,
        "p_one_sided": p_one_sided,
        "pass_90": bool(alpha > 0.0 and p_one_sided < _ALPHA_LEVEL),
    }
