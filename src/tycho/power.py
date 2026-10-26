"""Local, seeded, synthetic joint-power study for E-001 criteria 1-5 (#32).

What this measures
------------------
For each synthetic scenario we draw a fixed number of independent seeded daily
paths, run the same four MA components and the same BTC-up/down subperiod
attribution from each path, compute DSR and beta-adjusted alpha, build the two
criterion-3 null distributions, and pass the results to ``gates.evaluate`` with
explicit thresholds.  The reported quantity is the *joint* pass count for
criteria 1-5 plus a 95% Clopper-Pearson interval.  This is not the old
two-endpoint ``power_approx`` table: it is a conjunction of five gates.

Criterion 6 (holdout) is owner-only.  It is never simulated, never passed as an
attestation, and therefore every gate report here has ``holdout=BLOCKED`` and
``verdict=BLOCKED``; only criteria 1-5 can contribute to a pass count.

Synthetic parameters (explicitly NOT monograph values)
------------------------------------------------------
``planted_sharpe`` is the **annualized** Sharpe of the latent
regime-conditional drift.  The generator sets

    daily_vol = annual_vol / sqrt(365)
    daily_mean = planted_sharpe * daily_vol / sqrt(365)

so the conditional annualized Sharpe is ``daily_mean / daily_vol * sqrt(365)
= planted_sharpe``.  ``annual_vol`` is explicit per scenario.  The realized
MA-ensemble Sharpe is reported per scenario on the same 365-day annualization;
it is not the planted oracle value.

Other explicit synthetic inputs: ``DSR_N_TRIALS_EFF=4`` (the four MA periods),
``DSR_TRIAL_SR_VARIANCE=1e-4`` (per-period trial Sharpe variance),
``HAC_MAXLAGS=5``, ``NULL_REPS=99``.  These are study knobs, not guesses for
the PENDING monograph values; real runs still refuse those PENDING config keys.

Null analogue
-------------
``nulls.py`` executes both nulls through the sequential ledger.  For a local
study that would be needlessly slow, so here the *same D-40 semantics* are
applied to the combined MA position path with a cheap cost model
(``cost_bps/10000 * |weight change|``):

* ``scramble`` keeps each day's ``|w|``, same-sign block boundaries and gross
  exposure; each non-flat block independently flips sign with p=0.5; costs are
  recomputed.  Merges and the resulting turnover change are allowed.
* ``random_entry`` keeps the run-length and flat-length multisets and each
  run's ``|w|`` profile, shuffles run/flat order with a seeded Fisher-Yates,
  draws a fresh sign per run, and recomputes costs.  Adjacent runs are forced
  opposite so the run-length multiset stays observable (as in D-40b).

This analogue has no funding, fill, latency or band model.  It is documented
here so the criterion-3 numbers are read as a synthetic sensitivity study, not
as a reproduction of a real E-001 null run.  With ``NULL_REPS=99`` the 1% gate
cutoff is estimated from only 99 nulls; it is noisier and slightly optimistic
(typically below) the config's 2000-rep cutoff, not conservative.

Benchmark
---------
For every scenario except ``correlated`` the beta-adjusted-alpha benchmark is
the same path's **raw buy-and-hold proxy**.  This is not the D-40 equal-risk
buy-and-hold baseline (no rebalancing, risk budget, costs, band or ledger).
``correlated`` uses a synthetic benchmark that shares 50% of the asset's noise
common factor and has no planted drift, so criterion 2 controls for that shared
factor rather than for the asset itself.

Approximations and scope
------------------------
The strategy is a raw equal-weight ensemble of the four MA rules.  It has no
volatility targeting, no rebalance band, no D-39 zero-crossing rule, no
sequential ledger, and no funding, fill, slippage or latency model.  Criterion
4 components are singleton MA net-PnL sums and criterion 5 uses the same raw
strategy PnL.  Consequently this study measures **gate discrimination under
those approximations**, not E-001 joint power on the production pipeline.

Limitation
----------
40 independent trials per scenario, 900 daily bars (699 decision returns after
a 200-bar warmup), one local process.  The 95% intervals are exact for the
sampled trials but say nothing beyond them; do not extrapolate these pass rates
to another sample size, period, universe or cost model.  Full-size runs are
out of scope (owner cancelled the Modal run).
"""

import math
from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from statsmodels.stats.proportion import proportion_confint

from . import gates, subperiods
from .stats import beta_adjusted_alpha, deflated_sharpe

DAYS = 900
MA_WARMUP = 200
MA_PERIODS = (20, 50, 100, 200)

N_TRIALS = 40
DEFAULT_SEED = 20261026
NULL_REPS = 99
HAC_MAXLAGS = 5
DSR_N_TRIALS_EFF = 4
DSR_TRIAL_SR_VARIANCE = 1e-4

# Mirror config/tycho.toml (stats/signal) and are passed explicitly to the gate.
DSR_THRESHOLD = 0.95
ALPHA_LEVEL = 0.90
NULL_TOP_FRACTION = 0.01

DEV_CRITERIA = ("dsr", "alpha", "null_top1", "ma_components", "subperiods")

SCENARIOS = {
    "zero": {
        "planted_sharpe": 0.0,
        "annual_vol": 0.60,
        "regime_switch_prob": 0.05,
        "cost_bps": 5.0,
        "t_df": None,
        "benchmark_correlation": 1.0,
    },
    "weak": {
        "planted_sharpe": 1.0,
        "annual_vol": 0.60,
        "regime_switch_prob": 0.05,
        "cost_bps": 5.0,
        "t_df": None,
        "benchmark_correlation": 1.0,
    },
    "strong": {
        "planted_sharpe": 6.0,
        "annual_vol": 0.60,
        "regime_switch_prob": 0.05,
        "cost_bps": 5.0,
        "t_df": None,
        "benchmark_correlation": 1.0,
    },
    "cost_heavy": {
        "planted_sharpe": 6.0,
        "annual_vol": 0.60,
        "regime_switch_prob": 0.05,
        "cost_bps": 100.0,
        "t_df": None,
        "benchmark_correlation": 1.0,
    },
    "fat_tailed": {
        "planted_sharpe": 6.0,
        "annual_vol": 0.60,
        "regime_switch_prob": 0.05,
        "cost_bps": 5.0,
        "t_df": 3.0,
        "benchmark_correlation": 1.0,
    },
    "correlated": {
        "planted_sharpe": 6.0,
        "annual_vol": 0.60,
        "regime_switch_prob": 0.05,
        "cost_bps": 5.0,
        "t_df": None,
        "benchmark_correlation": 0.50,
    },
}
SCENARIO_ORDER = tuple(SCENARIOS)

_SYNTHETIC_INPUTS = {
    "kind": "SYNTHETIC_LOCAL_POWER",
    "dsr_n_trials_eff": DSR_N_TRIALS_EFF,
    "dsr_trial_sharpe_variance": DSR_TRIAL_SR_VARIANCE,
    "hac_maxlags": HAC_MAXLAGS,
    "null_reps": NULL_REPS,
    "null_reps_note": (
        "99 reps is a study knob; fewer than config 2000, so the 1% cutoff "
        "is noisier and slightly optimistic"
    ),
    "units": (
        "planted_sharpe and realized strategy Sharpe are annualized "
        "(365-day); daily mean = planted_sharpe * daily_vol / sqrt(365)"
    ),
    "planted_sharpe_definition": (
        "annualized Sharpe of the latent state-conditional drift (oracle), "
        "not the realized MA-ensemble Sharpe"
    ),
    "scope": (
        "gate discrimination under synthetic approximations, not E-001 joint power"
    ),
    "approximations": (
        "raw MA ensemble; no vol targeting, band, D-39 rule, ledger, funding, "
        "fills, slippage or latency; criterion 2 benchmark is a raw buy-and-hold "
        "proxy, not the D-40 equal-risk buy-and-hold baseline"
    ),
    "null_analogue": (
        "D-40 block-sign scramble + run-length random entry on the combined "
        "MA weights; costs recomputed as bps*turnover, no ledger"
    ),
}


def _daily_drift(planted_sharpe, annual_vol):
    """Daily mean return for a declared annualized Sharpe at a given annual vol."""
    daily_vol = annual_vol / math.sqrt(365.0)
    return planted_sharpe * daily_vol / math.sqrt(365.0)


def _daily_returns(
    rng, *, planted_sharpe, annual_vol, regime_switch_prob, t_df, benchmark_correlation
):
    daily_vol = annual_vol / math.sqrt(365.0)
    if t_df is None:
        common = rng.standard_normal(DAYS)
        idio = rng.standard_normal(DAYS)
        bench_idio = rng.standard_normal(DAYS)
    else:
        scale = math.sqrt(t_df / (t_df - 2.0))
        common = rng.standard_t(t_df, DAYS) / scale
        idio = rng.standard_t(t_df, DAYS) / scale
        bench_idio = rng.standard_t(t_df, DAYS) / scale

    state = np.empty(DAYS, dtype=float)
    state[0] = 1.0 if rng.integers(0, 2) else -1.0
    for t in range(1, DAYS):
        state[t] = -state[t - 1] if rng.random() < regime_switch_prob else state[t - 1]

    root = math.sqrt(benchmark_correlation)
    resid = math.sqrt(1.0 - benchmark_correlation)
    returns = (
        _daily_drift(planted_sharpe, annual_vol) * state
        + daily_vol * (root * common + resid * idio)
    )
    if benchmark_correlation == 1.0:
        benchmark = returns
    else:
        benchmark = daily_vol * (root * common + resid * bench_idio)
    return returns, benchmark, state


def _ma_positions(close, period):
    ma = pd.Series(close).rolling(period).mean().to_numpy()
    raw = np.sign(close - ma)
    raw[~np.isfinite(ma)] = 0.0
    # Signal known at bar t; _pnl applies it to the next bar's return.
    return raw


def _pnl(positions, returns, cost_rate):
    # prev[t] is the position set at t-1, so pnl[t] earns return t without
    # using any information from bar t or later.
    prev = np.r_[0.0, positions[:-1]]
    prev_prev = np.r_[0.0, prev[:-1]]
    return prev * returns - cost_rate * np.abs(prev - prev_prev)


def _annualized_sharpe(values):
    values = np.asarray(values, dtype=float)
    sd = values.std(ddof=1)
    if sd == 0.0:
        return float("nan")
    return float(values.mean() / sd * math.sqrt(365.0))


def _runs(positions):
    signs = np.sign(positions)
    starts = np.r_[0, np.flatnonzero(np.diff(signs) != 0) + 1]
    ends = np.r_[starts[1:], len(signs)]
    return [(int(a), int(b), int(signs[a])) for a, b in zip(starts, ends)]


def _scramble(positions, rng):
    out = np.zeros_like(positions)
    for start, end, sign in _runs(positions):
        draw = 0 if sign == 0 else (1 if rng.integers(0, 2) else -1)
        out[start:end] = draw * np.abs(positions[start:end])
    return out


def _random_entry(positions, rng):
    blocks = _runs(positions)
    runs = [block for block in blocks if block[2] != 0]
    flats = [block for block in blocks if block[2] == 0]
    run_order = rng.permutation(len(runs))
    flat_order = rng.permutation(len(flats))
    run_i = flat_i = cursor = 0
    out = np.zeros_like(positions)
    previous_run = False
    previous_sign = 0
    for _, _, sign in blocks:
        if sign != 0:
            source = runs[run_order[run_i]]
            run_i += 1
            length = source[1] - source[0]
            draw = 1 if rng.integers(0, 2) else -1
            if previous_run and draw == previous_sign:
                draw = -draw
            out[cursor:cursor + length] = draw * np.abs(
                positions[source[0]:source[1]]
            )
            previous_run, previous_sign = True, draw
        else:
            source = flats[flat_order[flat_i]]
            flat_i += 1
            length = source[1] - source[0]
            out[cursor:cursor + length] = 0.0
            previous_run, previous_sign = False, 0
        cursor += length
    if cursor != len(positions):
        raise AssertionError("random entry lost days")
    return out


def _null_metrics(positions, returns, cost_rate, decisions, seed, null_reps):
    seed_sequence = (
        seed
        if isinstance(seed, np.random.SeedSequence)
        else np.random.SeedSequence(seed)
    )
    children = seed_sequence.spawn(2)
    out = {}
    for name, generator, child in (
        ("scramble", _scramble, children[0]),
        ("random_entry", _random_entry, children[1]),
    ):
        rng = np.random.default_rng(child)
        out[name] = [
            _annualized_sharpe(
                _pnl(generator(positions, rng), returns, cost_rate)[decisions]
            )
            for _ in range(null_reps)
        ]
    return out


def run_trial(scenario, *, seed, null_reps=NULL_REPS):
    """Run one independent synthetic trial and return its gate evidence."""
    params = SCENARIOS[scenario] if isinstance(scenario, str) else scenario
    rng = np.random.default_rng(seed)
    returns, benchmark, _state = _daily_returns(
        rng,
        planted_sharpe=params["planted_sharpe"],
        annual_vol=params["annual_vol"],
        regime_switch_prob=params["regime_switch_prob"],
        t_df=params["t_df"],
        benchmark_correlation=params["benchmark_correlation"],
    )
    close = 100.0 * np.exp(np.cumsum(returns))
    cost_rate = params["cost_bps"] / 10000.0

    components = {period: _ma_positions(close, period) for period in MA_PERIODS}
    combined = np.mean([components[period] for period in MA_PERIODS], axis=0)

    # Decision at t uses the bar at t; the return is realized from t to t+1.
    decision_idx = np.arange(MA_WARMUP, DAYS - 1)
    strategy = _pnl(combined, returns, cost_rate)[decision_idx + 1]
    bench = np.expm1(benchmark)[decision_idx + 1]
    component_net_pnl = {}
    for period in MA_PERIODS:
        component = _pnl(components[period], returns, cost_rate)[decision_idx + 1]
        component_net_pnl[period] = float(component.sum())

    labels = pd.Series(pd.NA, index=decision_idx, dtype="string")
    for t in decision_idx:
        window = close[t - MA_WARMUP + 1:t + 1]
        difference = window[-1] - window.mean()
        labels.loc[t] = "up" if difference > 0 else "down" if difference < 0 else "flat"
    subperiod = subperiods.net_by_subperiod(
        pd.Series(strategy, index=decision_idx), labels
    )

    per_period_sharpe = float(strategy.mean() / strategy.std(ddof=1))
    dsr = deflated_sharpe(
        per_period_sharpe,
        len(strategy),
        float(scipy_stats.skew(strategy)),
        float(scipy_stats.kurtosis(strategy, fisher=False)),
        DSR_N_TRIALS_EFF,
        DSR_TRIAL_SR_VARIANCE,
    )
    alpha = beta_adjusted_alpha(strategy, bench, HAC_MAXLAGS)
    metric = _annualized_sharpe(strategy)
    nulls = _null_metrics(
        combined, returns, cost_rate, decision_idx + 1, seed, null_reps
    )

    report = gates.evaluate(
        dsr=dsr["dsr"],
        alpha=alpha,
        strategy_metric=metric,
        null_metrics=nulls,
        component_net_pnl=component_net_pnl,
        component_periods=list(MA_PERIODS),
        subperiod_net_pnl=subperiod,
        # Criterion 6 is owner-only: no attestation is ever invented here.
        holdout_attestation=None,
        dsr_threshold=DSR_THRESHOLD,
        alpha_level=ALPHA_LEVEL,
        null_top_fraction=NULL_TOP_FRACTION,
        null_reps=null_reps,
    )
    statuses = {name: item["status"] for name, item in report["criteria"].items()}
    return {
        "statuses": statuses,
        "verdict": report["verdict"],
        "dev_pass": all(statuses[name] == gates.PASS for name in DEV_CRITERIA),
        "strategy_sharpe": metric,
        "dsr": dsr["dsr"],
        "alpha": alpha["alpha"],
        "alpha_p_one_sided": alpha["p_one_sided"],
    }


def _binomial_ci(passes, trials):
    low, high = proportion_confint(passes, trials, alpha=0.05, method="beta")
    return [float(low), float(high)]


def run_study(
    *, seed=DEFAULT_SEED, trials=N_TRIALS, scenarios=None, null_reps=NULL_REPS
):
    """Run the fixed small synthetic study; deterministic for a given seed."""
    if type(trials) is not int or trials < 1:
        raise ValueError("trials must be a positive integer")
    names = list(SCENARIO_ORDER if scenarios is None else scenarios)
    unknown = sorted(set(names) - set(SCENARIOS))
    if unknown:
        raise ValueError(f"unknown scenarios: {unknown}")

    result = {
        "kind": "SYNTHETIC_LOCAL_JOINT_POWER",
        "synthetic": True,
        "seed": seed,
        "n_days": DAYS,
        "ma_warmup": MA_WARMUP,
        "n_trials_per_scenario": trials,
        "null_reps": null_reps,
        "thresholds": {
            "dsr": DSR_THRESHOLD,
            "alpha_level": ALPHA_LEVEL,
            "null_top_fraction": NULL_TOP_FRACTION,
            "component_periods": list(MA_PERIODS),
        },
        "synthetic_inputs": {**_SYNTHETIC_INPUTS, "null_reps": null_reps},
        "scope": _SYNTHETIC_INPUTS["scope"],
        "approximations": _SYNTHETIC_INPUTS["approximations"],
        "pass_definition": "criteria 1-5 PASS; criterion 6 holdout is never simulated",
        "scenarios": {},
    }

    for name in names:
        records = [
            run_trial(
                name,
                # Common random numbers across scenarios: same path for trial i,
                # so scenario differences come from the planted parameters, not
                # from a different draw.
                seed=np.random.SeedSequence([seed, trial_index]),
                null_reps=null_reps,
            )
            for trial_index in range(trials)
        ]
        statuses = [record["statuses"] for record in records]
        passes = sum(record["dev_pass"] for record in records)
        result["scenarios"][name] = {
            "synthetic_parameters": {
                **SCENARIOS[name],
                "label": (
                    "explicit synthetic scenario value; "
                    "not a monograph PENDING value"
                ),
            },
            "n": trials,
            "passes": passes,
            "pass_rate": passes / trials,
            "ci95": _binomial_ci(passes, trials),
            "criterion_pass_counts": {
                criterion: sum(status[criterion] == gates.PASS for status in statuses)
                for criterion in (*DEV_CRITERIA, "holdout")
            },
            "holdout_status_counts": dict(
                Counter(status["holdout"] for status in statuses)
            ),
            "verdict_counts": dict(Counter(record["verdict"] for record in records)),
            "mean_realized_annualized_strategy_sharpe": float(
                np.mean([record["strategy_sharpe"] for record in records])
            ),
        }

    if "zero" in result["scenarios"]:
        result["false_positive_rate"] = result["scenarios"]["zero"]["pass_rate"]
    if "strong" in result["scenarios"]:
        result["false_negative_rate"] = 1.0 - result["scenarios"]["strong"]["pass_rate"]
    result["limitation"] = (
        f"{trials} independent seeded trials per scenario, {DAYS - MA_WARMUP - 1} "
        "decision returns per trial, local CPU only. Exact Clopper-Pearson "
        "intervals describe only the sampled trials; no extrapolation beyond "
        "this sample, period, universe or cost model."
    )
    return result


def report(summary):
    """Render a compact text report (the PR evidence)."""
    lines = [
        "SYNTHETIC LOCAL JOINT-POWER STUDY (criteria 1-5; criterion 6 owner-only)",
        f"seed={summary['seed']}  n_per_scenario={summary['n_trials_per_scenario']}  "
        f"days={summary['n_days']}  null_reps={summary['null_reps']}",
        "thresholds: "
        + ", ".join(f"{key}={value}" for key, value in summary["thresholds"].items()),
        "synthetic inputs (not monograph values): "
        + ", ".join(
            f"{key}={value}"
            for key, value in summary["synthetic_inputs"].items()
            if key
            in {
                "dsr_n_trials_eff",
                "dsr_trial_sharpe_variance",
                "hac_maxlags",
                "null_reps",
            }
        ),
        "",
        f"{'scenario':<14} {'n':>3} {'pass':>5} {'rate':>6} {'95% CI':>16} "
        f"{'realized SR (ann.)':>18} {'holdout':>8}",
    ]
    for name in SCENARIO_ORDER:
        if name not in summary["scenarios"]:
            continue
        item = summary["scenarios"][name]
        low, high = item["ci95"]
        lines.append(
            f"{name:<14} {item['n']:>3} {item['passes']:>5} "
            f"{item['pass_rate']:>6.3f} [{low:>5.3f}, {high:>5.3f}] "
            f"{item['mean_realized_annualized_strategy_sharpe']:>18.2f} "
            f"{str(item['holdout_status_counts']):>8}"
        )
    lines.append(f"units: {summary['synthetic_inputs']['units']}")
    lines.append(f"null reps: {summary['synthetic_inputs']['null_reps_note']}")
    lines.append(f"scope: {summary['scope']}")
    lines.append(f"approximations: {summary['approximations']}")
    if "false_positive_rate" in summary:
        lines.append(
            f"false-positive rate (zero edge): "
            f"{summary['false_positive_rate']:.3f}"
        )
    if "false_negative_rate" in summary:
        lines.append(
            f"false-negative rate (strong edge): "
            f"{summary['false_negative_rate']:.3f}"
        )
    lines.append(f"limitation: {summary['limitation']}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(run_study()))
