"""E-001 baselines and hand-written null generators (D-40).

Every null is built from the strategy's realized target-weight panel
``w[t, asset]``. Every null/baseline panel is executed by the same sequential
ledger as E-001 (``e001._simulate`` with the same ``prep`` and ``settings``), so
costs, funding, fills and latency are recomputed, never carried over.

Eligible days for an asset are the days it is in the point-in-time universe and
listed. A block is a maximal run of consecutive eligible days with the same sign
of ``w`` (+, -, 0); ineligible days are skipped by working on the compressed
eligible-day index.
"""

import numpy as np
import pandas as pd

from . import e001, trials
from .exp_trend import EXPERT_ID
from .portfolio import risk_budget

BTC = "BTCUSDT"
GROSS_CAP = 2.0


def eligible_days(base) -> pd.DataFrame:
    """PIT universe membership and a listed bar (the D-40 eligible days)."""
    return base["member"] & base["panels"]["close"].notna()


def blocks(w, eligible) -> list[dict]:
    """Per-asset maximal same-sign runs on the compressed eligible-day index."""
    _validate(w, eligible)
    out = []
    for asset in w.columns:
        positions = np.flatnonzero(eligible[asset].to_numpy(dtype=bool))
        if positions.size == 0:
            continue
        values = w[asset].to_numpy(dtype=float)[positions]
        signs = np.sign(values)
        starts = np.r_[0, np.flatnonzero(np.diff(signs) != 0) + 1]
        ends = np.r_[starts[1:], len(signs)]
        for start, end in zip(starts, ends):
            out.append({
                "asset": asset,
                "start": w.index[positions[start]],
                "end": w.index[positions[end - 1]],
                "sign": int(signs[start]),
                "length": int(end - start),
                "_start": int(start),
                "_end": int(end),
            })
    return out


def apply_gross_cap(weights, cap=GROSS_CAP):
    """Proportional date-wise gross-cap shrink; returns (panel, binding-day count)."""
    out = weights.astype(float).copy()
    gross = out.abs().sum(axis=1)
    binding = gross > cap
    if binding.any():
        out.loc[binding] = out.loc[binding].mul(cap / gross[binding], axis=0)
    return out, int(binding.sum())


def scramble(w, eligible, *, reps, seed):
    """Run-based direction scramble (D-40a).

    Preserved exactly: ``|w|`` for every (t, asset); date-wise gross; the original
    block boundaries (reported as metadata).
    Not preserved: realized holding period (merges allowed), net exposure path,
    cross-sectional sign correlation, turnover, costs (recomputed).

    Each non-flat block independently draws s in {+1, -1} with p=0.5; flat blocks
    stay flat. Neighbouring blocks may take the same sign and merge.
    """
    _check(reps, w, eligible)
    per_asset = _by_asset(blocks(w, eligible))
    rng = np.random.default_rng(seed)
    for _ in range(reps):
        frame = pd.DataFrame(0.0, index=w.index, columns=w.columns)
        metadata = []
        for asset in w.columns:
            positions = np.flatnonzero(eligible[asset].to_numpy(dtype=bool))
            if positions.size == 0:
                continue
            values = w[asset].to_numpy(dtype=float)
            null = np.zeros(len(w))
            for block in per_asset.get(asset, []):
                sign = 0
                if block["sign"] != 0:
                    sign = 1 if rng.integers(0, 2) else -1
                    segment = positions[block["_start"]:block["_end"]]
                    null[segment] = sign * np.abs(values[segment])
                metadata.append({key: value for key, value in {
                    **block, "scramble_sign": sign,
                }.items() if not key.startswith("_")})
            frame[asset] = null
        yield {"weights": frame, "blocks": metadata}


def random_entry(w, eligible, *, reps, seed, gross_cap=GROSS_CAP):
    """Run-preserving random entry (D-40b).

    Preserved exactly: per-asset run-length multiset; per-asset flat-length
    multiset; pre-cap per-asset total ``|w|``; eligibility (no position on an
    ineligible day).
    Not preserved: date-wise cross-sectional gross, net exposure path,
    cross-sectional correlation, turnover, costs (recomputed).

    Runs and flats are separated and each list is shuffled with a seeded
    Fisher-Yates permutation; the shuffled blocks are rebuilt on the compressed
    eligible-day index in the original run/flat alternation, each run keeping its
    own ``|w|`` profile and drawing a sign. Where two runs are adjacent (no flat
    between them, a sign change in the original), the second sign is forced
    opposite so that the preserved run-length multiset stays observable. The
    result is mapped back to calendar days and the strategy's gross cap (2.0) is
    applied with a proportional shrink; the volatility cap is not reapplied.
    ``metadata`` reports ``cap_bind_days`` for the repetition.
    """
    _check(reps, w, eligible)
    per_asset = _by_asset(blocks(w, eligible))
    rng = np.random.default_rng(seed)
    for _ in range(reps):
        frame = pd.DataFrame(0.0, index=w.index, columns=w.columns)
        pre_cap_total = {}
        for asset in w.columns:
            positions = np.flatnonzero(eligible[asset].to_numpy(dtype=bool))
            if positions.size == 0:
                continue
            values = w[asset].to_numpy(dtype=float)
            asset_blocks = per_asset.get(asset, [])
            runs = [b for b in asset_blocks if b["sign"] != 0]
            flats = [b for b in asset_blocks if b["sign"] == 0]
            runs = [runs[i] for i in rng.permutation(len(runs))]
            flats = [flats[i] for i in rng.permutation(len(flats))]
            null = np.zeros(len(positions))
            cursor = run_i = flat_i = 0
            previous_was_run = False
            previous_sign = 0
            for block in asset_blocks:
                if block["sign"] != 0:
                    source = runs[run_i]
                    run_i += 1
                    sign = 1 if rng.integers(0, 2) else -1
                    # Adjacent runs must stay opposite or their lengths would merge
                    # and the run-length multiset would no longer be observable.
                    if previous_was_run and sign == previous_sign:
                        sign = -sign
                    segment = positions[source["_start"]:source["_end"]]
                    values_segment = np.abs(values[segment])
                    null[cursor:cursor + source["length"]] = sign * values_segment
                    previous_was_run, previous_sign = True, sign
                else:
                    source = flats[flat_i]
                    flat_i += 1
                    null[cursor:cursor + source["length"]] = 0.0
                    previous_was_run, previous_sign = False, 0
                cursor += source["length"]
            if cursor != len(positions):
                raise AssertionError("random entry lost compressed eligible days")
            frame.loc[frame.index[positions], asset] = null
            pre_cap_total[asset] = float(np.abs(null).sum())
        capped, binds = apply_gross_cap(frame, gross_cap)
        yield {
            "weights": capped,
            "pre_cap_total_abs": pre_cap_total,
            "cap_bind_days": binds,
        }


def no_trade(base) -> dict:
    """D-40e: zero weights; the sequential ledger leaves equity constant."""
    weights = pd.DataFrame(0.0, index=base["targets"].index, columns=base["targets"].columns)
    return {"weights": weights}


def equal_risk_buy_and_hold(tape, params, *, synthetic: bool = True, base=None) -> dict:
    """D-40c: E-001 pipeline with the expert score fixed to +1.

    Data-driven ABSTAIN (missing/stale data) still applies; universe, b, EWMA
    sigma, caps, band, D-39, costs and ledger are the main run's.
    """
    base = _base(tape, params, synthetic, base)
    score = base["panels"]["score"].notna().astype(float)
    targets, _, _, used = e001.size_targets(
        tape, base["settings"], base["panels"], base["universe"], score=score
    )
    return {
        "weights": targets,
        "score": used,
        "b": risk_budget(
            base["settings"]["target_vol"], base["settings"]["universe_size"],
            base["settings"]["rho"],
        ),
        "base": base,
    }


def trend_following_btc(tape, params, *, synthetic: bool = True, base=None) -> dict:
    """D-40d: E-001 pipeline with universe={BTC}; b via C-04 with N=1 (b=target vol)."""
    base = _base(tape, params, synthetic, base)
    if BTC not in base["universe"].columns:
        raise ValueError(f"{BTC} is not in the tape universe")
    mask = pd.DataFrame(False, index=base["universe"].index, columns=base["universe"].columns)
    mask[BTC] = True
    targets, _, _, _ = e001.size_targets(
        tape, base["settings"], base["panels"], base["universe"] & mask, n=1
    )
    return {
        "weights": targets,
        "b": risk_budget(base["settings"]["target_vol"], 1, base["settings"]["rho"]),
        "base": base,
    }


def run_baselines(tape, params, *, synthetic: bool = True, base=None) -> dict:
    """Run (c), (d), (e) through the sequential ledger; append one trial each."""
    base = _base(tape, params, synthetic, base)
    buy_hold = equal_risk_buy_and_hold(tape, params, synthetic=synthetic, base=base)
    btc = trend_following_btc(tape, params, synthetic=synthetic, base=base)
    items = (
        ("no_trade", no_trade(base)["weights"], {}),
        ("equal_risk_buy_and_hold", buy_hold["weights"], {"b": buy_hold["b"]}),
        ("trend_following_btc", btc["weights"], {"b": btc["b"]}),
    )
    out = {}
    for label, weights, extra in items:
        run = e001._simulate(base["prep"], weights, base["settings"])
        record = _record(
            tape, base, run, role="baseline", variant=f"baseline:{label}",
            seed=tape.meta.get("seed"), extra=extra,
        )
        out[label] = {"metrics": run["metrics"], "trial_hash": record["hash"], **extra}
    return out


def run_nulls(tape, params, *, reps, seed, synthetic: bool = True, base=None) -> dict:
    """Run both D-40 null families through the ledger; append one trial per repetition."""
    base = _base(tape, params, synthetic, base)
    w = base["targets"]
    eligible = eligible_days(base)
    child = np.random.SeedSequence(seed).spawn(2)
    out = {}
    for label, generator, child_seed in (
        ("scramble", scramble, child[0]),
        ("random_entry", random_entry, child[1]),
    ):
        hashes, binds = [], 0
        for rep, item in enumerate(generator(
            w, eligible, reps=reps, seed=child_seed,
            **({"gross_cap": base["settings"]["gross_leverage_cap"]}
               if label == "random_entry" else {}),
        )):
            run = e001._simulate(base["prep"], item["weights"], base["settings"])
            record = _record(
                tape, base, run, role="null", variant=f"null:{label}:{rep}",
                seed=seed, extra={"null_kind": label, "repetition": rep},
            )
            hashes.append(record["hash"])
            binds += item.get("cap_bind_days", 0)
        out[label] = {"reps": reps, "seed": seed, "trial_hashes": hashes}
        if label == "random_entry":
            out[label]["gross_cap_bind_count"] = binds
    return out


def _base(tape, params, synthetic, base):
    return e001.build_experiment(tape, params, synthetic) if base is None else base


def _record(tape, base, run, *, role, variant, seed, extra):
    settings = base["settings"]
    return trials.append_trial(
        settings["trials_path"],
        {
            "kind": "SYNTHETIC",
            "expert": EXPERT_ID,
            "variant": variant,
            "role": role,
            "execution_policy": settings["synthetic_execution_policy"],
            "params_hash": settings["params_hash"],
            "git_head": e001._git_head(),
            "dataset_hash": e001._dataset_hash(tape),
            "seed": seed,
            "metrics": run["metrics"],
            "dsr_neff_membership": "PENDING",
            **extra,
        },
    )


def _by_asset(items):
    out = {}
    for item in items:
        out.setdefault(item["asset"], []).append(item)
    return out


def _check(reps, w, eligible):
    if type(reps) is not int or reps < 1:
        raise ValueError("reps must be a positive integer")
    _validate(w, eligible)


def _validate(w, eligible):
    if not isinstance(w, pd.DataFrame) or not isinstance(eligible, pd.DataFrame):
        raise ValueError("w and eligible must be DataFrames")
    if not w.index.equals(eligible.index) or not w.columns.equals(eligible.columns):
        raise ValueError("w and eligible must share index and columns")
    mask = eligible.to_numpy(dtype=bool)
    values = w.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("w must be finite")
    if np.any(values[~mask] != 0.0):
        raise ValueError("realized weights must be flat outside eligible days")
