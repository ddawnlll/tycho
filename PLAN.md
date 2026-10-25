# PLAN — Tycho DEV research package

Monograph phases: F1 = M1, F2 (null/DSR/power code) = M5, F3 = M2–M4, F4 = M6–M7. The order below is the owner's requested order and differs from the monograph's F2-before-F3. The F2 lock itself is OWNER-only either way.
Markers: `[x]` acceptance test passes · `[ ]` open · `[BLOCKED]` reason given · `[OWNER]` waits for the owner.

## M0 — Skeleton
- [x] CLAUDE.md, PLAN.md, PROGRESS.md, config/tycho.toml (PENDING kept), config/budget.toml
- [ ] T0.1 pyproject (uv; numpy, pandas, pyarrow, arch, statsmodels, pytest), `src/tycho` package, smoke test — `uv run pytest -q tests/test_smoke.py`
- [ ] T0.2 guard hook `.claude/hooks/guard.py` + deny rules: holdout/secrets/keys deny all; golden + prereg/locked write-once; guard files immutable; Binance data hosts blocked — `uv run pytest -q tests/test_guard.py`
- [ ] T0.3 `scripts/cleanup.py`: allowlist, dry-run by default, archives unmerged worktree patches to runs/archive/, never deletes the protected set — `uv run pytest -q tests/test_cleanup.py`
- [OWNER] OS-level lock of the guard, golden and prereg (chattr +i / separate user / branch protection); the hook is a heuristic guard only

## M1 — G0 FEASIBILITY-DATA (F1)
- [ ] T1.1 Tape schema (event_time, received_at, received_at_is_observed, available_at, source, revision, quality, instrument_id, instrument_spec_version), PIT loader with DEV clamp (refuses while dev_end_date PENDING; never touches data/holdout), deterministic synthetic tape generator (listings/delistings, 8h funding, mark, specs incl. 1000x multiplier, stablecoin/index symbols) — `tests/test_tape.py`
- [ ] T1.2 G0 audit `tycho.feasibility`: PIT listings incl. delisted, availability provenance, funding/mark coverage, contract filters + min history, monthly top-30 coverage; no returns or Sharpe; verdict PASS/BLOCKED/INCOMPLETE (BLOCKED while thresholds PENDING) — `tests/test_feasibility.py`
- [BLOCKED] G0 run on real Binance data — dev_end_date PENDING [OWNER], G0 sufficiency thresholds PENDING

## M2 — MarketState / Factor / ExpertSignal (F3)
- [ ] T2.1 contracts (MarketStateRequest v1, SnapshotRef, FactorFrame, ExpertManifest, ExpertSignal) + C-01 provider: available_at<=as_of, closed bars only, PIT top-30 universe, immutable snapshot_id, cache key (dataset_hash, as_of, universe_hash, request_hash, feature_code_hash) — `tests/test_marketstate.py` (future-event injection leaves past snapshots byte-identical; revisions; missing data → ABSTAIN metadata)
- [ ] T2.2 C-02 factors MA20/50/100/200, EWMA_VOL_60D (convention = required argument), trailing_30d_liquidity; versioned code hash — `tests/test_factors.py`
- [ ] T2.3 C-03 expert runtime + manifest hashing, ABSTAIN on missing/stale, no direct data access — `tests/test_expert_runtime.py`

## M3 — EXP-TREND-E001
- [ ] T3.1 EXP-TREND-E001: score = mean sign(P/MA_k − 1), k∈{20,50,100,200}, direction, abstain — `tests/test_exp_trend.py`
- [ ] T3.2 C-04/C-05: w = score·b/σ_EWMA, b = target/(N·√(ρ+(1−ρ)/N)), EWMA-cov forecast vol cap 2×target (proportional shrink), gross cap 2.0, one net target per symbol — `tests/test_portfolio.py`
- [ ] T3.3 core rule: full history vs trailing window gives the same last-row weights (window length is a test parameter) — `tests/test_window_equality.py`
- [BLOCKED] binding live window length: §08 (v1.1) says ≥400 bars; registry shared_factors.warmup says "not universal 400 bars", expert lookback = 260. Text/registry conflict.

## M4 — Sequential ledger + CPU numpy scanner
- [ ] T4.0 golden hand-calculated cases, written blind from the interface spec (funding at close, fee+slippage, open position across window edge, min-notional/step rounding, partial fill then cancel, delist exit mark+50bps, mark vs last, zero-crossing, no-trade band) — `tests/golden/`
- [ ] T4.1 authoritative sequential ledger (D-22): equity = wallet + uPnL@mark, funding = −signed notional × rate at the funding timestamp, fees from fills, band rule incl. zero-crossing + round-to-lot; partial ratio/abs band are required args — `tests/test_ledger.py` + golden
- [ ] T4.2 CPU numpy scanner: PnL = Σw·r − c·|w − w̃| − Σw·f with drifted w̃, vectorized over variants, float64 — `tests/test_scanner.py`
- [ ] T4.3 E-001 DEV runner (one command, synthetic tape end to end) + append-only hash-chained trial log `ledger/trials.jsonl` — `tests/test_runner.py`
- [BLOCKED] E-001 DEV on real data — dev_end_date, research nominal, absolute band, partial ratio, EWMA convention, latency PENDING; prereg lock [OWNER]

## M5 — Null / bootstrap / DSR (F2 code)
- [ ] T5.1 hand-written null generators: run-based direction scramble, random entry matching run lengths + gross exposure; baselines no-trade, equal-risk B&H (same b), BTC trend — `tests/test_nulls.py`
- [ ] T5.2 DSR (Bailey & López de Prado 2014, hand-written; N_eff and trial Sharpe variance are required args); stationary block bootstrap via `arch`; beta-adjusted one-sided 90% alpha with HAC via `statsmodels` — `tests/test_stats.py`
- [ ] T5.3 six-criteria gate evaluator; any BLOCKED → overall not PASS; holdout criterion always OWNER — `tests/test_gates.py`
- [ ] T5.4 synthetic joint power + seeded false positives, small local sample only — `tests/test_power.py`
- [BLOCKED] full joint-power run — MODAL_USD_CAP empty, N_eff PENDING

## M6 — Independent reference ledger parity (F4)
- [ ] T6.1 reference ledger written blind (clean context, must not read src/tycho ledger code); tolerance written to config before the first run; scanner ↔ reference ↔ sequential ledger parity — `tests/test_parity_reference.py`
- [ ] T6.2 full replay vs windowed replay equality — `tests/test_replay.py`

## M7 — Nautilus parity (F4, last)
- [ ] T7.1 pinned stable nautilus_trader; precomputed target weights executed with imposed fills + latency model on the synthetic tape; parity vs ledger within the pre-written tolerance — `tests/test_parity_nautilus.py`

## Verifier
Each milestone ends with a clean-context verifier run (reviews only, writes no code).
