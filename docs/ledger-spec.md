# Sequential ledger — interface spec (M4)

Source: monograph §07 "Nakit–pozisyon–fonlama muhasebesi", registry `accounting_ledger`, `first_experiment` (band, costs, delist), C-06 E (rounding). Golden cases are written from THIS file only, never from the implementation.

## Module `tycho.ledger`

```python
@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    contract_multiplier: float   # e.g. 1000 for 1000SHIBUSDT
    step_size: float             # quantity step in contracts
    min_notional: float          # USDT

def run_ledger(initial_wallet: float, instruments: dict[str, Instrument], events: list[dict]) -> list[dict]
def rebalance_orders(target_weights: dict, positions: dict, marks: dict, equity: float,
                     instruments: dict, band_relative: float, absolute_band_nav: float,
                     partial_ratio: float) -> dict   # instrument_id -> signed contract qty (0 omitted)
def slippage_bps(order_notional: float, daily_vol: float, adv_notional: float,
                 y: float = 1.0, min_bps: float = 2.0) -> float
```

## Events (dicts; `time` is a UTC ISO string)
| type | fields | effect |
|---|---|---|
| `MARK` | instrument_id, mark_price | updates mark; no cash |
| `FUNDING` | instrument_id, rate, mark_price | cashflow = −qty·mult·mark_price·rate added to wallet (long pays when rate > 0) |
| `FILL` | instrument_id, qty (signed contracts), price, fee_rate, slippage_bps | exec_price = price·(1 + sign(qty)·slippage_bps/1e4); fee = abs(qty)·mult·exec_price·fee_rate subtracted from wallet |
| `DELIST` | instrument_id, mark_price, fee_rate, slippage_bps (50 in E-001) | closes the whole position as a FILL of −qty at price = mark_price (slippage and fee applied as for FILL); position becomes 0 |

Ordering: events sorted by `time`; on equal time, priority MARK < FUNDING < DELIST < FILL (a position held at the funding instant pays funding even if closed at that same instant). Input order breaks remaining ties.

## Accounting
- Position: signed qty, average entry price. Increasing (same sign): entry = qty-weighted average of exec prices. Reducing: realized = (exec_price − entry)·closed_qty·mult·sign(position) added to wallet; entry unchanged. Crossing zero: close the full old position (realize), then open the remainder at exec_price.
- `wallet` = initial + realized − fees + funding (fees and funding never double counted).
- `unrealized` = Σ qty·mult·(mark − entry), using the latest mark (a FILL or DELIST also sets the mark to its `price` / `mark_price` if no MARK is later).
- `equity` = wallet + unrealized.
- `run_ledger` returns one snapshot dict per event, in processing order: `{time, type, instrument_id, wallet, equity, unrealized, realized_cum, fees_cum, funding_cum, positions: {id: qty}, entry: {id: price}}`.

## Rebalance (no-trade band, registry `band`)
For each instrument in target ∪ positions:
- `w_cur = qty·mult·mark/equity` (the drifted weight); `d = target − w_cur`.
- **Zero target or sign crossing** (target == 0 with qty ≠ 0, or sign(target) ≠ sign(w_cur) with both ≠ 0): trade the full `d` (band and partial ratio do not apply). *Coordinator interpretation of "include zero-crossing rules"; the monograph does not spell the rule out. Re-check before prereg.*
- Otherwise no trade if `abs(d) < max(band_relative·abs(target), absolute_band_nav)`; else trade `partial_ratio·d`.
- qty = trade_weight·equity/(mult·mark), rounded toward zero to a multiple of step_size. If abs(qty)·mult·mark < min_notional → no trade (rounded to zero; C-06 E). Exception: closing an entire position to zero target is allowed below min_notional (reduce to flat).
- `absolute_band_nav` and `partial_ratio` are PENDING in the monograph: required arguments, never defaulted.

## Slippage (registry costs: square-root model, min 2 bps)
`slippage_bps = max(min_bps, y · daily_vol · sqrt(order_notional / adv_notional) · 1e4)`, with daily_vol a daily (not annual) return std and adv_notional the average daily traded notional.
