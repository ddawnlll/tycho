"""Authoritative sequential ledger (D-22).

Spec: docs/ledger-spec.md (monograph §07 "Nakit–pozisyon–fonlama muhasebesi",
registry accounting_ledger). The vector scanner is only an approximation of this.
"""

import math
from dataclasses import dataclass
from datetime import datetime

_EVENT_PRIORITY = {"MARK": 0, "FUNDING": 1, "DELIST": 2, "FILL": 3}


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    contract_multiplier: float
    step_size: float
    min_notional: float


def _priority(event):
    try:
        return _EVENT_PRIORITY[event["type"]]
    except KeyError:
        raise ValueError(f"unknown event type {event['type']!r}") from None


def run_ledger(initial_wallet, instruments, events):
    """Process events in order and return one snapshot per event, in processing order."""
    realized_cum = fees_cum = funding_cum = 0.0
    qty = {}    # instrument_id -> signed contracts
    entry = {}  # instrument_id -> average entry price
    mark = {}   # instrument_id -> latest mark

    def apply_fill(instrument_id, fill_qty, price, fee_rate, slippage_bps):
        nonlocal realized_cum, fees_cum
        # The mark follows the raw event price, so slippage shows up as unrealized PnL.
        mark[instrument_id] = price
        if fill_qty == 0.0:
            return
        mult = instruments[instrument_id].contract_multiplier
        exec_price = price * (1.0 + math.copysign(slippage_bps, fill_qty) / 1e4)
        fees_cum += abs(fill_qty) * mult * exec_price * fee_rate
        held = qty.get(instrument_id, 0.0)
        if held == 0.0:
            qty[instrument_id], entry[instrument_id] = fill_qty, exec_price
        elif held * fill_qty > 0:
            entry[instrument_id] = (
                held * entry[instrument_id] + fill_qty * exec_price
            ) / (held + fill_qty)
            qty[instrument_id] = held + fill_qty
        else:
            realized = (
                (exec_price - entry[instrument_id])
                * min(abs(fill_qty), abs(held))
                * mult
                * (1.0 if held > 0 else -1.0)
            )
            realized_cum += realized
            remaining = held + fill_qty
            if abs(fill_qty) > abs(held):
                # Crossed zero: old side closed, remainder opens at the exec price.
                qty[instrument_id], entry[instrument_id] = remaining, exec_price
            elif remaining == 0.0:
                qty.pop(instrument_id)
                entry.pop(instrument_id)
            else:
                qty[instrument_id] = remaining

    # Python's sort is stable, so input order breaks remaining ties.
    ordered = sorted(
        range(len(events)),
        key=lambda i: (datetime.fromisoformat(events[i]["time"]), _priority(events[i])),
    )

    snapshots = []
    for i in ordered:
        event = events[i]
        etype = event["type"]
        instrument_id = event["instrument_id"]
        if etype == "MARK":
            mark[instrument_id] = event["mark_price"]
        elif etype == "FUNDING":
            # Position at the funding instant, never bar start; funding does not move the mark.
            funding_cum += (
                -qty.get(instrument_id, 0.0)
                * instruments[instrument_id].contract_multiplier
                * event["mark_price"]
                * event["rate"]
            )
        elif etype == "DELIST":
            apply_fill(
                instrument_id,
                -qty.get(instrument_id, 0.0),
                event["mark_price"],
                event["fee_rate"],
                event["slippage_bps"],
            )
        else:  # FILL
            apply_fill(
                instrument_id,
                event["qty"],
                event["price"],
                event["fee_rate"],
                event["slippage_bps"],
            )

        # Spec identity: wallet = initial + realized - fees + funding. Recomputing it
        # from the cumulative components cannot double count them.
        wallet = initial_wallet + realized_cum - fees_cum + funding_cum
        unrealized = sum(
            q * instruments[k].contract_multiplier * (mark[k] - entry[k])
            for k, q in qty.items()
        )
        snapshots.append(
            {
                "time": event["time"],
                "type": etype,
                "instrument_id": instrument_id,
                "wallet": wallet,
                "equity": wallet + unrealized,
                "unrealized": unrealized,
                "realized_cum": realized_cum,
                "fees_cum": fees_cum,
                "funding_cum": funding_cum,
                "positions": dict(qty),
                "entry": dict(entry),
            }
        )
    return snapshots


def rebalance_orders(
    target_weights,
    positions,
    marks,
    equity,
    instruments,
    band_relative,
    absolute_band_nav,
    partial_ratio,
):
    """Orders (instrument_id -> signed contracts, 0 omitted) to move drifted to target."""
    orders = {}
    universe = list(target_weights) + [k for k in positions if k not in target_weights]
    for instrument_id in universe:
        inst = instruments[instrument_id]
        target = target_weights.get(instrument_id, 0.0)
        held = positions.get(instrument_id, 0.0)
        mark = marks[instrument_id]
        w_cur = held * inst.contract_multiplier * mark / equity
        diff = target - w_cur
        zero_target = target == 0.0 and held != 0.0
        sign_crossing = target != 0.0 and w_cur != 0.0 and (target > 0.0) != (w_cur > 0.0)
        if zero_target or sign_crossing:
            trade_weight = diff  # band and partial ratio do not apply
        elif abs(diff) < max(band_relative * abs(target), absolute_band_nav):
            continue
        else:
            trade_weight = partial_ratio * diff

        raw_qty = trade_weight * equity / (inst.contract_multiplier * mark)
        # Tiny epsilon so exact multiples of step_size are not lost to float representation.
        steps = math.trunc(raw_qty / inst.step_size + math.copysign(1e-9, raw_qty))
        order_qty = steps * inst.step_size
        if order_qty == 0.0:
            continue
        closing_to_zero = target == 0.0 and order_qty == -held
        if (
            abs(order_qty) * inst.contract_multiplier * mark < inst.min_notional
            and not closing_to_zero
        ):
            continue
        orders[instrument_id] = order_qty
    return orders


def slippage_bps(order_notional, daily_vol, adv_notional, y=1.0, min_bps=2.0):
    """Square-root impact in bps, floored at min_bps (registry costs, Y ~ 1)."""
    raw = y * daily_vol * math.sqrt(order_notional / adv_notional) * 1e4
    return float(max(min_bps, raw))
