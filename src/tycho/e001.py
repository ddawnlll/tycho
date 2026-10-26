"""E-001 DEV runner on the synthetic tape, with a hash-chained trial log (§14, §19).

The sequential ledger (D-22) is authoritative: each day's events are replayed on a
warm-started ledger whose synthetic opening fills reproduce the previous close's
positions and average entry prices exactly (zero fee, zero slippage), so the daily
chain equals one uninterrupted run_ledger over the whole event stream.

Daily OHLC is not an executable-price tape. The opt-in
SYNTHETIC_PUBLISHED_DAILY_MARK fixture assumes a visible daily mark is executable
at that day's decision as_of, exactly 1D after the preceding decision. This is
not a production fill model or the 1h diagnostic; those remain blocked on #51.
"""

import argparse
import json
import subprocess
from dataclasses import asdict
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
from .tape import TABLE_COLUMNS, validate
from .vectorized import trend_panels, universe_panel, weights_panel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_TRIALS = _REPO_ROOT / "ledger" / "trials.jsonl"
_DAILY_MARK_FIXTURE = "SYNTHETIC_PUBLISHED_DAILY_MARK"


def run_e001(tape, params, synthetic: bool) -> dict:
    """Run the E-001 signal through the authoritative ledger; append one trial per variant."""
    settings = _settings(params, synthetic)
    if not tape.meta.get("synthetic") or any(
        not getattr(tape, name)["source"].eq("synthetic").all()
        for name in TABLE_COLUMNS
    ):
        raise ValueError("synthetic execution fixture requires a synthetic tape")
    validate(tape)
    panels = trend_panels(
        tape, settings["ma_periods_days"], settings["ewma_convention"],
        decision_lag=settings["decision_lag"],
    )
    dates = panels["score"].index
    provider = MarketStateProvider(
        tape, settings["universe_size"], settings["min_history_days"]
    )
    universe = universe_panel(provider, panels["as_of"])
    universe.index = dates
    targets = weights_panel(
        panels,
        universe,
        _cov_fn(tape, settings["ewma_convention"]),
        settings["target_vol"],
        settings["rho"],
        settings["universe_size"],
        settings["vol_cap_multiple"],
        settings["gross_leverage_cap"],
        on_abstain=settings["on_abstain"],
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
                    "execution_policy": settings["synthetic_execution_policy"],
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
        "execution_policy": settings["synthetic_execution_policy"],
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
    latency = pd.Timedelta(_explicit(params, "executable_latency"))
    if pd.isna(latency) or latency <= pd.Timedelta(0):
        raise ValueError("executable_latency must be strictly positive")
    # shortcut: this labelled fixture supports one daily step only; #51 needs an executable tape.
    if latency != pd.Timedelta(days=1):
        raise ValueError("daily fixture supports only 1D executable_latency; executable-price tape/schedule required (#51)")
    if params.get("synthetic_execution_policy") != _DAILY_MARK_FIXTURE:
        raise ValueError("daily tape has no executable prices; explicitly opt into the synthetic daily-mark fixture (#51)")
    settings = {
        "executable_latency": str(latency),
        "synthetic_execution_policy": _DAILY_MARK_FIXTURE,
        "ewma_convention": _explicit(params, "ewma_convention"),
        "decision_lag": str(pd.Timedelta(_explicit(params, "decision_lag"))),
        "on_abstain": _explicit(params, "on_abstain"),
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


def _cov_fn(tape, convention):
    """EWMA covariance on closed bars and revisions visible at decision as_of."""

    def cov(date, active):
        active = list(active)
        if not active:
            return pd.DataFrame(index=active, columns=active, dtype=float)
        bars = tape.as_of(date).bars_1d
        subset = bars[(bars["event_time"] < date) & bars["instrument_id"].isin(active)]
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
    prep = {
        "dates": dates,
        "as_of": panels["as_of"],
        "closes": panels["close"],
        "daily_vol": panels["vol"] / np.sqrt(365.0),
        "instruments": {}, "funding": {}, "delists": {},
    }
    marks, adv = {}, {}
    exact_marks = tape.mark[tape.mark["available_at"] <= tape.mark["event_time"]]
    exact_marks = exact_marks.sort_values("revision").drop_duplicates(
        ["instrument_id", "event_time"], keep="last"
    ).set_index(["instrument_id", "event_time"])["mark_price"].to_dict()
    for i, date in enumerate(dates):
        as_of = panels["as_of"].loc[date]
        view = tape.as_of(as_of)
        bars = view.bars_1d
        bars = bars[(bars["event_time"] <= date) & (bars["event_time"] < as_of)]
        bars = bars.sort_values(["instrument_id", "event_time"], kind="stable")
        trailing = bars[bars["event_time"] > date - pd.Timedelta(days=30)]
        adv[date] = trailing.groupby("instrument_id")["quote_volume"].agg(
            lambda volume: volume.mean() if len(volume) == 30 else np.nan
        )
        current_marks = view.mark[view.mark["event_time"] == date]
        marks[date] = current_marks.set_index("instrument_id")["mark_price"]
        prep["instruments"][i] = _instruments(view.specs, as_of)
        prep["delists"][i] = _delists(view.instruments, date)
        previous = panels["as_of"].iloc[i - 1] if i else date - pd.Timedelta(days=1)
        funding = view.funding[
            (view.funding["event_time"] > previous) & (view.funding["event_time"] <= as_of)
        ]
        rows = []
        for row in funding.itertuples():
            mark = exact_marks.get((row.instrument_id, row.event_time))
            rows.append((row.event_time, row.instrument_id, float(row.rate), mark, row.available_at))
        raw = tape.funding[
            (tape.funding["event_time"] > previous) & (tape.funding["event_time"] <= as_of)
        ]
        visible = set(zip(funding["instrument_id"], funding["event_time"]))
        for row in raw.drop_duplicates(["instrument_id", "event_time"]).itertuples():
            if (row.instrument_id, row.event_time) not in visible:
                rows.append((row.event_time, row.instrument_id, None, None, row.available_at))
        prep["funding"][i] = rows
    for name, rows in (("marks", marks), ("adv", adv)):
        prep[name] = pd.DataFrame.from_dict(rows, orient="index").reindex(dates)
    return prep


def _instruments(specs, as_of):
    specs = specs[
        (specs["available_at"] <= as_of) & (specs["event_time"] <= as_of)
        & (specs["valid_from"] <= as_of)
    ]
    latest = specs.sort_values(["instrument_id", "valid_from", "revision"]).groupby(
        "instrument_id", sort=False
    ).tail(1)
    values = latest[["contract_multiplier", "step_size", "min_notional"]].astype(float)
    if (
        not np.isfinite(values.to_numpy()).all()
        or (values[["contract_multiplier", "step_size"]] <= 0).any().any()
        or (values["min_notional"] < 0).any()
    ):
        raise ValueError(f"invalid executable contract spec at {as_of}")
    return {
        row.instrument_id: Instrument(
            row.instrument_id,
            float(row.contract_multiplier),
            float(row.step_size),
            float(row.min_notional),
        )
        for row in latest.itertuples()
    }


def _delists(instruments, date):
    latest = instruments.sort_values(["event_time", "revision"]).groupby(
        "instrument_id", sort=False
    ).tail(1)
    return {
        row.instrument_id: pd.Timestamp(row.last_trade_day)
        for row in latest.itertuples()
        if not pd.isna(row.last_trade_day) and row.last_trade_day <= date
    }


def _replay_input_hash(prep, targets, settings):
    inputs = {
        key: frame_hash(value.reset_index())
        for key, value in prep.items() if isinstance(value, pd.DataFrame)
    }
    inputs.update(
        dates=[date.isoformat() for date in prep["dates"]],
        as_of=frame_hash(prep["as_of"].to_frame(name="as_of").reset_index()),
        instruments={i: {k: asdict(v) for k, v in row.items()}
                     for i, row in prep["instruments"].items()},
        funding=prep["funding"], delists=prep["delists"],
        targets=frame_hash(targets.reset_index()),
        settings={k: v for k, v in settings.items() if k != "trials_path"},
        git_head=_git_head(),
    )
    return canonical_hash(json.loads(json.dumps(inputs, default=str)))


def _simulate(prep, targets, settings, *, checkpoint=None, stop=None) -> dict:
    """Execute [checkpoint.next_day, stop); retain the full PIT input/warm-up panels.

    A checkpoint is local JSON evidence, bound to these inputs and this git head.
    Only completed days are checkpointed; opening fills restore the book once per day.
    """
    dates = prep["dates"]
    nominal = settings["research_nominal_usd"]
    state = {
        "input_hash": _replay_input_hash(prep, targets, settings),
        "next_day": 0, "watermark": None,
        "wallet": nominal, "positions": {}, "entry": {}, "orders": {},
        "equity_series": [nominal], "identity_error": 0.0,
        "fees_total": 0.0, "funding_total": 0.0, "traded_notional": 0.0,
        "trade_count": 0, "weights_rows": [], "cost_rows": [], "rate_rows": [],
    }
    if checkpoint is not None:
        try:
            # Round-trip also detaches mutable history from the caller's checkpoint.
            saved = json.loads(json.dumps(checkpoint["state"], allow_nan=False))
            if (set(saved) != set(state)
                    or checkpoint["hash"] != canonical_hash(saved)
                    or saved["input_hash"] != state["input_hash"]):
                raise ValueError("checkpoint hash or replay inputs differ")
            start = saved["next_day"]
            if type(start) is not int or not 0 <= start <= len(dates):
                raise ValueError("invalid checkpoint next_day")
            watermark = prep["as_of"].iloc[start - 1].isoformat() if start else None
            if (saved["watermark"] != watermark
                    or len(saved["equity_series"]) != start + 1
                    or saved["equity_series"][0] != nominal
                    or any(len(saved[k]) != start for k in ("weights_rows", "cost_rows", "rate_rows"))
                    or set(saved["positions"]) != set(saved["entry"])):
                raise ValueError("inconsistent checkpoint boundary/state")
            known = prep["instruments"][start - 1] if start else {}
            for key in ("positions", "entry", "orders"):
                book = saved[key]
                if (not isinstance(book, dict) or not book.keys() <= known.keys()
                        or any(type(v) not in (int, float) or v == 0 for v in book.values())):
                    raise ValueError(f"invalid checkpoint {key}")
            if (any(type(saved[k]) not in (int, float) for k in (
                    "wallet", "identity_error", "fees_total", "funding_total", "traded_notional"))
                    or type(saved["trade_count"]) is not int or saved["trade_count"] < 0
                    or any(v <= 0 for v in saved["entry"].values())):
                raise ValueError("invalid checkpoint accounting values")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid replay checkpoint: {exc}") from exc
        state = saved
    start = state["next_day"]
    stop = len(dates) if stop is None else stop
    if type(stop) is not int or not start <= stop <= len(dates):
        raise ValueError("stop must be a day boundary at or after checkpoint.next_day")
    wallet, positions, entry, orders = (state[k] for k in ("wallet", "positions", "entry", "orders"))
    equity_series = state["equity_series"]
    identity_error = state["identity_error"]
    fees_total, funding_total, traded_notional = (
        state[k] for k in ("fees_total", "funding_total", "traded_notional")
    )
    trade_count = state["trade_count"]
    weights_rows, cost_rows, rate_rows = (
        state[k] for k in ("weights_rows", "cost_rows", "rate_rows")
    )

    for i in range(start, stop):
        date = dates[i]
        as_of = prep["as_of"].loc[date]
        instruments = prep["instruments"][i]
        for instrument_id in positions.keys() | orders.keys():
            if instrument_id not in instruments:
                raise ValueError(f"missing executable contract spec: {instrument_id} at {as_of}")
            previous_inst = prep["instruments"][i - 1][instrument_id]
            if instruments[instrument_id].contract_multiplier != previous_inst.contract_multiplier:
                raise ValueError("contract multiplier changed with exposure or a queued order; migration policy required")
        events = []
        # Recreate the previous close's book: a zero-cost fill at the average
        # entry price restores qty and entry exactly.
        for instrument_id, qty in positions.items():
            events.append(
                _fill(prep["as_of"].iloc[i - 1], instrument_id, qty, entry[instrument_id])
            )
        for timestamp, instrument_id, rate, price, available_at in prep["funding"].get(i, []):
            if not positions.get(instrument_id):
                continue
            if rate is None or available_at > timestamp:
                raise ValueError(f"funding unavailable at payment time: {instrument_id} at {timestamp}")
            if price is None or not np.isfinite(price) or price <= 0:
                raise ValueError(f"funding mark unavailable: {instrument_id} at {timestamp}")
            events.append(_funding(timestamp, instrument_id, rate, price))
        costs, rates = {}, {}
        for instrument_id, qty in orders.items():
            eligible = prep["as_of"].iloc[i - 1] + pd.Timedelta(settings["executable_latency"])
            if as_of != eligible:
                raise ValueError("daily-mark fixture requires an executable price exactly after executable_latency")
            # Explicit synthetic fixture only: the published daily mark is executable.
            price = _value(prep["marks"], date, instrument_id)
            if price is None or price <= 0:
                raise ValueError(f"executable price unavailable: {instrument_id} at {as_of}")
            inst = instruments[instrument_id]
            notional = abs(qty) * inst.contract_multiplier * price
            steps = qty / inst.step_size
            if not np.isclose(steps, round(steps), rtol=0, atol=1e-7) or (
                notional < inst.min_notional and qty != -positions.get(instrument_id, 0.0)
            ):
                raise ValueError(f"queued order violates executable contract spec: {instrument_id} at {as_of}")
            decision = dates[i - 1]
            daily_vol = _value(prep["daily_vol"], decision, instrument_id)
            adv = _value(prep["adv"], decision, instrument_id)
            if adv is None or adv <= 0 or daily_vol is None or daily_vol <= 0:
                raise ValueError(f"PIT execution cost inputs unavailable: {instrument_id} at {decision}")
            slip = slippage_bps(
                notional,
                daily_vol,
                adv,
                y=settings["slippage_y"],
                min_bps=settings["slippage_min_bps"],
            )
            events.append(
                _fill(as_of, instrument_id, qty, price, settings["taker_fee"], slip)
            )
            traded_notional += notional
            trade_count += 1
            costs[instrument_id] = settings["taker_fee"] + slip / 1e4
        delists = prep["delists"][i]
        for instrument_id, last_trade_day in delists.items():
            if not positions.get(instrument_id) and instrument_id not in orders:
                continue
            if last_trade_day < date:
                raise ValueError(f"delist published after liquidation day: {instrument_id}; owner policy required")
            if instrument_id in orders:
                raise ValueError(f"queued order overlaps delist: {instrument_id}; owner policy required")
            mark = _value(prep["marks"], date, instrument_id)
            if mark is None or mark <= 0:
                raise ValueError(f"delist liquidation mark unavailable: {instrument_id} at {as_of}")
            events.append(
                {
                    "time": as_of.isoformat(),
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
                if mark <= 0:
                    raise ValueError(f"invalid mark: {instrument_id} at {as_of}")
                close_marks[instrument_id] = mark
                events.append(
                    {
                        "time": as_of.isoformat(),
                        "type": "MARK",
                        "instrument_id": instrument_id,
                        "mark_price": mark,
                    }
                )
        for timestamp, instrument_id, rate, _, _ in prep["funding"].get(i, []):
            if rate is not None:
                rates[instrument_id] = rates.get(instrument_id, 0.0) + rate
        for instrument_id in positions:
            if positions[instrument_id] + orders.get(instrument_id, 0.0) != 0 and instrument_id not in delists:
                if instrument_id not in close_marks:
                    raise ValueError(f"valuation mark unavailable: {instrument_id} at {as_of}")

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
            # Rebuild from this day's visible marks, never a stale/entry fallback.
            rebuilt = wallet + sum(
                qty
                * instruments[instrument_id].contract_multiplier
                * (close_marks[instrument_id] - entry[instrument_id])
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
        equity_series.append(equity)
        weights_rows.append(
            {
                instrument_id: qty
                * instruments[instrument_id].contract_multiplier
                * close_marks[instrument_id]
                / equity
                for instrument_id, qty in positions.items()
            }
        )
        cost_rows.append(costs)
        rate_rows.append(rates)

        orders = {}
        if i + 1 < len(dates):
            marks = close_marks
            target = {}
            for instrument_id, weight in targets.loc[date].items():
                if float(weight) != 0 and instrument_id not in instruments:
                    raise ValueError(f"missing decision contract spec: {instrument_id} at {as_of}")
                if instrument_id in delists:
                    continue
                if float(weight) != 0 and instrument_id not in close_marks:
                    raise ValueError(f"decision mark unavailable: {instrument_id} at {as_of}")
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
    state.update(
        next_day=stop, watermark=prep["as_of"].iloc[stop - 1].isoformat() if stop else None,
        wallet=wallet, positions=positions, entry=entry, orders=orders,
        identity_error=identity_error, fees_total=fees_total, funding_total=funding_total,
        traded_notional=traded_notional, trade_count=trade_count,
    )
    evidence_hash = canonical_hash(state)
    return {
        "checkpoint": {"state": state, "hash": evidence_hash},
        "equity": equity_series[start + 1:],
        "evidence_hash": evidence_hash,
        "metrics": metrics,
        "identity_max_abs_error": identity_error,
        "scanner_max_abs_diff": _scanner_diff(
            {**prep, "dates": dates[:stop]},
            weights_rows, cost_rows, rate_rows, equity_series, settings
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
    return None if not np.isfinite(value) else float(value)


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
    return {
        "time": timestamp.isoformat(),
        "type": "FUNDING",
        "instrument_id": instrument_id,
        "rate": rate,
        "mark_price": mark_price,
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
