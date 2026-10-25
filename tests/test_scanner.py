"""§10 scan formula: hand case, drift, funding sign, dtype, NaN, variant vectorisation."""

import numpy as np
import pytest

from tycho.scanner import annualized_sharpe, scan


def test_two_asset_three_period_hand_computed():
    # Targets decided at the end of each period; enter from flat at t=0.
    w = np.array([[
        [0.5, 0.5],
        [0.6, 0.4],
        [0.4, 0.6],
    ]])
    r = np.array([
        [0.02, -0.01],
        [0.10, -0.05],
        [-0.02, 0.04],
    ])
    c = np.array([
        [0.001, 0.002],
        [0.001, 0.002],
        [0.001, 0.002],
    ])
    f = np.array([
        [0.0, 0.0],
        [0.01, 0.02],
        [0.03, -0.01],
    ])
    out = scan(w, r, c, f)

    # t=0 from flat: turnover 0.5+0.5 = 1.0, cost 0.001*0.5 + 0.002*0.5 = 0.0015,
    # no gross and no funding.
    # t=1: gross = 0.5*0.10 + 0.5*(-0.05) = 0.025.
    #   w~ = [0.5*1.10/1.025, 0.5*0.95/1.025] = [0.5365853658536587, 0.4634146341463415].
    #   trade = (0.6-0.5365853658536587) + (0.4634146341463415-0.4)
    #         = 0.12682926829268293.
    #   cost = 0.001*(0.6-w~0) + 0.002*(w~1-0.4) = 0.0001902439024390244.
    #   funding = 0.5*0.01 + 0.5*0.02 = 0.015.
    #   pnl = 0.025 - 0.0001902439024390244 - 0.015 = 0.009809756097560976.
    # t=2: gross = 0.6*(-0.02) + 0.4*0.04 = 0.004.
    #   w~ = [0.6*0.98/1.004, 0.4*1.04/1.004] = [0.5856573705179282, 0.4143426294820717].
    #   trade = (0.5856573705179282-0.4) + (0.6-0.4143426294820717)
    #         = 0.3713147410358565.
    #   cost = 0.001*(w~0-0.4) + 0.002*(0.6-w~1) = 0.0005569721115537849.
    #   funding = 0.6*0.03 + 0.4*(-0.01) = 0.014.
    #   pnl = 0.004 - 0.0005569721115537849 - 0.014 = -0.010556972111553785.
    np.testing.assert_allclose(
        out["gross"], [[0.0, 0.025, 0.004]], rtol=0, atol=1e-15
    )
    np.testing.assert_allclose(
        out["turnover"],
        [[1.0, 0.12682926829268293, 0.3713147410358565]],
        rtol=0,
        atol=1e-15,
    )
    np.testing.assert_allclose(
        out["cost"],
        [[0.0015, 0.0001902439024390244, 0.0005569721115537849]],
        rtol=0,
        atol=1e-15,
    )
    np.testing.assert_allclose(
        out["funding"], [[0.0, 0.015, 0.014]], rtol=0, atol=1e-15
    )
    np.testing.assert_allclose(
        out["pnl"],
        [[-0.0015, 0.009809756097560976, -0.010556972111553785]],
        rtol=0,
        atol=1e-15,
    )
    # equity = cumprod(1 + pnl); 0.9985 * 1.009809756... * 0.989443027...
    np.testing.assert_allclose(
        out["equity"],
        [[0.9985, 1.0082950414634146, 0.9976504988304674]],
        rtol=0,
        atol=1e-15,
    )


def test_variant_vectorisation_equals_per_variant_loop():
    rng = np.random.default_rng(7)
    V, T, A = 3, 5, 4
    w = rng.normal(scale=0.3, size=(V, T, A))
    r = rng.normal(scale=0.02, size=(T, A))
    c = rng.uniform(0.0005, 0.003, size=(T, A))
    f = rng.normal(scale=0.0001, size=(T, A))

    together = scan(w, r, c, f)
    for v in range(V):
        alone = scan(w[v : v + 1], r, c, f)
        for key in ("pnl", "gross", "cost", "funding", "turnover", "equity"):
            np.testing.assert_array_equal(together[key][v], alone[key][0])


def test_drift_only_turnover_is_exactly_zero():
    # No cost, no funding, and the next target is already the drifted weight.
    # w0 = [0.5, 0.25], r1 = [0.5, 0.0] -> gross 0.25 exactly,
    # w~ = [0.5*1.5/1.25, 0.25*1.0/1.25] = [0.6, 0.2], both exact in binary.
    w = np.array([[[0.5, 0.25], [0.6, 0.2]]])
    r = np.array([[0.0, 0.0], [0.5, 0.0]])
    c = np.zeros((2, 2))
    f = np.zeros((2, 2))
    out = scan(w, r, c, f)
    assert out["turnover"][0, 1] == 0.0
    assert out["cost"][0, 1] == 0.0
    np.testing.assert_array_equal(out["pnl"][0], [0.0, 0.25])


def test_zero_weights_give_zero_pnl():
    # Zero weights, but returns/costs/funding still present.
    w = np.zeros((2, 4, 3))
    r = np.full((4, 3), 0.05)
    c = np.full((4, 3), 0.01)
    f = np.full((4, 3), 0.02)
    out = scan(w, r, c, f)
    for key in ("pnl", "gross", "cost", "funding", "turnover"):
        np.testing.assert_array_equal(out[key], np.zeros((2, 4)))
    np.testing.assert_array_equal(out["equity"], np.ones((2, 4)))


def test_funding_sign_long_pays_positive_rate():
    # Long 0.5, flat return, zero cost, funding +0.01 over the period -> -0.005.
    w = np.array([[[0.5], [0.5]]])
    r = np.zeros((2, 1))
    c = np.zeros((2, 1))
    f = np.array([[0.0], [0.01]])
    out = scan(w, r, c, f)
    assert out["funding"][0, 1] == 0.005
    assert out["pnl"][0, 1] == -0.005
    assert out["gross"][0, 1] == 0.0


def test_float64_dtype_even_for_integer_and_float32_inputs():
    w = np.zeros((2, 2, 2), dtype=np.float32)
    w[0, 0] = [1, 0]
    r = np.zeros((2, 2), dtype=np.float32)
    c = np.ones((2, 2), dtype=np.float32)
    f = np.zeros((2, 2), dtype=np.float32)
    out = scan(w, r, c, f)
    for key, arr in out.items():
        assert arr.dtype == np.float64, key


def test_nan_weights_are_treated_as_zero():
    r = np.array([[0.01, 0.02], [0.03, -0.04]])
    c = np.array([[0.001, 0.002], [0.003, 0.004]])
    f = np.array([[0.0, 0.0], [0.01, -0.01]])
    with_nan = np.array([[[np.nan, 0.5], [np.nan, np.nan]]])
    zeros = np.array([[[0.0, 0.5], [0.0, 0.0]]])
    nan_out = scan(with_nan, r, c, f)
    zero_out = scan(zeros, r, c, f)
    for key in nan_out:
        np.testing.assert_array_equal(nan_out[key], zero_out[key])


def test_annualized_sharpe_hand_computed():
    # pnl = [1%, -1%, 2%, 0%]: mean 0.005, sd(ddof=1) = sqrt(0.0005/3),
    # sharpe = 0.005 / 0.012909944487358056 * sqrt(365) = 7.399324293474371.
    assert annualized_sharpe([0.01, -0.01, 0.02, 0.0]) == pytest.approx(
        7.399324293474371, rel=1e-12
    )
    # periods_per_year is a required caller choice, not a hidden default value.
    assert annualized_sharpe([0.01, -0.01, 0.02, 0.0], periods_per_year=252) == (
        pytest.approx(7.399324293474371 * np.sqrt(252 / 365), rel=1e-12)
    )
    # Constant pnl has zero dispersion: undefined, reported as NaN.
    assert np.isnan(annualized_sharpe([0.0, 0.0, 0.0]))
