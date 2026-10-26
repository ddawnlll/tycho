# Scanner public interface

Public callable: `tycho.scanner.scan(w, r, c, f)`.

- `w`: float64 `[variants, periods, assets]`, target weights decided at the end of period `t`; NaN targets mean zero.
- `r`, `c`, `f`: float64 `[periods, assets]`: simple period returns, proportional cost per unit traded weight, and accumulated funding rate (positive means long pays).
- Period zero enters from flat: trading cost, no gross return or funding. Subsequent periods hold the previous period's target weights, then rebalance using price-drifted weights.
- Return: dictionary of float64 `[variants, periods]` arrays: `pnl`, `gross`, `cost`, `funding`, `turnover`, `equity`. Equity starts from unit capital and compounds net period returns, including period zero.

This documents the existing public API, not an independent reference algorithm. The monograph §10 specifies the approximation; §07 sequential event accounting remains authoritative. General equality in the presence of fees, intraperiod position changes, rounding or funding is not guaranteed by the scanner approximation. A parity fixture must state its scope; a scientific contradiction must be reported, never hidden by adjusting frozen tolerances.
