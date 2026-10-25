"""G0 FEASIBILITY-DATA audit: data sufficiency only.

Returns, PnL, Sharpe and every other outcome metric are prohibited here; this
module only counts rows and ratios of the tape itself.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import config
from .tape import TABLE_COLUMNS, load_tape

_THRESHOLD_KEYS = (
    "min_delisted_coverage",
    "min_funding_coverage",
    "min_mark_coverage",
    "min_spec_coverage",
    "max_months_below_30",
)


def audit(tape, thresholds, min_history_days=None) -> dict:
    if min_history_days is None:
        min_history_days = config.load()["universe"]["min_history_days"]
    defects = _structural_defects(tape)
    metrics = _metrics(tape, int(min_history_days))

    if defects:
        verdict, reasons = "INCOMPLETE", defects
    elif thresholds is None or thresholds == "PENDING":
        verdict, reasons = "BLOCKED", ["G0 sufficiency thresholds PENDING"]
    else:
        failing = _failing_thresholds(metrics, thresholds)
        if failing:
            verdict = "INCOMPLETE"
            reasons = [f"threshold failed: {key}" for key in failing]
        else:
            verdict, reasons = "PASS", []
    return {
        "verdict": verdict,
        "reasons": reasons,
        "metrics": metrics,
        "prohibited_metrics_computed": False,
    }


def _structural_defects(tape) -> list:
    defects = []
    for name in TABLE_COLUMNS:
        df = getattr(tape, name)
        nat = int(df["available_at"].isna().sum())
        if nat:
            defects.append(f"{name}: NaT available_at ({nat} rows)")
        early = int((df["available_at"] < df["event_time"]).sum())
        if early:
            defects.append(f"{name}: available_at before event_time ({early} rows)")

    bar_ids = set(tape.bars_1d["instrument_id"])
    spec_ids = set(tape.specs["instrument_id"])
    missing_spec = int((~tape.bars_1d["instrument_id"].isin(spec_ids)).sum())
    if missing_spec:
        defects.append(f"bars_1d: bars without any spec ({missing_spec} rows)")

    delisted = _delisted_perps(tape.instruments)
    no_delisted_at = delisted["delisted_at"].isna().sum()
    if no_delisted_at:
        defects.append(f"instruments: delisted perps without delisted_at ({int(no_delisted_at)})")
    no_bars = [iid for iid in delisted["instrument_id"] if iid not in bar_ids]
    if no_bars:
        defects.append(f"instruments: delisted perps without bars ({len(no_bars)}: {no_bars})")
    return defects


def _metrics(tape, min_history_days) -> dict:
    instruments = tape.instruments
    perps = instruments[instruments["kind"] == "perp"]
    excluded = int(instruments["kind"].isin(["stablecoin", "index"]).sum())

    delisted = _delisted_perps(instruments)
    bars_by = dict(tuple(tape.bars_1d.groupby("instrument_id")))
    covered = 0
    for _, row in delisted.iterrows():
        if pd.isna(row["delisted_at"]) or pd.isna(row["last_trade_day"]):
            continue
        bars = bars_by.get(row["instrument_id"])
        if bars is not None and len(bars) and bars["event_time"].max() >= row["last_trade_day"]:
            covered += 1

    availability = {}
    total_rows = observed_rows = revisions = 0
    for name in TABLE_COLUMNS:
        df = getattr(tape, name)
        if len(df):
            ok = df["available_at"].notna() & (df["available_at"] >= df["event_time"])
            availability[name] = float(ok.mean())
            observed_rows += int(df["received_at_is_observed"].sum())
        else:
            availability[name] = 1.0
        total_rows += len(df)
        revisions += int((df["revision"] > 0).sum())

    funding_min, funding_median = _funding_coverage(tape, perps, bars_by)
    return {
        "listings": {
            "instruments_total": int(len(instruments)),
            "perps": int(len(perps)),
            "excluded_stablecoin_index": excluded,
            "delisted_perps": int(len(delisted)),
            "delisted_covered": int(covered),
            "delisted_coverage": covered / len(delisted) if len(delisted) else 1.0,
        },
        "availability": {
            "tables": availability,
            "received_at_is_observed": observed_rows / total_rows if total_rows else 1.0,
            "revision_count": revisions,
        },
        "funding_coverage": {"min": funding_min, "median": funding_median},
        "mark_coverage": len(tape.mark) / len(tape.bars_1d) if len(tape.bars_1d) else 1.0,
        "spec_coverage": _spec_coverage(tape),
        "universe": _universe(tape, perps, bars_by, min_history_days),
    }


def _delisted_perps(instruments):
    perps = instruments[instruments["kind"] == "perp"]
    return perps[perps["delisted_at"].notna() | perps["last_trade_day"].notna()]


def _funding_coverage(tape, perps, bars_by):
    # specs persist until replaced, so the last revision carries the interval
    intervals = (
        tape.specs.sort_values("valid_from")
        .groupby("instrument_id")["funding_interval_hours"]
        .last()
    )
    observed = tape.funding.groupby("instrument_id").size()
    ratios = []
    for _, row in perps.iterrows():
        interval = intervals.get(row["instrument_id"])
        if interval is None or not np.isfinite(interval) or interval <= 0:
            continue
        live_end = row["last_trade_day"] if not pd.isna(row["last_trade_day"]) else None
        if live_end is None:
            bars = bars_by.get(row["instrument_id"])
            if bars is None or not len(bars):
                continue
            live_end = bars["event_time"].max()
        if pd.isna(row["listed_at"]) or live_end <= row["listed_at"]:
            continue
        span_hours = (live_end - row["listed_at"]).total_seconds() / 3600.0
        expected = int(span_hours // float(interval)) + 1
        # both life boundaries carry a funding slot, so the count can exceed whole intervals
        ratios.append(min(1.0, int(observed.get(row["instrument_id"], 0)) / expected))
    if not ratios:
        return None, None
    return float(np.min(ratios)), float(np.median(ratios))


def _spec_coverage(tape):
    if not len(tape.bars_1d):
        return 1.0
    first_valid = tape.specs.groupby("instrument_id")["valid_from"].min()
    if first_valid.empty:
        return 0.0
    valid_at = tape.bars_1d["instrument_id"].map(first_valid)
    covered = valid_at.notna() & (valid_at <= tape.bars_1d["event_time"])
    return float(covered.mean())


def _universe(tape, perps, bars_by, min_history_days):
    if not len(tape.bars_1d):
        return {
            "min_history_days": min_history_days,
            "months": [],
            "months_below_30": 0,
            "first_month_with_30": None,
        }
    start = tape.bars_1d["event_time"].min().normalize().replace(day=1)
    month_starts = pd.date_range(start, tape.bars_1d["event_time"].max(), freq="MS")
    months = []
    for month_start in month_starts:
        window_start = month_start - pd.Timedelta(days=30)
        eligible = 0
        for _, row in perps.iterrows():
            bars = bars_by.get(row["instrument_id"])
            if bars is None or pd.isna(row["listed_at"]):
                continue
            if row["listed_at"] > month_start:
                continue
            if not pd.isna(row["delisted_at"]) and row["delisted_at"] < month_start:
                continue
            if bars.loc[bars["event_time"] < month_start, "event_time"].nunique() < min_history_days:
                continue
            trailing = bars[(bars["event_time"] >= window_start) & (bars["event_time"] < month_start)]
            if not trailing["quote_volume"].notna().any():
                continue
            eligible += 1
        months.append({"month": month_start.strftime("%Y-%m"), "eligible": eligible})
    below = [m["month"] for m in months if m["eligible"] < 30]
    first_ok = next((m["month"] for m in months if m["eligible"] >= 30), None)
    return {
        "min_history_days": min_history_days,
        "months": months,
        "months_below_30": len(below),
        "first_month_with_30": first_ok,
    }


def _failing_thresholds(metrics, thresholds) -> list:
    checks = {
        "min_delisted_coverage": (metrics["listings"]["delisted_coverage"], "min"),
        "min_funding_coverage": (metrics["funding_coverage"]["min"], "min"),
        "min_mark_coverage": (metrics["mark_coverage"], "min"),
        "min_spec_coverage": (metrics["spec_coverage"], "min"),
        "max_months_below_30": (metrics["universe"]["months_below_30"], "max"),
    }
    failing = []
    for key in _THRESHOLD_KEYS:
        value, kind = checks[key]
        limit = thresholds.get(key) if hasattr(thresholds, "get") else None
        if limit is None or value is None:
            failing.append(key)
        elif (kind == "min" and value < limit) or (kind == "max" and value > limit):
            failing.append(key)
    return failing


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="G0 FEASIBILITY-DATA audit")
    parser.add_argument("--tape", required=True, help="tape directory")
    parser.add_argument("--out", required=True, help="report JSON path")
    args = parser.parse_args(argv)
    cfg = config.load()
    tape = load_tape(args.tape, cfg)
    report = audit(
        tape,
        cfg["g0"]["data_sufficiency_thresholds"],
        cfg["universe"]["min_history_days"],
    )
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
