"""Mechanical parity through the pinned NautilusTrader backtest engine (T7.1).

The engine does not decide: it executes a precomputed target-weight array on a
synthetic daily bar tape. Market fills are imposed by a fill model from the
supplied fee/slippage cost model, a positive executable latency is mandatory, and
the supplied funding schedule is applied to the simulated account. Daily and total
USDT PnL are then compared with an independent reference by the test.

This module never imports the research ledger and never computes expected PnL.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pandas as pd
from nautilus_trader.backtest.config import SimulationModuleConfig
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FeeModel, FillModel, LatencyModel
from nautilus_trader.backtest.modules import SimulationModule
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import (
    Bar,
    BarSpecification,
    BarType,
    BookOrder,
    FundingRateUpdate,
)
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    BarAggregation,
    BookType,
    OmsType,
    OrderSide,
    PriceType,
)
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

_UNLIMITED = 1_000_000


def _ns(time) -> int:
    return int(pd.Timestamp(time, tz="UTC").value) if not isinstance(time, int) else time


class _ImposedFills(FillModel):
    """Unlimited liquidity exactly at the cost-model price, never at the raw bar."""

    def __init__(self, slippage_bps: float):
        super().__init__()
        if slippage_bps < 0.0:
            raise ValueError("slippage_bps must be non-negative")
        self._slippage_bps = slippage_bps

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        reference = (
            best_ask.as_double() if order.side == OrderSide.BUY else best_bid.as_double()
        )
        sign = 1.0 if order.side == OrderSide.BUY else -1.0
        price = reference * (1.0 + sign * self._slippage_bps / 1e4)
        book = OrderBook(instrument.id, BookType.L2_MBP)
        for order_id, side in ((1, OrderSide.BUY), (2, OrderSide.SELL)):
            book.add(
                BookOrder(
                    side,
                    Price(price, instrument.price_precision),
                    Quantity(_UNLIMITED, instrument.size_precision),
                    order_id,
                ),
                0,
                0,
            )
        return book


class _Fees(FeeModel):
    """The same abs(qty)*multiplier*price*fee_rate charge as the cost model."""

    def __init__(self, fee_rate: float):
        if fee_rate < 0.0:
            raise ValueError("fee_rate must be non-negative")
        self._fee_rate = fee_rate

    def get_commission(self, order, fill_qty, fill_px, instrument):
        notional = abs(float(fill_qty)) * float(instrument.multiplier) * float(fill_px)
        return Money(notional * self._fee_rate, USDT)


class _FundingAndEquity(SimulationModule):
    """Apply the supplied funding schedule and snapshot Nautilus account equity.

    Nautilus 1.221 caches FundingRateUpdate data but does not post funding to the
    account, so this module posts the supplied cashflows through the exchange's
    documented adjust_account API at the supplied times.
    """

    def __init__(self, instrument, bar_times, funding):
        super().__init__(SimulationModuleConfig())
        self._instrument = instrument
        self._bar_times = set(bar_times)
        self._funding = sorted(funding, key=lambda event: _ns(event["time"]))
        self._next_funding = 0
        self._recorded = set()
        self.equity = []
        self.funding_paid = []

    def pre_process(self, data):
        pass

    def process(self, ts_now):
        while (
            self._next_funding < len(self._funding)
            and ts_now >= _ns(self._funding[self._next_funding]["time"])
        ):
            event = self._funding[self._next_funding]
            self._next_funding += 1
            positions = self.exchange.cache.positions_open(
                instrument_id=self._instrument.id
            )
            quantity = sum(
                float(position.quantity) if position.is_long else -float(position.quantity)
                for position in positions
            )
            amount = (
                -quantity
                * float(self._instrument.multiplier)
                * float(event["mark_price"])
                * float(event["rate"])
            )
            if amount:
                self.exchange.adjust_account(Money(amount, USDT))
            self.funding_paid.append((ts_now, amount))

        if ts_now in self._bar_times and ts_now not in self._recorded:
            self._recorded.add(ts_now)
            account = self.exchange.cache.account_for_venue(self._instrument.id.venue)
            equity = account.balance_total(USDT).as_double()
            mark = self.exchange.cache.price(self._instrument.id, PriceType.LAST)
            for position in self.exchange.cache.positions_open(
                instrument_id=self._instrument.id
            ):
                equity += position.unrealized_pnl(mark).as_double()
            self.equity.append((ts_now, equity))

    def log_diagnostics(self, logger):
        pass

    def reset(self):
        self._next_funding = 0
        self._recorded.clear()
        self.equity.clear()
        self.funding_paid.clear()


class _ExecuteTargets(Strategy):
    """Read the target array; submit at most one rebalance per fill."""

    def __init__(self, instrument, bar_type, targets):
        super().__init__()
        self._instrument = instrument
        self._bar_type = bar_type
        self._targets = targets
        self._index = 0
        self._pending = False
        self.decisions = []
        self.fills = []

    def on_start(self):
        self.subscribe_bars(self._bar_type)

    def on_bar(self, bar):
        index = self._index
        self._index += 1
        if self._pending or index >= len(self._targets):
            return

        target = self._targets[index]
        account = self.portfolio.account(bar.bar_type.instrument_id.venue)
        cash = account.balance_total(USDT).as_double()
        unrealized = self.portfolio.unrealized_pnl(
            bar.bar_type.instrument_id
        ).as_double()
        equity = cash + unrealized
        position = float(self.portfolio.net_position(bar.bar_type.instrument_id))
        mark = bar.close.as_double()
        multiplier = float(self._instrument.multiplier)
        step = self._instrument.size_increment.as_double()

        current_weight = position * multiplier * mark / equity if equity else 0.0
        raw = (target - current_weight) * equity / (multiplier * mark)
        quantity = math.trunc(raw / step + math.copysign(1e-9, raw)) * step
        if quantity == 0.0:
            return
        closing = target == 0.0 and position != 0.0
        notional = abs(quantity) * multiplier * mark
        if notional < self._instrument.min_notional.as_double() and not closing:
            return

        order = self.order_factory.market(
            bar.bar_type.instrument_id,
            OrderSide.BUY if quantity > 0.0 else OrderSide.SELL,
            Quantity(abs(quantity), self._instrument.size_precision),
        )
        self.submit_order(order)
        self._pending = True
        self.decisions.append((bar.ts_event, quantity, target))

    def on_order_filled(self, event):
        self._pending = False
        self.fills.append(
            (
                event.ts_event,
                "BUY" if event.order_side == OrderSide.BUY else "SELL",
                float(event.last_qty),
                float(event.last_px),
            )
        )


def run_nautilus_parity(
    instrument,
    bars,
    targets,
    funding,
    *,
    initial_wallet: float,
    fee_rate: float,
    slippage_bps: float,
    executable_latency_ns: int,
):
    """Run the pinned Nautilus backtest and return its account equity and fills.

    Parameters
    ----------
    instrument : nautilus_trader.model.instruments.CryptoPerpetual
        USDT-margined synthetic perpetual.
    bars : list[dict]
        Daily bars with ISO ``time`` (close), ``open``, ``high``, ``low``, ``close``.
    targets : list[float]
        Precomputed target weight per bar, aligned with ``bars``.
    funding : list[dict]
        Supplied funding events: ISO ``time``, ``rate``, ``mark_price``.
    initial_wallet, fee_rate, slippage_bps : float
        Synthetic account and cost-model inputs.
    executable_latency_ns : int
        Required positive order-to-exchange latency; zero/negative is refused.

    Returns
    -------
    dict with ``daily_pnl`` (first day measured from the initial wallet), ``total_pnl``,
    ``equity`` snapshots, ``fills``, and ``funding_paid``.
    """
    if len(targets) != len(bars):
        raise ValueError("targets must be aligned with bars")
    if executable_latency_ns <= 0:
        raise ValueError("executable_latency_ns must be positive (no same-close fill)")
    if initial_wallet <= 0.0:
        raise ValueError("initial_wallet must be positive")

    venue = instrument.id.venue
    bar_type = BarType(
        instrument.id,
        BarSpecification(1, BarAggregation.DAY, PriceType.LAST),
        AggregationSource.EXTERNAL,
    )
    bar_times = [_ns(bar["time"]) for bar in bars]
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId("PARITY-001"),
            logging=LoggingConfig(log_level="ERROR", bypass_logging=True),
            run_analysis=False,
        )
    )
    module = _FundingAndEquity(instrument, bar_times, funding)
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(initial_wallet, USDT)],
        default_leverage=Decimal(1),
        modules=[module],
        fill_model=_ImposedFills(slippage_bps),
        fee_model=_Fees(fee_rate),
        latency_model=LatencyModel(base_latency_nanos=executable_latency_ns),
        bar_execution=True,
    )
    engine.add_instrument(instrument)

    data = []
    for bar, timestamp in zip(bars, bar_times):
        data.append(
            Bar(
                bar_type,
                Price(bar["open"], instrument.price_precision),
                Price(bar["high"], instrument.price_precision),
                Price(bar["low"], instrument.price_precision),
                Price(bar["close"], instrument.price_precision),
                Quantity(bar.get("volume", 1), instrument.size_precision),
                timestamp,
                timestamp,
            )
        )
    for event in funding:
        timestamp = _ns(event["time"])
        data.append(
            FundingRateUpdate(
                instrument.id,
                Decimal(str(event["rate"])),
                timestamp,
                timestamp,
            )
        )
    engine.add_data(sorted(data, key=lambda item: item.ts_init))

    strategy = _ExecuteTargets(instrument, bar_type, targets)
    engine.add_strategy(strategy)
    engine.run()

    equity = [value for _, value in sorted(module.equity)]
    if len(equity) != len(bars):
        raise RuntimeError(f"expected {len(bars)} equity snapshots, got {len(equity)}")
    daily = [
        equity[0] - initial_wallet,
        *(later - earlier for earlier, later in zip(equity, equity[1:])),
    ]
    result = {
        "daily_pnl": daily,
        "total_pnl": equity[-1] - initial_wallet,
        "equity": equity,
        "fills": strategy.fills,
        "decisions": strategy.decisions,
        "funding_paid": module.funding_paid,
    }
    engine.dispose()
    return result
