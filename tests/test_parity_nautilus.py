"""T7.1 acceptance: pinned Nautilus executes precomputed targets, parity to the ledger."""

import math
import tomllib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader import __version__ as nautilus_version
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity

from tycho.ledger import Instrument, rebalance_orders, run_ledger
from tycho.nautilus_parity import run_nautilus_parity

TOLERANCES = tomllib.loads((Path(__file__).resolve().parents[1] / "config" / "parity.toml").read_text())
VENUE = Venue("PARITY")


def nautilus_instrument():
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("AAAUSDT"), VENUE),
        raw_symbol=Symbol("AAAUSDT"),
        base_currency=USDT,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=6,
        size_precision=0,
        price_increment=Price.from_str("0.000001"),
        size_increment=Quantity.from_int(1),
        multiplier=Quantity.from_int(1),
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.05"),
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
        min_notional=Money(1.0, USDT),
        ts_event=0,
        ts_init=0,
    )


def _ns(time):
    return int(time.timestamp() * 1e9)


def _iso(ns):
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def fixture():
    """Synthetic daily tape, target array, and supplied funding (no production values)."""
    closes = [100.0, 103.0, 97.0, 101.0, 99.0, 105.0, 102.0, 104.0]
    bars = []
    start = datetime(2024, 1, 2, tzinfo=timezone.utc)
    for index, close in enumerate(closes):
        open_ = closes[index - 1] if index else 100.0
        bars.append(
            {
                "time": (start + timedelta(days=index)).isoformat().replace("+00:00", "Z"),
                "open": open_,
                "high": max(open_, close) + 1.0,
                "low": min(open_, close) - 1.0,
                "close": close,
                "volume": 1,
            }
        )
    targets = [0.4, 0.4, -0.2, -0.2, 0.0, 0.0, 0.3, 0.3]
    funding = [
        {"time": "2024-01-03T12:00:00Z", "rate": 0.001, "mark_price": 102.0},
        {"time": "2024-01-05T12:00:00Z", "rate": -0.0005, "mark_price": 101.0},
    ]
    return bars, targets, funding


def reference(bars, targets, funding, initial_wallet, fee_rate, slippage_bps):
    """Independent ledger path: same targets and cost model, no PnL copied in."""
    ledger_instrument = Instrument("AAAUSDT.PARITY", 1.0, 1.0, 1.0)
    instruments = {ledger_instrument.instrument_id: ledger_instrument}
    instrument_id = ledger_instrument.instrument_id
    events = []
    pending = None
    equity = []
    fills = []
    next_funding = 0
    supplies = sorted(funding, key=lambda event: event["time"])

    for index, bar in enumerate(bars):
        close_ns = _ns(datetime.fromisoformat(bar["time"].replace("Z", "+00:00")))
        close = bar["close"]
        while next_funding < len(supplies) and supplies[next_funding]["time"] <= bar["time"]:
            event = supplies[next_funding]
            events.append(
                {
                    "time": event["time"],
                    "type": "FUNDING",
                    "instrument_id": instrument_id,
                    "rate": event["rate"],
                    "mark_price": event["mark_price"],
                }
            )
            next_funding += 1

        events.append(
            {
                "time": _iso(close_ns),
                "type": "MARK",
                "instrument_id": instrument_id,
                "mark_price": close,
            }
        )
        filled_this_bar = pending is not None
        if filled_this_bar:
            events.append(
                {
                    "time": _iso(close_ns),
                    "type": "FILL",
                    "instrument_id": instrument_id,
                    "qty": pending,
                    "price": close,
                    "fee_rate": fee_rate,
                    "slippage_bps": slippage_bps,
                }
            )
            fills.append((close_ns, pending, close))
            pending = None

        snap_ns = close_ns + 1_000_000_000
        events.append(
            {
                "time": _iso(snap_ns),
                "type": "MARK",
                "instrument_id": instrument_id,
                "mark_price": close,
            }
        )
        snapshots = run_ledger(initial_wallet, instruments, events)
        latest = snapshots[-1]
        equity.append(latest["equity"])

        # One outstanding rebalance at a time, and no decision on its fill bar,
        # mirroring Nautilus's bar/fill message ordering.
        if not filled_this_bar:
            orders = rebalance_orders(
                {instrument_id: targets[index]},
                latest["positions"],
                {instrument_id: close},
                latest["equity"],
                instruments,
                0.0,
                0.0,
                1.0,
            )
            pending = orders.get(instrument_id)

    daily = [equity[0] - initial_wallet, *(b - a for a, b in zip(equity, equity[1:]))]
    return {
        "daily_pnl": daily,
        "total_pnl": equity[-1] - initial_wallet,
        "equity": equity,
        "fills": fills,
    }


def test_pin_is_stable_and_latency_is_required():
    assert nautilus_version == "1.221.0"
    assert "rc" not in nautilus_version.lower()
    bars, targets, funding = fixture()
    with pytest.raises(ValueError, match="positive"):
        run_nautilus_parity(
            nautilus_instrument(),
            bars,
            targets,
            funding,
            initial_wallet=500.0,
            fee_rate=0.0005,
            slippage_bps=10.0,
            executable_latency_ns=0,
        )


def test_nautilus_daily_and_total_parity_with_imposed_costs():
    bars, targets, funding = fixture()
    fee_rate = 0.0005
    slippage_bps = 10.0
    initial = 500.0
    latency = 1_000_000_000

    actual = run_nautilus_parity(
        nautilus_instrument(),
        bars,
        targets,
        funding,
        initial_wallet=initial,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        executable_latency_ns=latency,
    )
    expected = reference(bars, targets, funding, initial, fee_rate, slippage_bps)

    # The engine really executed orders, and every fill is after its decision.
    assert len(actual["fills"]) == 4
    assert len(actual["decisions"]) == 4
    for (decision_ns, _, _), fill in zip(actual["decisions"], actual["fills"]):
        assert fill[0] > decision_ns
        bar_index = [bar["time"] for bar in bars].index(_iso(decision_ns))
        assert fill[0] == _ns(datetime.fromisoformat(bars[bar_index + 1]["time"]))
    # Imposed prices are the next bar close moved by the same slippage, not a copied PnL.
    for (_, qty, raw_price), fill in zip(expected["fills"], actual["fills"]):
        side = 1.0 if qty > 0 else -1.0
        assert fill[3] == pytest.approx(raw_price * (1.0 + side * slippage_bps / 1e4))
    assert actual["fills"][0][1:3] == ("BUY", 2.0)
    assert actual["fills"][1][1:3] == ("SELL", 3.0)

    # Supplied funding was posted to the Nautilus account and changes equity.
    assert len(actual["funding_paid"]) == 2
    assert any(amount != 0.0 for _, amount in actual["funding_paid"])

    # Both long and short PnL paths ran.
    assert min(actual["daily_pnl"]) < 0.0
    assert max(actual["daily_pnl"]) > 0.0

    for index, (got, want) in enumerate(zip(actual["daily_pnl"], expected["daily_pnl"])):
        assert abs(got - want) <= TOLERANCES["daily_pnl_abs_diff"], (index, got, want)
    assert abs(actual["total_pnl"] - expected["total_pnl"]) <= TOLERANCES["total_pnl_abs_diff"]
