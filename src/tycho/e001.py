"""E-001 DEV runner on the synthetic tape, with a hash-chained trial log (§14, §19).

The sequential ledger (D-22) is authoritative: each day's events are replayed on a
warm-started ledger whose synthetic opening fills reproduce the previous close's
positions and average entry prices exactly (zero fee, zero slippage), so the daily
chain equals one uninterrupted run_ledger over the whole event stream.

shortcut: daily bars only; the fill uses the next bar's open because the first
executable price after the PENDING latency needs an intraday tape. Upgrade when
an intraday tape exists.
"""

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, scanner, trials
from .contracts import SnapshotRef, canonical_hash, frame_hash
from .exp_trend import EXPERT_ID
from .factors import ewma_cov
from .ledger import Instrument, rebalance_orders, run_ledger, slippage_bps
from .marketstate import MarketStateProvider
from .synthetic import make_synthetic_tape
from .tape import TABLE_COLUMNS
from .vectorized import trend_panels, universe_panel, weights_panel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TRIALS = _REPO_ROOT / "ledger" / "trials.jsonl"
_CLOSE_OFFSET = pd.Timedelta(hours=23, minutes=59)
_WARM_OFFSET = pd.Timedelta(hours=12)


def run_e001(tape, params, synthetic: bool) -> dict:
    """Run the E-001 signal through the authoritative ledger; append one trial per variant."""
    settings = _settings(params, synthetic)
    panels = trend_panels(tape, settings["ma_periods_days"], settings["ewma_convention"])
    dates = panels["score"].index
    provider = MarketStateProvider(
        tape, settings["universe_size"], settings["min_history_days"]
    )
    universe = universe_panel(provider, dates)
    bars = _fixed_view(tape).bars_1d.sort_values(
        ["instrument_id", "event_time"], kind="stable"
    )
    targets = weights_panel(
        panels,
        universe,
        _cov_fn(bars, settings["ewma_convention"]),
        settings["target_vol"],
        settings["rho"],
        settings["universe_size"],
        settings["vol_cap_multiple"],
        settings["gross_leverage_cap"],
    )
    member = universe.reindex(
        index=panels["score"].index, columns=panels["score"].columns, fill_value=False
    ).astype(bool)
    # max_staleness: a row whose last close is older than the limit trades flat.
    limit = int(pd.Timedelta(settings["max_staleness"]) / pd.Timedelta(days=1))
    stale = ~panels["close"].ffill(limit=limit).notna()
    targets = targets.mask(stale, 0.0)
    score = panels["score"].where(member, 0.0).fillna(0.0).mask(stale, 0.0)
    constant = score * settings["constant_notional_weight"]

    prep = _prepare(tape, dates, panels)
    results = {}
    runs = {}
    records = []
    for variant, weights in (("main", targets), ("constant_notional", constant)):
        run = runs[variant] = _simulate(prep, weights, settings)
        results[variant] = run["metrics"]
        records.append(
            trials.append_trial(
                settings["trials_path"],
                {
                    "kind": "SYNTHETIC",
                    "expert": EXPERT_ID,
                    "variant": variant,
                    "params_hash": settings["params_hash"],
                    "git_head": _git_head(),
                    "dataset_hash": _dataset_hash(tape),
                    "seed": tape.meta.get("seed"),
                    "metrics": run["metrics"],
                },
            )
        )
    return {
        "kind": "SYNTHETIC",
        "params_hash": settings["params_hash"],
        "git_head": records[0]["git_head"],
        "dataset_hash": records[0]["dataset_hash"],
        "metrics": results,
        "scanner_max_abs_diff": runs["main"]["scanner_max_abs_diff"],
        "identity_max_abs_error": max(
            run["identity_max_abs_error"] for run in runs.values()
        ),
        "trial_hashes": [record["hash"] for record in records],
        "trials_path": str(settings["trials_path"]),
    }


def _settings(params, synthetic: bool) -> dict:
    cfg = config.load()
    if not synthetic:
        # Real runs stay refused until the owner fills every PENDING value.
        for value, name in (
            (cfg["periods"]["dev_end_date"], "dev_end_date"),
            (cfg["execution"]["research_nominal_usd"], "research_nominal_usd"),
            (cfg["execution"]["absolute_band_nav"], "absolute_band_nav"),
            (cfg["execution"]["partial_trading_ratio"], "partial_trading_ratio"),
            (cfg["sizing"]["ewma_convention"], "ewma_convention"),
            (cfg["execution"]["executable_latency"], "executable_latency"),
        ):
            config.require(value, name)
        raise ValueError("only synthetic mode is implemented; real data stays BLOCKED")

    def pick(key, default):
        return params[key] if key in params else default

    universe_size = int(pick("universe_size", cfg["universe"]["size"]))
    settings = {
        "ewma_convention": _explicit(params, "ewma_convention"),
        "max_staleness": str(pd.Timedelta(_explicit(params, "max_staleness"))),
        "absolute_band_nav": float(_explicit(params, "absolute_band_nav")),
        "partial_ratio": float(_explicit(params, "partial_ratio")),
        "research_nominal_usd": float(_explicit(params, "research_nominal_usd")),
        "ma_periods_days": [int(k) for k in pick(
            "ma_periods_days", cfg["signal"]["ma_periods_days"]
        )],
        "target_vol": float(pick("target_vol", cfg["sizing"]["target_vol"])),
        "rho": float(pick("rho", cfg["sizing"]["rho"])),
        "vol_cap_multiple": float(
            pick("vol_cap_multiple", cfg["sizing"]["vol_cap_multiple"])
        ),
        "gross_leverage_cap": float(
            pick("gross_leverage_cap", cfg["sizing"]["gross_leverage_cap"])
        ),
        "taker_fee": float(pick("taker_fee", cfg["execution"]["taker_fee"])),
        "slippage_min_bps": float(
            pick("slippage_min_bps", cfg["execution"]["slippage_min_bps"])
        ),
        "slippage_y": float(pick("slippage_y", cfg["execution"]["slippage_y"])),
        "band_relative": float(
            pick("band_relative", cfg["execution"]["band_relative"])
        ),
        "delist_exit_slippage_bps": float(
            pick("delist_exit_slippage_bps", cfg["universe"]["delist_exit_slippage_bps"])
        ),
        "universe_size": universe_size,
        "min_history_days": int(
            pick("min_history_days", cfg["universe"]["min_history_days"])
        ),
        # Constant-notional diagnostic: an equal 1/N book, no risk scaling.
        "constant_notional_weight": 1.0 / universe_size,
        "trials_path": Path(pick("trials_path", _DEFAULT_TRIALS)),
    }
    payload = {k: v for k, v in settings.items() if k != "trials_path"}
    settings["params_hash"] = canonical_hash(payload)
    return settings


def _explicit(params, key):
    value = params.get(key)
    if value is None or value == "" or value == "PENDING":
        raise config.PendingParameterError(f"{key} is PENDING")
    return value


def _fixed_view(tape):
    return tape.as_of(tape.bars_1d["available_at"].max())


def _cov_fn(bars, convention):
    """ewma_cov over bars stamped at or before the decision date.

    shortcut: revisions are applied from their bar stamp, the same shortcut
    trend_panels documents; PIT revision replay needs an as-of bar store.
    """

    def cov(date, active):
        active = list(active)
        if not active:
            return pd.DataFrame(index=active, columns=active, dtype=float)
        subset = bars[(bars["event_time"] <= date) & bars["instrument_id"].isin(active)]
        snapshot = SnapshotRef(
            snapshot_id="panel",
            as_of=date,
            dataset_hash="",
            universe_hash="",
            request_hash="",
            universe=tuple(active),
            bars=subset.reset_index(drop=True),
            availability=pd.DataFrame(index=pd.Index(active, name="instrument_id")),
        )
        return ewma_cov(snapshot, active, convention)

    return cov


def _prepare(tape, dates, panels) -> dict:
    view = _fixed_view(tape)
    bars = view.bars_1d.sort_values(["instrument_id", "event_time"], kind="stable")
    adv = bars.groupby("instrument_id", sort=False)["quote_volume"].transform(
        lambda volume: volume.rolling(30).mean()
    )
    bars = bars.assign(adv=adv)
    prep = {
        "dates": dates,
        "opens": _wide(bars, "open"),
        "closes": panels["close"],
        "marks": _wide(view.mark, "mark_price"),
        "adv": _wide(bars, "adv"),
        "daily_vol": panels["vol"] / np.sqrt(365.0),
        "instruments": _instruments(view.specs),
        "funding": _funding_by_day(view.funding, dates),
        "delists": _delists(view.instruments, dates),
    }
    return prep


def _wide(frame, column):
    return (
        frame.pivot(index="event_time", columns="instrument_id", values=column)
        .sort_index()
    )


def _instruments(specs):
    latest = specs.sort_values(["instrument_id", "valid_from", "revision"]).groupby(
        "instrument_id", sort=False
    ).tail(1)
    return {
        row.instrument_id: Instrument(
            row.instrument_id,
            float(row.contract_multiplier),
            float(row.step_size),
            float(row.min_notional),
        )
        for row in latest.itertuples()
    }


def _funding_by_day(funding, dates):
    funding = funding.sort_values("event_time")
    index = np.searchsorted(
        dates.to_numpy(), funding["event_time"].to_numpy(), side="right"
    ) - 1
    keep = (index >= 0) & (index < len(dates))
    rows = {}
    for i, row in zip(index[keep], funding[keep].itertuples()):
        rows.setdefault(int(i), []).append(
            (pd.Timestamp(row.event_time), row.instrument_id, float(row.rate))
        )
    return rows


def _delists(instruments, dates):
    latest = instruments.sort_values(["event_time", "revision"]).groupby(
        "instrument_id", sort=False
    ).tail(1)
    rows = []
    for row in latest.itertuples():
        if pd.isna(row.last_trade_day):
            continue
        position = dates.get_indexer([pd.Timestamp(row.last_trade_day)])[0]
        if position >= 0:
            rows.append((int(position), row.instrument_id))
    return rows


def _simulate(prep, targets, settings) -> dict:
    dates = prep["dates"]
    instruments = prep["instruments"]
    nominal = settings["research_nominal_usd"]
    wallet = nominal
    positions, entry, last_mark = {}, {}, {}
    orders = {}
    equity_series = [nominal]
    identity_error = 0.0
    fees_total = funding_total = traded_notional = 0.0
    trade_count = 0
    weights_rows, cost_rows, rate_rows = [], [], []

    for i, date in enumerate(dates):
        events = []
        # Recreate the previous close's book: a zero-cost fill at the average
        # entry price restores qty and entry exactly.
        for instrument_id, qty in positions.items():
            events.append(
                _fill(date - _WARM_OFFSET, instrument_id, qty, entry[instrument_id])
            )
        for timestamp, instrument_id, rate in prep["funding"].get(i, []):
            price = last_mark.get(instrument_id)
            if price is None:
                price = _value(prep["closes"], dates[i - 1], instrument_id) if i else None
            events.append(_funding(timestamp, instrument_id, rate, price))
        costs, rates = {}, {}
        for instrument_id, qty in orders.items():
            price = _value(prep["opens"], date, instrument_id)
            if price is None:
                continue
            inst = instruments[instrument_id]
            notional = abs(qty) * inst.contract_multiplier * price
            decision = dates[i - 1]
            daily_vol = _value(prep["daily_vol"], decision, instrument_id) or 0.0
            adv = _value(prep["adv"], decision, instrument_id)
            slip = (
                slippage_bps(
                    notional,
                    daily_vol,
                    adv,
                    y=settings["slippage_y"],
                    min_bps=settings["slippage_min_bps"],
                )
                if adv and adv > 0.0
                else settings["slippage_min_bps"]
            )
            events.append(
                _fill(date, instrument_id, qty, price, settings["taker_fee"], slip)
            )
            traded_notional += notional
            trade_count += 1
            costs[instrument_id] = settings["taker_fee"] + slip / 1e4
        for day, instrument_id in prep["delists"]:
            if day != i:
                continue
            mark = _value(prep["marks"], date, instrument_id)
            if mark is None:
                mark = last_mark.get(instrument_id)
            if mark is not None:
                events.append(
                    {
                        "time": (date + _CLOSE_OFFSET).isoformat(),
                        "type": "DELIST",
                        "instrument_id": instrument_id,
                        "mark_price": mark,
                        # E-001 delist rule = mark + 50 bps plus the taker fee
                        "fee_rate": settings["taker_fee"],
                        "slippage_bps": settings["delist_exit_slippage_bps"],
                    }
                )
        close_marks = {}
        for instrument_id in prep["marks"].columns:
            mark = _value(prep["marks"], date, instrument_id)
            if mark is not None:
                close_marks[instrument_id] = mark
                events.append(
                    {
                        "time": (date + _CLOSE_OFFSET).isoformat(),
                        "type": "MARK",
                        "instrument_id": instrument_id,
                        "mark_price": mark,
                    }
                )
        for timestamp, instrument_id, rate in prep["funding"].get(i, []):
            rates[instrument_id] = rates.get(instrument_id, 0.0) + rate

        opening_wallet = wallet
        snapshots = run_ledger(wallet, instruments, events) if events else []
        if snapshots:
            last = snapshots[-1]
            wallet = last["wallet"]
            positions = dict(last["positions"])
            entry = dict(last["entry"])
            fees_total += last["fees_cum"]
            funding_total += last["funding_cum"]
            equity = last["equity"]
            # Ledger identities, plus a cross-check against the marks the runner
            # emitted: a missing close MARK would break the rebuilt equity.
            marks_now = {**last_mark, **close_marks}
            rebuilt = wallet + sum(
                qty
                * instruments[instrument_id].contract_multiplier
                * (marks_now.get(instrument_id, entry[instrument_id])
                   - entry[instrument_id])
                for instrument_id, qty in positions.items()
            )
            identity_error = max(
                identity_error,
                abs(equity - (last["wallet"] + last["unrealized"])),
                abs(
                    last["wallet"]
                    - (opening_wallet + last["realized_cum"]
                       - last["fees_cum"] + last["funding_cum"])
                ),
                abs(rebuilt - equity),
            )
        else:
            equity = wallet
        last_mark.update(close_marks)
        equity_series.append(equity)
        weights_rows.append(
            {
                instrument_id: qty
                * instruments[instrument_id].contract_multiplier
                * (last_mark.get(instrument_id, entry[instrument_id]))
                / equity
                for instrument_id, qty in positions.items()
            }
        )
        cost_rows.append(costs)
        rate_rows.append(rates)

        if i + 1 < len(dates):
            # the ledger marks a FILL at its price, so entry is the fallback mark
            marks = {k: entry[k] for k in positions}
            marks.update(last_mark)
            marks.update(close_marks)
            target = {}
            for instrument_id, weight in targets.loc[date].items():
                # no mark at today's close means the instrument stopped trading
                if instrument_id in instruments and instrument_id in close_marks:
                    target[instrument_id] = float(weight)
            orders = rebalance_orders(
                target,
                positions,
                marks,
                equity,
                instruments,
                settings["band_relative"],
                settings["absolute_band_nav"],
                settings["partial_ratio"],
            )

    metrics = _metrics(
        equity_series, fees_total, funding_total, traded_notional, trade_count
    )
    return {
        "metrics": metrics,
        "identity_max_abs_error": identity_error,
        "scanner_max_abs_diff": _scanner_diff(
            prep, weights_rows, cost_rows, rate_rows, equity_series, settings
        ),
    }


def _scanner_diff(prep, weights_rows, cost_rows, rate_rows, equity_series, settings):
    """Max |scan daily pnl - ledger daily return|; parity is milestone M6, not asserted."""
    dates, closes = prep["dates"], prep["closes"]
    columns = sorted(set(closes.columns) | {k for row in weights_rows for k in row})
    days = len(dates) - 1
    if days < 2 or not columns:
        return 0.0
    index = {instrument_id: j for j, instrument_id in enumerate(columns)}
    close = closes.reindex(columns=columns)
    w = np.zeros((days, len(columns)))
    r = np.zeros_like(w)
    c = np.zeros_like(w)
    f = np.zeros_like(w)
    base_cost = settings["taker_fee"] + settings["slippage_min_bps"] / 1e4
    # scan period t is ledger day t: w[t] is the weight held at day t's close,
    # r[t] its close-to-close return, c[t]/f[t] that day's costs and funding.
    # Day 0 has no fill in either series, so the lengths line up directly.
    for t in range(days):
        for instrument_id, weight in weights_rows[t].items():
            w[t, index[instrument_id]] = weight
        for instrument_id in columns:
            j = index[instrument_id]
            value = _value(close, dates[t], instrument_id)
            previous = _value(close, dates[t - 1], instrument_id) if t else None
            if value is not None and previous:
                r[t, j] = value / previous - 1.0
            c[t, j] = cost_rows[t].get(instrument_id, base_cost)
            f[t, j] = rate_rows[t].get(instrument_id, 0.0)
    out = scanner.scan(w[None, :, :], r, c, f)
    returns = np.diff(np.asarray(equity_series, dtype=float)) / np.asarray(
        equity_series, dtype=float
    )[:-1]
    pnl = out["pnl"][0]
    ledger = returns[: len(pnl)]
    return float(np.max(np.abs(pnl - ledger))) if ledger.size else 0.0


def _metrics(equity_series, fees, funding, traded_notional, trade_count) -> dict:
    equity = np.asarray(equity_series, dtype=float)
    returns = equity[1:] / equity[:-1] - 1.0
    peak = np.maximum.accumulate(equity)
    values = {
        "sharpe": scanner.annualized_sharpe(returns, 365) if returns.size > 1 else np.nan,
        "total_return": float(equity[-1] / equity[0] - 1.0),
        "max_drawdown": float(np.max(1.0 - equity / peak)),
        "turnover": float(traded_notional / equity.mean()),
        "fees": float(fees),
        "funding": float(funding),
        "trade_count": int(trade_count),
        "days": int(len(equity) - 1),    }
    return {key: value if not isinstance(value, float) or np.isfinite(value) else None
            for key, value in values.items()}


def _value(panel, date, instrument_id):
    if date not in panel.index or instrument_id not in panel.columns:
        return None
    value = panel.at[date, instrument_id]
    return None if pd.isna(value) else float(value)


def _fill(time, instrument_id, qty, price, fee_rate=0.0, slippage_bps_value=0.0):
    return {
        "time": time.isoformat(),
        "type": "FILL",
        "instrument_id": instrument_id,
        "qty": qty,
        "price": price,
        "fee_rate": fee_rate,
        "slippage_bps": slippage_bps_value,
    }


def _funding(timestamp, instrument_id, rate, mark_price):
    # The tape's funding rows carry no price, so the position's latest mark
    # (the previous daily close for an overnight event) prices the cashflow.
    return {
        "time": timestamp.isoformat(),
        "type": "FUNDING",
        "instrument_id": instrument_id,
        "rate": rate,
        "mark_price": mark_price if mark_price is not None else 0.0,
    }


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


def _dataset_hash(tape) -> str:
    return canonical_hash(
        {name: frame_hash(getattr(tape, name)) for name in TABLE_COLUMNS}
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run E-001 on the synthetic tape (DEV).")
    parser.add_argument("--synthetic-seed", type=int, required=True)
    parser.add_argument("--params", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    params = json.loads(args.params.read_text())
    tape = make_synthetic_tape(seed=args.synthetic_seed)
    result = run_e001(tape, params, synthetic=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str)
    )
    print(json.dumps(result["metrics"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
