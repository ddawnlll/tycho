"""Synthetic-only independent accounting, from §07 and docs/ledger-spec.md.

Equity is signed inventory marked to market plus a trade-cash account; average
cost is tracked separately to split equity into wallet and unrealized PnL.
Neither production accounting module is imported here.

D-41 provenance: a cost-bearing scanner diagnostic existed at 8d9db4b before
D-41; the owner then fixed the 1e-4 relative bound, and config/scanner_bound.toml
was committed in e0a2451 before all post-D-41 cost-bearing comparisons.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import copysign, isfinite, sqrt, trunc


def _number(value, *, positive=False, nonnegative=False):
    if not isfinite(value) or (positive and value <= 0) or (nonnegative and value < 0):
        raise ValueError("invalid finite numeric input")
    return value


def _synthetic_only(synthetic):
    if synthetic is not True:
        raise ValueError("reference policy is synthetic-only; owner decisions are PENDING")


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    contract_multiplier: float
    step_size: float
    min_notional: float

    def __post_init__(self):
        if not self.instrument_id:
            raise ValueError("instrument_id is required")
        _number(self.contract_multiplier, positive=True)
        _number(self.step_size, positive=True)
        _number(self.min_notional, nonnegative=True)


def run_ledger(initial_wallet, instruments, events, *, synthetic):
    """Replay documented MARK/FUNDING/FILL/DELIST events without production code."""
    _synthetic_only(synthetic)
    _number(initial_wallet, positive=True)
    priority = {"MARK": 0, "FUNDING": 1, "DELIST": 2, "FILL": 3}
    ordered = []
    for event in events:
        timestamp = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
        if timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
            raise ValueError("event time must be UTC")
        kind, symbol = event["type"], event["instrument_id"]
        if kind not in priority or symbol not in instruments:
            raise ValueError("unknown event type or instrument")
        ordered.append((timestamp, priority[kind], event))

    qty = dict.fromkeys(instruments, 0.0)
    basis = dict.fromkeys(instruments, 0.0)
    marks = dict.fromkeys(instruments, 0.0)
    cash, fees, funding = initial_wallet, 0.0, 0.0
    snapshots = []
    for _, _, event in sorted(ordered, key=lambda item: item[:2]):
        kind, symbol = event["type"], event["instrument_id"]
        mult = instruments[symbol].contract_multiplier
        if kind == "MARK":
            marks[symbol] = _number(event["mark_price"], positive=True)
        elif kind == "FUNDING":
            price = _number(event["mark_price"], positive=True)
            payment = -qty[symbol] * mult * price * _number(event["rate"])
            funding += payment
            cash += payment
        else:
            price = _number(event["price" if kind == "FILL" else "mark_price"], positive=True)
            delta = _number(event["qty"]) if kind == "FILL" else -qty[symbol]
            fee_rate = _number(event["fee_rate"], nonnegative=True)
            slip = _number(event["slippage_bps"], nonnegative=True)
            if slip >= 10000:
                raise ValueError("slippage must be below 10000 bps")
            executed = price * (1 + (copysign(1, delta) if delta else 0) * slip / 10000)
            old, new = qty[symbol], qty[symbol] + delta
            if old * delta >= 0:
                basis[symbol] += delta * mult * executed
            elif old * new > 0:
                basis[symbol] *= new / old
            else:
                basis[symbol] = new * mult * executed
            cost = abs(delta) * mult * executed * fee_rate
            cash -= delta * mult * executed + cost
            fees += cost
            qty[symbol], marks[symbol] = new, price

        inventory = sum(qty[key] * instruments[key].contract_multiplier * marks[key] for key in qty)
        equity = cash + inventory
        unrealized = inventory - sum(basis.values())
        wallet = cash + sum(basis.values())
        snapshots.append({
            "time": event["time"], "type": kind, "instrument_id": symbol,
            "wallet": wallet, "equity": equity, "unrealized": unrealized,
            "realized_cum": wallet - initial_wallet + fees - funding,
            "fees_cum": fees, "funding_cum": funding,
            "positions": dict(qty),
            "entry": {key: basis[key] / (qty[key] * instruments[key].contract_multiplier)
                      if qty[key] else 0.0 for key in qty},
        })
    return snapshots


def rebalance_orders(target_weights, positions, marks, equity, instruments,
                     band_relative, absolute_band_nav, partial_ratio, *,
                     zero_crossing_rule, synthetic):
    """D-39 full crossing, explicit and still required for synthetic runs."""
    _synthetic_only(synthetic)
    if zero_crossing_rule != "full_trade_on_zero_or_cross":
        raise ValueError("zero_crossing_rule must be 'full_trade_on_zero_or_cross' (D-39)")
    _number(equity, positive=True)
    _number(band_relative, nonnegative=True)
    _number(absolute_band_nav, nonnegative=True)
    _number(partial_ratio, positive=True)
    if partial_ratio > 1:
        raise ValueError("partial_ratio must be <= 1")
    orders = {}
    for symbol in sorted(target_weights.keys() | positions.keys()):
        spec = instruments[symbol]
        unit = spec.contract_multiplier * _number(marks[symbol], positive=True)
        held = _number(positions.get(symbol, 0))
        target = _number(target_weights.get(symbol, 0))
        current = held * unit / equity
        difference = target - current
        if target == 0 and held:
            orders[symbol] = -held
            continue
        if target * current >= 0:
            if abs(difference) < max(band_relative * abs(target), absolute_band_nav):
                continue
            difference *= partial_ratio
        order = trunc(difference * equity / unit / spec.step_size) * spec.step_size
        if order and abs(order) * unit >= spec.min_notional:
            orders[symbol] = order
    return orders


def slippage_bps(order_notional, daily_vol, adv_notional, y=1.0, min_bps=2.0):
    _number(order_notional, nonnegative=True)
    _number(daily_vol, nonnegative=True)
    _number(adv_notional, positive=True)
    _number(y, nonnegative=True)
    _number(min_bps, nonnegative=True)
    return max(min_bps, y * daily_vol * sqrt(order_notional / adv_notional) * 10000)
