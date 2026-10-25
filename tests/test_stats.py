import math

import numpy as np
import pytest
from scipy.stats import norm

from tycho.config import PendingParameterError
from tycho.stats import (
    beta_adjusted_alpha,
    block_bootstrap_sharpe,
    deflated_sharpe,
    mean_holding_period,
)

EULER_GAMMA = 0.5772156649015329


def test_dsr_matches_worked_example():
    # sr=0.1 per period, T=1000, normal moments (skew 0, kurt 3), N=10, V=0.001.
    # SR0 = sqrt(0.001) * ((1-g)*Phi^-1(0.9) + g*Phi^-1(1 - 1/(10*e)))
    n_eff, trial_var = 10, 0.001
    sr0 = math.sqrt(trial_var) * (
        (1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_eff)
        + EULER_GAMMA * norm.ppf(1 - 1 / (n_eff * math.e))
    )
    assert sr0 == pytest.approx(0.049793170320847, rel=0, abs=1e-15)
    scale = math.sqrt(1000 - 1) / math.sqrt(1 - 0 * 0.1 + (3 - 1) / 4 * 0.1**2)
    expected = norm.cdf((0.1 - sr0) * scale)
    assert expected == pytest.approx(0.943281623071475, rel=0, abs=1e-12)
    result = deflated_sharpe(0.1, 1000, 0, 3, n_eff, trial_var)
    assert result["dsr"] == pytest.approx(expected, rel=0, abs=1e-12)
    assert result["sr0"] == pytest.approx(sr0, rel=0, abs=1e-15)
    assert result["psr_vs_zero"] == pytest.approx(
        norm.cdf(0.1 * scale), rel=0, abs=1e-12
    )


def test_dsr_single_trial_is_psr():
    result = deflated_sharpe(0.1, 1000, 0, 3, 1, 0.0)
    assert result["sr0"] == 0.0
    assert result["dsr"] == pytest.approx(result["psr_vs_zero"])
    # Normal kurtosis still divides by sqrt(1 + (3-1)/4 * sr^2).
    scale = math.sqrt(999) / math.sqrt(1 + (3 - 1) / 4 * 0.1**2)
    assert result["psr_vs_zero"] == pytest.approx(norm.cdf(0.1 * scale))


def test_dsr_pending_raises():
    for n_eff, var in [("PENDING", 0.001), (None, 0.001), (10, "PENDING"), (10, None)]:
        with pytest.raises(PendingParameterError):
            deflated_sharpe(0.1, 1000, 0, 3, n_eff, var)


def test_dsr_decreases_with_trials():
    values = [deflated_sharpe(0.1, 1000, 0, 3, n, 0.001)["dsr"] for n in (1, 2, 5, 50, 500)]
    assert values == sorted(values, reverse=True)


def test_mean_holding_period_hand_case():
    # Run lengths pooled over columns: 3, 2, 1 (column 0) and 2 (column 1) -> 8/4.
    w = np.array(
        [
            [1, 0],
            [1, 0],
            [1, 1],
            [0, 1],
            [-1, 0],
            [-1, 0],
            [0, 0],
            [1, 0],
        ],
        dtype=float,
    )
    assert mean_holding_period(w) == pytest.approx(2.0)


def test_block_bootstrap_deterministic_per_seed():
    pnl = np.linspace(-0.01, 0.02, 200)
    first = block_bootstrap_sharpe(pnl, 5, 50, seed=123)
    again = block_bootstrap_sharpe(pnl, 5, 50, seed=123)
    other = block_bootstrap_sharpe(pnl, 5, 50, seed=124)
    assert np.array_equal(first["sharpe_dist"], again["sharpe_dist"])
    assert first["p_le_zero"] == again["p_le_zero"]
    assert not np.array_equal(first["sharpe_dist"], other["sharpe_dist"])


def test_block_bootstrap_positive_vs_zero_mean():
    rng = np.random.default_rng(0)
    positive = rng.normal(0.002, 0.01, 1000)
    strong = block_bootstrap_sharpe(positive, 10, 400, seed=1)
    assert strong["p_le_zero"] < 0.05

    zero = rng.normal(0.0, 0.01, 1000)
    zero -= zero.mean()  # exact zero mean so only sampling noise remains
    neutral = block_bootstrap_sharpe(zero, 10, 400, seed=6)
    assert 0.25 < neutral["p_le_zero"] < 0.75


def test_block_bootstrap_pending_raises():
    for block in ("PENDING", None):
        with pytest.raises(PendingParameterError):
            block_bootstrap_sharpe(np.zeros(10), block, 10, seed=0)


def test_alpha_recovers_planted_alpha():
    rng = np.random.default_rng(7)
    benchmark = rng.normal(0, 0.01, 2000)
    strategy = 0.0005 + 0.5 * benchmark + rng.normal(0, 0.002, 2000)
    result = beta_adjusted_alpha(strategy, benchmark, hac_maxlags=5)
    assert result["beta"] == pytest.approx(0.5, abs=0.05)
    assert result["alpha"] == pytest.approx(0.0005, abs=0.0002)
    assert result["alpha_annualized"] == pytest.approx(result["alpha"] * 365)
    assert result["p_one_sided"] < 0.10
    assert result["pass_90"] is True


def test_alpha_pure_beta_fails():
    rng = np.random.default_rng(8)
    benchmark = rng.normal(0, 0.01, 2000)
    strategy = 0.5 * benchmark + rng.normal(0, 0.01, 2000)
    result = beta_adjusted_alpha(strategy, benchmark, hac_maxlags=5)
    assert result["beta"] == pytest.approx(0.5, abs=0.05)
    assert result["pass_90"] is False


def test_alpha_pending_raises():
    for lags in ("PENDING", None):
        with pytest.raises(PendingParameterError):
            beta_adjusted_alpha(np.zeros(10), np.zeros(10), lags)
