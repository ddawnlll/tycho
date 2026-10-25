import math

import numpy as np
import pandas as pd
import pytest

from tycho.config import load
from tycho.portfolio import net_targets, risk_budget, target_weights

B_EXPECTED = 0.007911877721292357
IDS = [f"I{i:02d}" for i in range(30)]


def flat(n=30, value=1.0, index=None):
    return pd.Series(value, index=index or IDS[:n], dtype=float)


def const_corr_cov(ids, sigma, rho):
    n = len(ids)
    cov = np.full((n, n), sigma * sigma * rho)
    np.fill_diagonal(cov, sigma * sigma)
    return pd.DataFrame(cov, index=ids, columns=ids)


def forecast(weights, cov):
    w = weights.to_numpy(dtype=float)
    return math.sqrt(float(w @ cov.loc[weights.index, weights.index].to_numpy() @ w))


def test_risk_budget_numeric():
    assert risk_budget(0.20, 30, 0.7) == pytest.approx(B_EXPECTED, rel=1e-14)


def test_calibration_hits_target_with_no_shrink():
    cov = const_corr_cov(IDS, 0.6, 0.7)
    weights, diag = target_weights(
        flat(), flat(value=0.6), cov, 0.20, 0.7, 30, 2.0, 2.0
    )
    assert forecast(weights, cov) == pytest.approx(0.20, abs=1e-12)
    assert diag["forecast_vol"] == pytest.approx(0.20, abs=1e-12)
    assert weights.iloc[0] == pytest.approx(B_EXPECTED / 0.6, rel=1e-14)
    assert diag["s"] == 1.0
    assert diag["cap"] == "none"


def test_vol_cap_scales_book_to_twice_target():
    # scores cannot exceed 1, so the vol cap is reached by shrinking vols instead
    cov = const_corr_cov(IDS, 0.6, 0.7)
    weights, diag = target_weights(flat(), flat(value=0.2), cov, 0.20, 0.7, 30, 2.0, 2.0)
    assert diag["forecast_vol"] == pytest.approx(0.60, abs=1e-12)
    assert diag["s"] == pytest.approx(2.0 / 3.0, rel=1e-15)
    assert diag["cap"] == "vol_cap"
    assert forecast(weights, cov) == pytest.approx(0.40, abs=1e-12)


def test_gross_cap_scales_book_to_two():
    cov = const_corr_cov(IDS, 0.6, 0.0)  # forecast 0.26 < 0.40, so the vol cap is slack
    weights, diag = target_weights(flat(), flat(value=0.1), cov, 0.20, 0.7, 30, 2.0, 2.0)
    assert diag["raw_gross"] == pytest.approx(2.373563316387707, rel=1e-14)
    assert diag["cap"] == "gross_cap"
    assert diag["s"] == pytest.approx(0.8426149773176357, rel=1e-14)
    assert weights.abs().sum() == pytest.approx(2.0, abs=1e-12)


def test_abstain_and_nan_vol_get_zero():
    scores = pd.Series([1.0, np.nan, 1.0, 1.0], index=list("ABCD"))
    vols = pd.Series([0.6, 0.6, np.nan, 0.0], index=list("ABCD"))
    cov = pd.DataFrame(np.eye(4) * 0.36, index=list("ABCD"), columns=list("ABCD"))
    weights, diag = target_weights(scores, vols, cov, 0.20, 0.7, 30, 2.0, 2.0)
    assert list(weights) == [pytest.approx(B_EXPECTED / 0.6), 0.0, 0.0, 0.0]
    assert weights.index.tolist() == list("ABCD")
    assert diag["forecast_vol"] == pytest.approx(B_EXPECTED, rel=1e-14)


def test_scale_never_levers_up():
    tiny = pd.Series(1e-6, index=IDS, dtype=float)
    cov = const_corr_cov(IDS, 0.6, 0.7)
    _, diag = target_weights(flat(value=0.1), flat(value=5.0), cov, 0.20, 0.7, 30, 2.0, 2.0)
    assert diag["s"] == 1.0
    _, diag = target_weights(flat(value=0.1), tiny, cov, 0.20, 0.7, 30, 2.0, 2.0)
    assert 0.0 < diag["s"] < 1.0


def test_negative_forecast_variance_raises():
    # indefinite on purpose: ewma_cov has no PSD repair, so bad input must raise
    cov = pd.DataFrame([[0.36, 0.72], [0.72, 0.36]], index=list("AB"), columns=list("AB"))
    with pytest.raises(ValueError, match="forecast variance"):
        target_weights(
            pd.Series([1.0, -1.0], index=list("AB")),
            pd.Series([0.6, 0.6], index=list("AB")),
            cov,
            0.20,
            0.7,
            30,
            2.0,
            2.0,
        )


def test_net_targets_sums_overlapping_instruments():
    a = pd.Series([0.1, -0.2, 0.3], index=list("ABC"))
    b = pd.Series([0.4, 0.5], index=list("AB"))
    net = net_targets([a, b])
    assert net.index.tolist() == list("ABC")
    assert net.loc["A"] == pytest.approx(0.5)
    assert net.loc["B"] == pytest.approx(0.3)
    assert net.loc["C"] == pytest.approx(0.3)


def test_config_wiring():
    config = load()
    sizing = config["sizing"]
    target_vol = sizing["target_vol"]
    rho = sizing["rho"]
    vol_cap_multiple = sizing["vol_cap_multiple"]
    gross_cap = sizing["gross_leverage_cap"]
    n = config["universe"]["size"]

    assert (target_vol, rho, vol_cap_multiple, gross_cap, n) == (0.20, 0.7, 2.0, 2.0, 30)
    cov = const_corr_cov(IDS, 0.6, rho)
    weights, diag = target_weights(
        flat(index=IDS), flat(value=0.6, index=IDS), cov,
        target_vol, rho, n, vol_cap_multiple, gross_cap,
    )
    assert diag["b"] == pytest.approx(B_EXPECTED, rel=1e-14)
    assert forecast(weights, cov) == pytest.approx(0.20, abs=1e-12)
    assert diag["s"] == 1.0
