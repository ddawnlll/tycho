# PLAN — Tycho DEV research package

Binding spec: [`docs/tycho-monograf-v1.3.2.html`](docs/tycho-monograf-v1.3.2.html) (monograph v1.3.2). Canonical decisions = its `#tycho-registry` JSON.
Tracking: every item below is a GitHub issue in [ddawnlll/tycho](https://github.com/ddawnlll/tycho/issues), grouped by [milestones](https://github.com/ddawnlll/tycho/milestones?state=all).
Issue forms live in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE) (agent task, verifier, defect, BLOCKED decision); PRs use [`.github/pull_request_template.md`](.github/pull_request_template.md).

## 1. Goals

**What Tycho is.** A systematic research system for Binance USDT-M perpetuals. Data enters only through a point-in-time **Tape**; Experts read only an as-of **MarketState**; one central Portfolio → Risk → Execution chain turns signals into targets (§01, §08A). Nothing goes live without passing a protected validation chain (§11).

**What this repo must deliver (DEV only).** Everything needed to run experiment **TYCHO-E-001** end to end on the synthetic tape, so that the day the owner fills the PENDING parameters and locks the pre-registration, the real run is one command:

1. **G0 data audit** (done) — can the data support the experiment, without looking at returns.
2. **E-001 pipeline** — `EXP-TREND-E001` signal (mean sign(P/MA_k − 1), k ∈ {20,50,100,200}) → per-asset EWMA-vol sizing with constant risk budget b → 2× target vol cap and 2.0 gross cap → band/partial trading → **sequential net-equity ledger** (authoritative, D-22) and a numpy scanner (approximation).
3. **Statistical gate code (G1/G3)** — baselines and exposure-preserving nulls, Deflated Sharpe, stationary block bootstrap, HAC beta-adjusted alpha, the six-criteria evaluator, synthetic joint power.
4. **Mechanical parity (G4)** — blind golden cases, an independent reference ledger, full vs windowed replay, and Nautilus executing the same targets.

**What success looks like.** Stop condition: every non-cancelled item is `[x]` (its acceptance test passes) and the M7 verifier passes. The research question itself — *does the price/MA ensemble with per-asset risk parity beat the baselines after real costs?* (§14) — is **not** answered here: it needs real data, the owner's prereg lock and the one-time holdout, all owner-only.

**Non-goals.** Live orders, exchange keys, stops/kill switch (E-002), Hermes/Telegram/Qdrant (§19), Modal runs (MODAL_USD_CAP empty), other Experts (EXP-MOM deferred, EXP-CARRY blocked), any parameter search.

## 2. Rules every worker follows (from CLAUDE.md and the monograph)

- **Never guess a PENDING parameter.** research nominal, absolute band NAV floor, partial trading ratio, EWMA convention, executable latency, max_staleness, DSR N_eff + trial Sharpe variance, bootstrap block rule, G0 thresholds, zero-crossing rule. Code takes them as **required arguments with no default**; real runs raise `PendingParameterError` while `config/tycho.toml` says `"PENDING"`. Tests use clearly labelled synthetic values.
- **Text vs registry conflict → BLOCKED.** Open a BLOCKED issue with both quotes; never pick an interpretation.
- **Synthetic tape only.** Never download data, never read `data/holdout/`. `tests/golden/` and `prereg/locked/` are write-once; if the ledger disagrees with a golden case, fix the code.
- **No look-ahead.** Anything used at decision time must have `available_at ≤ as_of` and come from closed bars.
- **No own engine.** pandas/numpy; `arch` for block bootstrap, `statsmodels` for HAC/alpha; hand-written only DSR and null generators.
- **Evidence over claims.** A PLAN item becomes `[x]` only when its acceptance test passes; paste the command and output in the PR.

## 3. How to read the monograph

The HTML is 360 KB; never read it whole. Find a section by anchor + phrase, then read only those lines:

```bash
grep -n 'Nakit–pozisyon–fonlama' docs/tycho-monograf-v1.3.2.html
```

Dump the canonical registry JSON once per session (it lives in `runs/`, which is gitignored):

```bash
uv run python -c "import re,html,json;s=open('docs/tycho-monograf-v1.3.2.html',encoding='utf-8').read();m=re.search(r'id=\"tycho-registry\"[^>]*>(.*?)</pre>',s,re.S);json.dump(json.loads(html.unescape(re.sub(r'<[^>]+>','',m.group(1)))),open('runs/registry.json','w'),ensure_ascii=False,indent=1)"
```

| § | Anchor | What it gives the code | Used by |
|---|---|---|---|
| 00, 02 | `#durum`, `#ilkeler` | Status, evidence tags, rules K-01…K-12 (prereg, no engine, exchange is truth) | all |
| 03 | `#piyasa` | Fees, funding, leverage arithmetic, portfolio-vol formula with ρ and N | T3.2 |
| 07 | `#matematik` | Cost/turnover, **normative cash–position–funding accounting** | T4.0, T4.1, T4.4 |
| 08 | `#mimari` | C-01…C-10 data flow, edge list, **core rule: full history vs window** | T3.3, T4.2 |
| 08A | `#expert` | Tape (3 clocks), MarketStateRequest, manifest, ExpertSignal, acceptance tests | M2, T3.1 |
| 09 | `#bilesenler` | C-01 universe details, C-02 trend score, C-04 b formula, C-05 caps, C-06 band/rounding | T3.1, T3.2, T4.1 |
| 10 | `#altyapi` | **Scanner formula** with drifted weights, Nautilus role and warnings | T4.2, T7.1 |
| 11 | `#dogrulama` | Gates G0–G9, baselines, bootstrap, parity, golden scenarios, protected judge, joint power | M5, M6 |
| 14 | `#e001` | **E-001 prereg draft**: universe, signal, sizing, fill, costs, baselines, six criteria, outcomes | M3–M5 |
| 15–16 | `#yol`, `#acik` | Phases F0–F9, open questions Q-xx and parameters P-xx, PENDING list | all |
| 18 | `#kayit` | Registry JSON (canonical) | all |
| 19–19G | `#sirket` … | Hermes / Telegram / agent org — **out of scope** | — |

Registry paths used most: `first_experiment` (signal, sizing, fill, costs, band, baselines, pass_all, outcomes, preconditions), `market_state_architecture`, `experts`, `accounting_ledger`, `scientific_judge`, `decisions` (D-04 scanner, D-06 vol scaling, D-22 ledger, D-24 DSR/power), `feasibility`.

Phase mapping: monograph F1 = M1, F2 (null/DSR/power code) = M5, F3 = M2–M4, F4 = M6–M7. The order here is the owner's (F3 before F2 code); the F2 prereg lock is owner-only either way.

## 4. Worker protocol (one issue = one session)

1. `gh issue view <n>` — the issue is self-contained: goal, monograph reading list, files, contract, acceptance command, PENDING list, out of scope, dependencies.
2. Check every "Depends on" issue is closed. If not, stop.
3. Read CLAUDE.md, then only the monograph sections listed. Explore the listed files; reuse existing helpers (`config.require`, `contracts.canonical_hash`, `MarketStateProvider.resolve`, `factors.compute_factors/ewma_cov`, `runtime.run_expert`, `synthetic.make_synthetic_tape`).
4. Plan, implement the smallest change that meets the contract, write the acceptance test.
5. Run the acceptance command and `uv run pytest -q`. Open a PR with `Closes #<n>`; set the PLAN item to `[x]`; add decisions/worker notes to PROGRESS.md.
6. Something underspecified or conflicting → open a **BLOCKED / owner decision** issue and keep the value a required argument.
7. Each milestone ends with its **verifier issue**, run in a fresh context that wrote none of the code. Findings become new task/bug issues in the same milestone.

Markers: `[x]` acceptance test passes · `[ ]` open · `[CANCELLED owner 2026-10-25]` owner cancelled every item that waited on an owner decision · `[BLOCKED]` reason given.

## 5. Dependency order

### Autonomous DEV completion — 2026-10-25

Starting main: `92d89e3f6f922aa81bbaeed954a3276e169aa481` (T5.2 already merged; #29 closed).
Wave 1: #43 (slot A), genuinely blind #35 (slot B), #28 (slot C), coordinator verification/reconciliation (slot D).
Then follow `docs/dispatch-plan.md`: independent verification before closure, exact acceptance and full suite on integrated main, one PR per task, PLAN/PROGRESS updated together after each merge.
No Modal execution: skill/PLAN prohibit it and the budget is owner-only; small synthetic checks stay local. Undefined scientific rules remain BLOCKED, never guessed.
Owner decisions discovered: #45 (zero-crossing policy), #46 (null/baseline preservation rules). Public scanner interface supplied in `docs/scanner-interface.md` for blind-worker request #47.
Additional blockers: #51 (1h execution tape), #52 (manifest text/registry conflict), #53 (cost-bearing exact scanner parity). V0/V1/V4 fixes: #48/#58, #49, #50. #30 implementation is in PR #54 with closed-bar review fix #56; no unchecked item is marked complete.

```text
M0 T0.5 ─► V0
M1 (done) ─► V1
M2 (done) ─► V2 ─► M3: T3.1 ─► T3.2 ─► T3.3 ─► V3
                    T3.2 ─► T4.0 (blind golden) ─► T4.1 ledger ─┐
                    T3.2 ─► T4.2 scanner ───────────────────────┼─► T4.3 runner + trial log ─► T4.4 diagnostics ─► V4
                                                                │
                 T4.3 ─► T5.1 nulls ┐                           │
                 T4.3 ─► T5.2 stats ├─► T5.4 gates ─► T5.5 power ─► V5
                 T4.3 ─► T5.3 subp. ┘                           │
          T4.1 + T4.2 ─► T6.1 reference parity ─► T7.1 Nautilus ─► V7 (stop condition)
                 T4.3 ─► T6.2 replay ─► V6
```

Parallel-safe groups (no shared files): {T4.0, T4.2} after T3.2; {T5.1, T5.2, T5.3, T6.2} after T4.3. T6.1 must be written by a worker that has not read `src/tycho/ledger.py` or `scanner.py`; T4.0 by one that has not read any ledger code.

## 6. Milestones and issues


### M0 — Skeleton & guard — [milestone](https://github.com/ddawnlll/tycho/milestone/1)

Package, config with PENDING kept, guard hook, cleanup script. Open: 2/7.

- [x] **T0.1** pyproject (uv), src/tycho package, smoke test — [#1](https://github.com/ddawnlll/tycho/issues/1)  
  Commit 2b8e292; suite green at merge (18 passed after M0).
  Acceptance: `uv run pytest -q tests/test_smoke.py`
- [x] **T0.2** Guard hook + deny rules (holdout, secrets, golden/prereg write-once) — [#2](https://github.com/ddawnlll/tycho/issues/2)  
  Commit f8f8ec3.
  Acceptance: `uv run pytest -q tests/test_guard.py`
- [x] **T0.3** scripts/cleanup.py: allowlisted, dry-run default, archives worktrees — [#3](https://github.com/ddawnlll/tycho/issues/3)  
  Commit d4169ab.
  Acceptance: `uv run pytest -q tests/test_cleanup.py`
- [x] **T0.5** cleanup.py fixes from the M0 verifier — [#4](https://github.com/ddawnlll/tycho/issues/4)
  - **Goal:** Close the five M0 verifier findings so cleanup can never lose work or escape the repo.
  - **Read in the monograph:**
    - Not a monograph item. Findings: PROGRESS.md → "M0 verifier (wf_4e6413d1-3f8): FAIL"
  - **Files:**
    ```text
    read+change: scripts/cleanup.py; tests/test_cleanup.py (add one test per finding)
    ```
  - **Contract:**
    1. **Untracked files** (blocker): before removing a worktree, archive tracked diff *and* untracked files (`git ls-files --others --exclude-standard`) into runs/archive/<worktree>/; removal aborts if archiving fails.
    2. **Symlink escape**: resolve every candidate path (`Path.resolve()`); refuse anything outside the repo root or inside the protected set.
    3. **Hardcoded `main`**: read the base branch from the main worktree's current branch (or `--base`), never a literal.
    4. **Locked worktrees**: skip with a printed reason; never `--force`/unlock; never crash.
    5. **--repo must be the main worktree**: refuse when `git rev-parse --git-dir` ≠ `--git-common-dir`.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_cleanup.py
    ```
  - **PENDING / BLOCKED:** —
  - **Out of scope:** Guard hook changes (owner cancelled); deleting anything under the protected set.
  - **Depends on:** T0.3 [#3](https://github.com/ddawnlll/tycho/issues/3)
- [CANCELLED owner 2026-10-25] **X0.a** Guard hardening (relative-path Bash, python3 -c, rm -rf .claude, Grep/Glob glob field) — [#5](https://github.com/ddawnlll/tycho/issues/5) · BLOCKED  
  M0 verifier found heuristic bypasses. The guard protects itself, so only the owner can change it. Owner cancelled 2026-10-25; known residual risk.
- [CANCELLED owner 2026-10-25] **X0.b** OS-level lock of guard, golden and prereg (chattr +i / separate user / branch protection) — [#6](https://github.com/ddawnlll/tycho/issues/6) · BLOCKED  
  Monograph wants separate credentials for the protected judge. Owner cancelled 2026-10-25; the hook stays a heuristic guard.
- [x] **T0.6 / T0.7** Preserve locked/archival-failed worktrees, including generic-directory overlaps — #48 / #58, commits `74fe1ef` / `8c69f50`. Independent Astra PASS (12 adversarial probes); integrated acceptance: 21 passed; full suite: 196 passed.
- [x] **V0** M0 verifier re-run after T0.5 — [#7](https://github.com/ddawnlll/tycho/issues/7) · verifier
  - **Goal:** First run (wf_4e6413d1-3f8) = FAIL → T0.5. Re-verify cleanup.py after the fixes. Guard bypasses are a known, owner-cancelled residual risk — do not re-report them.
  - **Read in the monograph:**
    - CLAUDE.md protected set
  - **Files:**
    ```text
    scripts/cleanup.py, tests/test_cleanup.py
    ```
  - **Contract:**
    Try to make cleanup lose an untracked file, follow a symlink out of the repo, or run from a linked worktree.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T0.5 [#4](https://github.com/ddawnlll/tycho/issues/4)

### M1 — G0 FEASIBILITY-DATA (F1) — [milestone](https://github.com/ddawnlll/tycho/milestone/2)

Tape schema, PIT loader, synthetic tape, G0 data audit. No returns, no Sharpe. Open: 1/4.

- [x] **T1.1** Tape schema, PIT loader with DEV clamp, synthetic tape generator — [#8](https://github.com/ddawnlll/tycho/issues/8)  
  Commit 049e640.
  Acceptance: `uv run pytest -q tests/test_tape.py`
- [x] **T1.2** G0 FEASIBILITY-DATA audit (tycho.feasibility) — [#9](https://github.com/ddawnlll/tycho/issues/9)  
  Commit 1f15ca9. Worker notes: funding expected count uses latest spec interval; history counted in distinct bar days.
  Acceptance: `uv run pytest -q tests/test_feasibility.py`
- [x] **T1.3** G0 audit fixes from the M1 verifier — [#40](https://github.com/ddawnlll/tycho/issues/40)  
  Commit e28bcaa. PIT universe (available_at), survivor-only tape fails, contract filters, per-instrument mark coverage, PIT synthetic delisting.
  Acceptance: `uv run pytest -q tests/test_feasibility.py tests/test_tape.py`
- [CANCELLED owner 2026-10-25] **X1.a** G0 run on real Binance data — [#10](https://github.com/ddawnlll/tycho/issues/10) · BLOCKED  
  Needs dev_end_date [OWNER] and G0 sufficiency thresholds (PENDING). Owner cancelled 2026-10-25.
- [x] **T1.4** Reject invalid/non-finite G0 thresholds — #49, `6fb1f20`; independent Astra PASS, integrated acceptance 89 passed, full suite 252 passed.
- [x] **V1** M1 verifier (G0 tape + audit) — [#11](https://github.com/ddawnlll/tycho/issues/11) · verifier
  - **Goal:** The first M1 verifier run was interrupted by /exit and the re-run result is not recorded. Verify T1.1/T1.2 from a clean context.
  - **Read in the monograph:**
    - §08A.2 tape contract; §14 G0 scope; registry `feasibility.data.prohibited`
  - **Files:**
    ```text
    src/tycho/tape.py, src/tycho/synthetic.py, src/tycho/feasibility.py + tests
    ```
  - **Contract:**
    Check: loader cannot see available_at > as_of; delisted symbols survive; audit never computes returns; BLOCKED while thresholds PENDING; funding-interval note from T1.2.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T1.1 [#8](https://github.com/ddawnlll/tycho/issues/8), T1.2 [#9](https://github.com/ddawnlll/tycho/issues/9)

### M2 — MarketState / Factor / ExpertSignal (F3) — [milestone](https://github.com/ddawnlll/tycho/milestone/3)

§08A contracts, C-01 provider, C-02 factors, C-03 runtime. Open: 1/4.

- [x] **T2.1** §08A contracts + C-01 MarketState provider — [#12](https://github.com/ddawnlll/tycho/issues/12)  
  Commit b6501ea.
  Acceptance: `uv run pytest -q tests/test_marketstate.py`
- [x] **T2.2** C-02 shared factors (MA, EWMA vol/cov, liquidity) with code hash — [#13](https://github.com/ddawnlll/tycho/issues/13)  
  Commit ffe2c7c. Worker note: ewma_cov has no PSD repair (matters for the 2x vol cap, see T3.2).
  Acceptance: `uv run pytest -q tests/test_factors.py`
- [x] **T2.3** C-03 expert runtime + manifest hashing, ABSTAIN enforcement — [#14](https://github.com/ddawnlll/tycho/issues/14)  
  Commit 226393d.
  Acceptance: `uv run pytest -q tests/test_expert_runtime.py`
- [ ] **V2** M2 verifier (MarketState / Factor / Runtime) — [#15](https://github.com/ddawnlll/tycho/issues/15) · verifier
  - **Goal:** No M2 verifier has run. Verify the §08A.8 acceptance table from a clean context before M3 builds on it.
  - **Read in the monograph:**
    - §08A.8 `#expert` → "Kabul testleri ve performans" (PIT isolation, shared cache, expert purity)
    - registry `market_state_architecture`
  - **Files:**
    ```text
    src/tycho/contracts.py, marketstate.py, factors.py, runtime.py + tests
    ```
  - **Contract:**
    Inject future tape events → past snapshots byte-identical; same factor hash → same value; expert cannot reach network/keys; EWMA convention never defaulted; ewma_cov PSD behaviour documented.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T2.1 [#12](https://github.com/ddawnlll/tycho/issues/12), T2.2 [#13](https://github.com/ddawnlll/tycho/issues/13), T2.3 [#14](https://github.com/ddawnlll/tycho/issues/14)

- [x] **T2.4** — [#41](https://github.com/ddawnlll/tycho/issues/41) M2 verifier fixes: expert gets copies and enforcement uses pre-call availability; dataset_hash from the as_of-visible data; real feature_code_hash factor cache (5-element key); closed-bar test; stablecoin with volume excluded; empty universe → raise; ewma_cov NaN alignment — `uv run pytest -q tests/test_marketstate.py tests/test_expert_runtime.py tests/test_factors.py`
- [x] **T2.5** — [#42](https://github.com/ddawnlll/tycho/issues/42) M2 re-verify fixes: FactorFrame cache returns copies / expert gets a copy; ordered feature hash; timeframe validation tests; quality != ok → ABSTAIN — `uv run pytest -q tests/test_marketstate.py tests/test_expert_runtime.py tests/test_factors.py`

## M3 — EXP-TREND-E001 + portfolio (F3) — [milestone](https://github.com/ddawnlll/tycho/milestone/4)

Trend Expert signal, C-04/C-05 sizing and caps, window-equality core rule. Open: 4/5.

- [x] **T3.1** EXP-TREND-E001 expert: mean sign(P/MA_k − 1) — [#16](https://github.com/ddawnlll/tycho/issues/16)
  - **Goal:** The only active Expert of E-001: a pure function from a MarketState snapshot + FactorFrame to ExpertSignal[] (score, direction, ABSTAIN). Never an order, never a weight.
  - **Read in the monograph:**
    - §08A.3 `#expert` → "Her Expert kendi MarketStateRequest'ini bildirir" (the JSON is E-001's binding input contract)
    - §08A.4 → "Expert manifest ve çıktı şeması" (ExpertSignal fields; ABSTAIN rule)
    - §09 C-02 `#bilesenler` → "Trend skoru"; C-03 → "Sözleşme: strateji(piyasa_penceresi, durum, önceki_hedef)" (T ignores previous target)
    - §14 `#e001` → row "Sinyal" (K={20,50,100,200}, variants=1, not searched)
    - registry `experts[0]`, `first_experiment.signal`, `market_state_architecture.expert_runtime`
  - **Files:**
    ```text
    read: src/tycho/contracts.py (MarketStateRequest, ExpertManifest, ExpertSignal), src/tycho/runtime.py (make_manifest, run_expert), src/tycho/factors.py (compute_factors), src/tycho/marketstate.py
    new: src/tycho/exp_trend.py, tests/test_exp_trend.py
    ```
  - **Contract:**
    - Request exactly as §08A.3: universe `USDTM_TOP30_PIT`, 1d, lookback 260 closed bars, fields close/volume, factors ma_20/50/100/200 + ewma_vol_60d, cross_section listing_state/contract_spec/trailing_30d_liquidity, point_in_time, closed_bars_only, on_missing=ABSTAIN_AND_REPORT; `max_staleness` is a required argument.
    - score_i = mean over k of sign(P_i / MA_k,i − 1) ∈ {−1, −0.5, …, 1} (P = last closed 1d close available at as_of).
    - direction: LONG if score>0, SHORT if score<0, FLAT if 0; ABSTAIN (score=null + abstain_reason) when any MA/price is missing, stale or lookback incomplete. A previous signal is never extended.
    - Pure: same manifest + snapshot → byte-identical signals. No confidence field (§08A.4).
    - Run through `run_expert`; the expert never touches the tape directly.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_exp_trend.py
    # cases: hand-computed score on a 4-instrument frame covering all 5 score levels; ABSTAIN with <200 bars; ABSTAIN on stale bar; determinism (run twice, compare hashes); manifest hash changes when params change
    ```
  - **PENDING / BLOCKED:** max_staleness (config execution.max_staleness), ewma_convention (only passed through to factors) → required args, no defaults.
  - **Out of scope:** Sizing/weights (T3.2); any return, PnL or Sharpe; other experts (EXP-MOM/CARRY are DEFERRED/BLOCKED); parameter search.
  - **Depends on:** T2.1 [#12](https://github.com/ddawnlll/tycho/issues/12), T2.2 [#13](https://github.com/ddawnlll/tycho/issues/13), T2.3 [#14](https://github.com/ddawnlll/tycho/issues/14)
- [x] **T3.2** C-04/C-05 portfolio: w = score·b/σ_EWMA, 2× vol cap, 2.0 gross cap — [#17](https://github.com/ddawnlll/tycho/issues/17)
  - **Goal:** Turn ExpertSignal[] into one net target weight per symbol with the pre-registered risk caps, and report every clip.
  - **Read in the monograph:**
    - §03 `#piyasa` → "Kaldıracın gerçek anlamı" (portfolio vol = gross × σ × √(ρ+(1−ρ)/N))
    - §09 C-04 `#bilesenler` → "Portföy oynaklığı: b = hedef / (N · √(ρ + (1−ρ)/N))" and D-06
    - §09 C-05 → pre-trade table (gross cap, forecast vol cap 2× target)
    - §14 `#e001` → row "Boyutlandırma" (cap at 40% forecast vol, gross 2x)
    - registry `first_experiment.sizing`, `decisions[D-06]`, `sleeve_risk_budget` (v1: T = 1.0), `market_state_architecture.portfolio`
  - **Files:**
    ```text
    read: src/tycho/factors.py (ewma_cov, EWMA_VOL_60D; 365-day annualization), src/tycho/contracts.py (ExpertSignal), config/tycho.toml [sizing]
    new: src/tycho/portfolio.py, tests/test_portfolio.py
    ```
  - **Contract:**
    - b = target_vol / (N·√(ρ + (1−ρ)/N)) with target_vol=0.20, ρ=0.7, N=`universe.size`=30, constant over time (b "zamanla değişmez"). b ≈ 0.007912.
    - raw w_i = score_i · b / σ_i (σ_i = annualized EWMA_VOL_60D). Instruments outside the PIT universe → 0.
    - Vol cap: σ_p = √(wᵀΣw) with the EWMA covariance; if σ_p > 2×target (0.40) scale the whole book by 0.40/σ_p. No other portfolio-level vol timing (D-06).
    - Gross cap: Σ|w| ≤ 2.0. The monograph gives only the cap; apply the same proportional shrink and record it as a coordinator decision in PROGRESS.md.
    - If wᵀΣw < 0 (ewma_cov has no PSD repair) raise; never repair silently.
    - Output: target weights + clip reasons (which cap, factor). One net target per symbol (one-way mode).
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_portfolio.py
    # cases: b numeric value; 2-asset hand example; vol cap scales exactly to 0.40; gross cap; no change when caps are slack; ABSTAIN handling via the required arg; non-PSD raises
    ```
  - **PENDING / BLOCKED:** - ewma_convention (PENDING) → required arg.
    - ABSTAIN → weight: the monograph forbids extending the previous signal but does not say flat vs hold. Make `on_abstain` a required argument with no default and open a BLOCKED issue if a real run needs it.
  - **Out of scope:** Account stress rule, per-asset cap (no number in the monograph), drawdown alerts, Kelly (P-04 unused), M/C sleeves, execution/band logic (T4.1).
  - **Depends on:** T3.1 [#16](https://github.com/ddawnlll/tycho/issues/16)
- [x] **T3.3** Core rule: full history vs trailing window → identical last-row weights — [#18](https://github.com/ddawnlll/tycho/issues/18)
  - **Goal:** Prove the §08 core rule: the same pure C-02…C-05 code applied to full history and to a trailing window gives the same last-row weights, so strategy code is never written twice.
  - **Read in the monograph:**
    - §08 `#mimari` → "Çekirdek kuralı: tüm geçmişe uygula, son satırı al" and "İki koşul (v1.1)"
    - registry `market_state_architecture.shared_factors.warmup` ("per factor param and tolerance, not universal 400 bars")
    - BLOCKED item X3.a (window length conflict)
  - **Files:**
    ```text
    read: src/tycho/exp_trend.py, src/tycho/portfolio.py, src/tycho/factors.py
    new: tests/test_window_equality.py
    ```
  - **Contract:**
    - Window length is a **test parameter** (pytest parametrize), not a binding constant: the binding value is BLOCKED (X3.a).
    - Assert last-row weights equal within a tolerance written in the test before running it; for EWMA the tolerance follows from the convention's decay (document the derivation in the test).
    - Show the failure mode too: a window too short for MA200 must ABSTAIN, not produce a different weight.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_window_equality.py
    ```
  - **PENDING / BLOCKED:** Binding live window length → BLOCKED (X3.a). ewma_convention → test fixture value, clearly marked synthetic.
  - **Out of scope:** Choosing a binding window length; editing config.
  - **Depends on:** T3.1 [#16](https://github.com/ddawnlll/tycho/issues/16), T3.2 [#17](https://github.com/ddawnlll/tycho/issues/17)
- [x] **T3.4** M3 verifier fixes: PIT leak in trend_panels, cov timing, NaN close, on_abstain — [#43](https://github.com/ddawnlll/tycho/issues/43), `d829405`; independent Astra PASS for all five findings, integrated acceptance 39 passed / full suite 271 passed.
- [ ] **T3.5** Delayed-current-bar readiness must agree between panel and snapshot — #61 (new independent V3 finding; ahead of dependent replay work).
  - **Goal:** Close the M3 verifier findings (wf_4bf0a3cb-a4f); the blocker is a point-in-time leak in the vectorized full-history path.
  - **Contract:** (1) blocker: `vectorized.trend_panels` builds from the end-of-tape view, so later revisions leak into earlier rows; (2) cov evaluated one bar before the decision time — add a test where the vol cap binds; (3) NaN latest close must ABSTAIN, not fall back; (4) `on_abstain` required argument, no default; (5) same MA NaN handling in panel and snapshot paths.
  - **Acceptance:** `uv run pytest -q tests/test_window_equality.py tests/test_portfolio.py tests/test_exp_trend.py`
  - **Depends on:** T2.5 [#42](https://github.com/ddawnlll/tycho/issues/42) (shared factors.py)
- [CANCELLED owner 2026-10-25] **X3.a** Binding live window length: §08 "≥400 bars" vs registry warmup "not universal 400" / lookback 260 — [#19](https://github.com/ddawnlll/tycho/issues/19) · BLOCKED  
  Text/registry conflict → BLOCKED per CLAUDE.md. Owner cancelled the resolution 2026-10-25; T3.3 keeps window length as a test parameter.
- [ ] **V3** M3 verifier (EXP-TREND-E001 + portfolio) — [#20](https://github.com/ddawnlll/tycho/issues/20) · verifier
  - **Goal:** Clean-context verification of the signal and sizing against §08A/§09/§14 before any ledger work uses them.
  - **Read in the monograph:**
    - §08A.3–08A.4, §09 C-02…C-05, §14 Sinyal/Boyutlandırma rows; registry `first_experiment.signal|sizing`
  - **Files:**
    ```text
    src/tycho/exp_trend.py, src/tycho/portfolio.py + tests
    ```
  - **Contract:**
    Check: no look-ahead (P and MA from bars available at as_of); b constant; cap math; no hidden defaults for PENDING; tests would fail if the formula changed.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T3.1 [#16](https://github.com/ddawnlll/tycho/issues/16), T3.2 [#17](https://github.com/ddawnlll/tycho/issues/17), T3.3 [#18](https://github.com/ddawnlll/tycho/issues/18)

### M4 — Sequential ledger + CPU scanner (F3) — [milestone](https://github.com/ddawnlll/tycho/milestone/5)

Golden cases, authoritative D-22 ledger, numpy scanner, one-command DEV runner, trial log. Open: 6/7.

- [x] **T4.0** Golden hand-calculated ledger cases (written blind) — [#21](https://github.com/ddawnlll/tycho/issues/21)
  - **Goal:** Small hand-computed cases that the sequential ledger must reproduce. Written from the interface spec only, by a worker that has not read and must not read src/tycho ledger code. tests/golden/ is write-once.
  - **Read in the monograph:**
    - §07 `#matematik` → "Nakit–pozisyon–fonlama muhasebesi: normatif sözleşme" (equity, notional, weight, funding, fees formulas; double-count rule)
    - §11 `#dogrulama` → "Ek testler" row "Altın senaryolar"
    - §09 C-01 → delist exit "mark fiyatı + 50 bps"; C-06 E → "yuvarlama sonrası sıfıra düşen hedef işlem görmez"
    - §14 row "Karar ve dolum" (band rule); registry `first_experiment.band`, `accounting_ledger`
  - **Files:**
    ```text
    new: tests/golden/*.json (one file per case: inputs, policy args, expected ledger rows), tests/golden/README.md (units, sign conventions)
    ```
  - **Contract:**
    Cases (each with inputs and expected equity/wallet/fees/funding/position after every event):
    1. funding paid at the funding timestamp while a position is open at that instant (cashflow = −signed_notional × rate)
    2. taker fee + slippage on a fill
    3. position open across a window edge
    4. min-notional / step rounding; target that rounds to 0 is not traded
    5. partial fill then cancel
    6. delist exit at mark + 50 bps
    7. mark vs last price (uPnL at mark)
    8. zero-crossing (long → short) under an explicit policy value
    9. no-trade band: |target − drifted| < max(0.25·|target|, abs_floor)
    Every PENDING parameter (abs_floor, partial ratio, zero-crossing rule, nominal) appears as an explicit input value of the case, marked synthetic.
  - **Acceptance:**
    ```bash
    uv run python -c "import json,glob; [json.load(open(f)) for f in glob.glob('tests/golden/*.json')]"  # cases parse; consumed by T4.1
    ```
  - **PENDING / BLOCKED:** All PENDING values are case inputs, never library defaults.
  - **Out of scope:** Reading src/tycho ledger/scanner code; editing a golden file after it is written (guard: write-once).
  - **Depends on:** T3.2 [#17](https://github.com/ddawnlll/tycho/issues/17)
- [x] **T4.1** Authoritative sequential ledger (D-22) — [#22](https://github.com/ddawnlll/tycho/issues/22)
  - **Goal:** The single source of truth for net results: an event-ordered account ledger (fills, fees, funding, mark, delist) that the vector scanner is only an approximation of.
  - **Read in the monograph:**
    - §07 `#matematik` → "Nakit–pozisyon–fonlama muhasebesi: normatif sözleşme" (all formulas + "İşaret ve çifte sayım")
    - §09 C-06 `#bilesenler` → layer A (band + partial ratio) and E (min notional, step, zero target)
    - §14 `#e001` → rows "Karar ve dolum", "Maliyet"
    - registry `accounting_ledger`, `first_experiment.band|fill|costs`, `decisions[D-22]`
  - **Files:**
    ```text
    read: tests/golden/ (T4.0), src/tycho/tape.py (funding, mark, specs), src/tycho/portfolio.py
    new: src/tycho/ledger.py, tests/test_ledger.py
    ```
  - **Contract:**
    - equity = wallet_balance + uPnL@mark; notional = qty × multiplier × mark; weight = notional / equity.
    - funding cashflow = −signed_notional_at_funding_timestamp × rate, using the position at that instant (never bar start).
    - fees from executed fills (qty × price × multiplier × fee_rate); taker 0.0005; slippage square-root, min 2 bps (Y=1).
    - Realized PnL/fees/funding already in wallet are never added again.
    - Band: no trade if |target − drifted| < max(0.25·|target|, absolute_band_nav); outside, trade partial_trading_ratio × difference; round to step; skip if below min notional or rounds to 0.
    - Fills: decision after the closed 1D bar's available_at + positive latency; first executable price + slippage.
    - Delist: close at mark + 50 bps on the last trading day.
    - Every golden case in tests/golden/ passes unchanged.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_ledger.py   # includes all tests/golden/ cases
    ```
  - **PENDING / BLOCKED:** research_notional_usd, absolute_band_nav, partial_trading_ratio, executable_latency, zero-crossing rule (monograph: "ayrı kural", undefined) → required args; add `zero_crossing_rule = "PENDING"` to config [execution].
  - **Out of scope:** Margin/liquidation/ADL simulation, stops (E-002), maker orders (deferred), editing golden cases.
  - **Depends on:** T4.0 [#21](https://github.com/ddawnlll/tycho/issues/21)
- [x] **T4.2** CPU numpy scanner with drifted weights — [#23](https://github.com/ddawnlll/tycho/issues/23)
  - **Goal:** Vectorised research formula over variants (v), time (t), assets (a) in float64 — a verifiable formula, not an engine (~200–300 lines).
  - **Read in the monograph:**
    - §10 `#altyapi` → "Tarama formülü (CPU'da numpy, gerekirse GPU)" (formula + why w̃ not w[t−1])
    - §08 Şekil 8c (research loop)
    - registry `decisions[D-04]`
  - **Files:**
    ```text
    read: src/tycho/portfolio.py, src/tycho/ledger.py (cost model only)
    new: src/tycho/scanner.py, tests/test_scanner.py
    ```
  - **Contract:**
    PnL[v,t] = Σ_a w[v,t−1,a]·r[t,a] − Σ_a c[t,a]·|w[v,t,a] − w̃[v,t,a]| − Σ_a w[v,t−1,a]·f[t,a]
    w̃[v,t,a] = w[v,t−1,a]·(1+r[t,a]) / (1 + Σ_b w[v,t−1,b]·r[t,b])
    c = commission + square-root slippage + rounding. float64 throughout. Vectorised over v.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_scanner.py
    # cases: 1-asset hand example; drift term; funding sign; v=3 variants equal 3 single runs; dtype float64
    ```
  - **PENDING / BLOCKED:** research_notional_usd (needed for √(order/volume) slippage) → required arg.
  - **Out of scope:** GPU/JAX (only after a measured CPU bottleneck, D-04); ledger parity (M6).
  - **Depends on:** T3.2 [#17](https://github.com/ddawnlll/tycho/issues/17)
- [x] **T4.3** E-001 DEV runner (one command) + append-only hash-chained trial log — [#24](https://github.com/ddawnlll/tycho/issues/24)
  - **Goal:** One deterministic command runs the whole E-001 pipeline on the synthetic tape and appends every run (losers included, K-03) to ledger/trials.jsonl.
  - **Read in the monograph:**
    - §15 `#yol` → F3 row ("Tek komutla deterministik DEV run")
    - §02 `#ilkeler` K-03 (log every variant)
    - §09 C-10 → "Araştırma arşivi" (hypothesis, params, data and code version, result, trial counter)
    - registry `scientific_judge.pass_attestation` (hashes to record)
  - **Files:**
    ```text
    read: tape.py, marketstate.py, exp_trend.py, portfolio.py, ledger.py, scanner.py, config.py
    new: src/tycho/runner.py (`uv run python -m tycho.runner --synthetic`), tests/test_runner.py, ledger/trials.jsonl (append-only)
    ```
  - **Contract:**
    - Pipeline: synthetic tape → provider → factors → EXP-TREND-E001 → portfolio → ledger (authoritative) + scanner (approx).
    - Each trial record: trial_id, prev_hash, record_hash, dataset_hash, code hash, manifest/request hash, params, PENDING placeholders used, metrics, timestamp.
    - Append-only: verify the hash chain on every write; refuse to write if the chain is broken.
    - Real-data mode refuses while any PENDING value is unset.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_runner.py
    # cases: two runs → identical metrics; chain verifies; tampered line detected; real mode raises PendingParameterError
    ```
  - **PENDING / BLOCKED:** All E-001 PENDING values → synthetic placeholders only in --synthetic mode, recorded in the trial record.
  - **Out of scope:** Real data; Modal; DuckDB archive.
  - **Depends on:** T3.1 [#16](https://github.com/ddawnlll/tycho/issues/16), T3.2 [#17](https://github.com/ddawnlll/tycho/issues/17), T4.1 [#22](https://github.com/ddawnlll/tycho/issues/22), T4.2 [#23](https://github.com/ddawnlll/tycho/issues/23)
- [ ] **T4.4** E-001 diagnostics: constant-notional variant, 1h delay, turnover report — [#25](https://github.com/ddawnlll/tycho/issues/25)
  - **Goal:** Diagnostics the monograph requires next to the main run — counted in the trial log, never pass criteria.
  - **Read in the monograph:**
    - §14 `#e001` → "Boyutlandırma" (Teşhis: sabit nominal), "Karar ve dolum" (1h gecikme robustness)
    - §07 `#matematik` → "E-001 devir hızını raporlar"
    - registry `first_experiment.sizing.diagnostic`, `first_experiment.fill`
  - **Files:**
    ```text
    read: src/tycho/runner.py, portfolio.py, ledger.py
    change: runner adds the diagnostics; new tests/test_diagnostics.py
    ```
  - **Contract:**
    Constant-notional sizing (same signal, no vol scaling); fills delayed by 1h; annual turnover = Σ|traded weight| per year. Each is its own trial record flagged `diagnostic: true`.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_diagnostics.py
    ```
  - **PENDING / BLOCKED:** Same as T4.3.
  - **Out of scope:** Using diagnostics in the gate evaluator.
  - **Depends on:** T4.3 [#24](https://github.com/ddawnlll/tycho/issues/24)
- [CANCELLED owner 2026-10-25] **X4.a** E-001 DEV on real data — [#26](https://github.com/ddawnlll/tycho/issues/26) · BLOCKED  
  Needs dev_end_date, research nominal, absolute band, partial ratio, EWMA convention, latency (all PENDING) and the owner prereg lock (G1). Owner cancelled 2026-10-25.
- [ ] **V4** M4 verifier (ledger, scanner, runner) — [#27](https://github.com/ddawnlll/tycho/issues/27) · verifier
  - **Goal:** Clean-context check that the ledger is authoritative and correct, golden cases were written blind, and the trial log cannot be rewritten.
  - **Read in the monograph:**
    - §07 accounting contract; §10 scan formula; §11 golden scenarios; registry `accounting_ledger`
  - **Files:**
    ```text
    src/tycho/ledger.py, scanner.py, runner.py, tests/golden/, ledger/trials.jsonl
    ```
  - **Contract:**
    Check: funding at timestamp not bar start; no double count; band/rounding; golden git history shows golden written before ledger code; seeded fee/funding error makes tests fail.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T4.0 [#21](https://github.com/ddawnlll/tycho/issues/21), T4.1 [#22](https://github.com/ddawnlll/tycho/issues/22), T4.2 [#23](https://github.com/ddawnlll/tycho/issues/23), T4.3 [#24](https://github.com/ddawnlll/tycho/issues/24), T4.4 [#25](https://github.com/ddawnlll/tycho/issues/25)

### M5 — Null / bootstrap / DSR / gates (F2 code) — [milestone](https://github.com/ddawnlll/tycho/milestone/6)

Baselines and nulls, DSR, block bootstrap, HAC alpha, six-criteria gate, synthetic joint power. Open: 5/7.

- [ ] **T5.1** Baselines + hand-written null generators — [#28](https://github.com/ddawnlll/tycho/issues/28)
  - **Goal:** The comparison set every E-001 result is judged against, with nulls that preserve exposure so they cannot be passed trivially.
  - **Read in the monograph:**
    - §11 `#dogrulama` → "Ek testler" row "Temel çizgiler" (run-based scrambling, why not daily)
    - §11 → "Korunmuş hakem, null modeli ve birleşik güç" row "Null" (write down what is preserved)
    - §14 `#e001` → row "Temel çizgiler"; registry `first_experiment.baselines`
  - **Files:**
    ```text
    read: src/tycho/runner.py, portfolio.py, scanner.py
    new: src/tycho/nulls.py, tests/test_nulls.py
    ```
  - **Contract:**
    - Baselines: no-trade; equal-risk buy & hold with the same b; Trend Following (BTC) (universe = BTC only).
    - Run-based direction scramble ×2000: runs = maximal same-sign position periods; random sign per run.
    - Random entry ×2000 matching run-length distribution and gross exposure.
    - Docstring lists what each null preserves (holding period, net exposure, cross-sectional correlation, funding, costs).
    - Seeded numpy Generator; reps a parameter (config stats.null_reps = 2000).
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_nulls.py
    # cases: scramble keeps run lengths and |w|; random entry matches run-length histogram and gross; seed determinism
    ```
  - **PENDING / BLOCKED:** —
  - **Out of scope:** Choosing the null after seeing results; daily scrambling.
  - **Depends on:** T4.3 [#24](https://github.com/ddawnlll/tycho/issues/24)
- [x] **T5.2** DSR (hand-written), stationary block bootstrap (arch), HAC beta-adjusted alpha (statsmodels) — [#29](https://github.com/ddawnlll/tycho/issues/29)
  - **Goal:** The statistics behind pass criteria 1 and 2.
  - **Read in the monograph:**
    - §04 `#kanit` → Bailey & López de Prado 2014 (DSR)
    - §11 `#dogrulama` → "Korunmuş hakem…" rows "Çoklu deneme düzeltmesi", "Bootstrap"; "Ek testler" row "Durağan blok bootstrap"
    - §14 `#e001` → "Başarı ölçütleri" 1–2 and the 90% alpha rationale
    - registry `first_experiment.pass_all[0..1]`, `alpha_90_rationale`, `decisions[D-24]`
  - **Files:**
    ```text
    new: src/tycho/stats.py, tests/test_stats.py
    ```
  - **Contract:**
    - DSR per Bailey & López de Prado (2014): skew/kurtosis-adjusted PSR against the expected max Sharpe of N_eff trials with trial Sharpe variance V. N_eff and V are required args.
    - Stationary block bootstrap via `arch.bootstrap.StationaryBootstrap`; mean block length is a required arg (rule PENDING: ≥ mean holding period, frozen before looking).
    - Beta-adjusted alpha vs equal-risk B&H: OLS with HAC (Newey–West) via statsmodels; one-sided test at 90%.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_stats.py
    # cases: DSR matches a worked example from the paper; bootstrap seed determinism; alpha recovers a planted alpha in synthetic data
    ```
  - **PENDING / BLOCKED:** dsr_trial_effective_n, dsr_trial_sharpe_variance, bootstrap_block_rule → required args, no defaults.
  - **Out of scope:** Picking N_eff or block length; reading DEV results to set them.
  - **Depends on:** T4.3 [#24](https://github.com/ddawnlll/tycho/issues/24)
- [ ] **T5.3** Subperiod split (BTC 200d MA as-of) + per-MA component check — [#30](https://github.com/ddawnlll/tycho/issues/30)
  - **Goal:** Inputs for pass criteria 4 and 5: each MA period alone positive net of costs; positive net in both BTC-up and BTC-down subperiods.
  - **Read in the monograph:**
    - §14 `#e001` → rows "Alt dönemler", "Başarı ölçütleri" 4–5
    - registry `first_experiment.subperiods`, `pass_all[3..4]`
  - **Files:**
    ```text
    read: src/tycho/exp_trend.py, runner.py
    new: src/tycho/subperiods.py, tests/test_subperiods.py
    ```
  - **Contract:**
    Subperiod label at t uses only the BTC close/MA200 available at t (no look-ahead). Component check re-runs the pipeline with K={k} for each k, each logged as a trial. Calendar blocks are a separate diagnostic, not a gate.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_subperiods.py
    ```
  - **PENDING / BLOCKED:** —
  - **Out of scope:** Regime gating; adding gates.
  - **Depends on:** T4.3 [#24](https://github.com/ddawnlll/tycho/issues/24)
- [ ] **T5.4** Six-criteria gate evaluator (PASS / FAIL / BLOCKED / INVALID) — [#31](https://github.com/ddawnlll/tycho/issues/31)
  - **Goal:** One function that evaluates all six E-001 criteria together; any BLOCKED makes the overall verdict not PASS; holdout criterion is always OWNER.
  - **Read in the monograph:**
    - §14 `#e001` → "Başarı ölçütleri (hepsi birden)", "Üç sonuç ve sonrası", "Yasaklar"
    - §11 gate table G2–G5; registry `first_experiment.pass_all`, `outcomes`, `scientific_judge.failure_behavior`
  - **Files:**
    ```text
    read: stats.py, nulls.py, subperiods.py
    new: src/tycho/gates.py, tests/test_gates.py
    ```
  - **Contract:**
    Criteria: (1) DSR P ≥ 0.95; (2) one-sided 90% beta-adj alpha > 0; (3) top 1% of both nulls; (4) each MA positive net; (5) both subperiods positive net; (6) holdout — always `OWNER` (never evaluated by code). Overall = PASS only if 1–5 PASS and 6 signed by owner; any missing input → BLOCKED (fail closed).
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_gates.py
    ```
  - **PENDING / BLOCKED:** DSR inputs from T5.2.
  - **Out of scope:** Opening the holdout; changing thresholds.
  - **Depends on:** T5.1 [#28](https://github.com/ddawnlll/tycho/issues/28), T5.2 [#29](https://github.com/ddawnlll/tycho/issues/29), T5.3 [#30](https://github.com/ddawnlll/tycho/issues/30)
- [ ] **T5.5** Synthetic joint power + seeded false positives (small local sample) — [#32](https://github.com/ddawnlll/tycho/issues/32)
  - **Goal:** Measure how often all six criteria pass together on synthetic processes — zero edge, weak edge, costly, fat-tailed, correlated — and report false positive/negative rates.
  - **Read in the monograph:**
    - §11 `#dogrulama` → "Korunmuş hakem…" row "Sentetik güç"
    - §14 `#e001` → "Güç: bu test neyi ayırt edebilir?" (old table is NOT joint power)
    - registry `first_experiment.joint_power` (NOT_MEASURED), `power_approx.limitation`
  - **Files:**
    ```text
    read: synthetic.py, gates.py
    new: src/tycho/power.py, tests/test_power.py
    ```
  - **Contract:**
    Planted-Sharpe generators; run gate evaluator (criteria 1–5) over a small seeded sample; output pass rates with binomial CIs. Zero-edge pass rate must be low; planted strong edge must pass more often.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_power.py   # small sample, < 2 min on 2 CPUs
    ```
  - **PENDING / BLOCKED:** N_eff etc. → scenario values clearly labelled synthetic.
  - **Out of scope:** Full-size run (cancelled: MODAL_USD_CAP empty).
  - **Depends on:** T5.4 [#31](https://github.com/ddawnlll/tycho/issues/31)
- [CANCELLED owner 2026-10-25] **X5.a** Full joint-power run — [#33](https://github.com/ddawnlll/tycho/issues/33) · BLOCKED  
  Needs MODAL_USD_CAP (empty) and N_eff (PENDING). Owner cancelled 2026-10-25.
- [ ] **V5** M5 verifier (nulls, DSR, bootstrap, gates, power) — [#34](https://github.com/ddawnlll/tycho/issues/34) · verifier
  - **Goal:** Clean-context review of the statistical gate: correct DSR, nulls that preserve exposure, fail-closed evaluator.
  - **Read in the monograph:**
    - §11 "Korunmuş hakem, null modeli ve birleşik güç"; §14 "Başarı ölçütleri"; registry `first_experiment`
  - **Files:**
    ```text
    src/tycho/nulls.py, stats.py, subperiods.py, gates.py, power.py + tests
    ```
  - **Contract:**
    Check: DSR vs paper; no defaults for N_eff/V/block; scramble unit = run; evaluator never PASSes with BLOCKED input; zero-edge false-positive rate reported.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T5.1 [#28](https://github.com/ddawnlll/tycho/issues/28), T5.2 [#29](https://github.com/ddawnlll/tycho/issues/29), T5.3 [#30](https://github.com/ddawnlll/tycho/issues/30), T5.4 [#31](https://github.com/ddawnlll/tycho/issues/31), T5.5 [#32](https://github.com/ddawnlll/tycho/issues/32)

### M6 — Independent reference parity (F4) — [milestone](https://github.com/ddawnlll/tycho/milestone/7)

Blind reference ledger, scanner↔reference↔ledger parity, full vs windowed replay. Open: 3/3.

- [ ] **T6.1** Blind independent reference ledger + three-way parity — [#35](https://github.com/ddawnlll/tycho/issues/35)
  - **Goal:** A second, simple, independently written PnL calculation; scanner ↔ reference ↔ sequential ledger agree within a tolerance written before the first run.
  - **Read in the monograph:**
    - §11 `#dogrulama` → "Ek testler" row "Parity", "Oracle'lar" (Parity Oracle light)
    - §11 → "Korunmuş hakem…" row "Bağımsızlık" (seeded fee/leak/delist errors must be rejected)
    - registry `accounting_ledger.reference`, `decisions[D-07]`
  - **Files:**
    ```text
    read: tests/golden/, the §07 formulas — NOT src/tycho/ledger.py or scanner.py
    new: src/tycho/reference_ledger.py, config/parity.toml (tolerances, committed first), tests/test_parity_reference.py
    ```
  - **Contract:**
    - Worker runs in a clean context and must not read src/tycho/ledger.py or scanner.py.
    - Commit config/parity.toml (daily PnL abs diff, total diff) before the first parity run; git history must show it.
    - Parity on the synthetic tape; seeded faults (wrong fee, funding at bar start, look-ahead bar, missing delist exit) must break parity.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_parity_reference.py
    ```
  - **PENDING / BLOCKED:** Same required args as T4.1.
  - **Out of scope:** Changing tolerances after seeing a diff; editing the ledger to match the reference without a root cause.
  - **Depends on:** T4.1 [#22](https://github.com/ddawnlll/tycho/issues/22), T4.2 [#23](https://github.com/ddawnlll/tycho/issues/23)
- [ ] **T6.2** Full replay vs windowed replay + restart equality — [#36](https://github.com/ddawnlll/tycho/issues/36)
  - **Goal:** Parallel time windows (with warm-up) and a restart mid-run give the same results and evidence hashes as one full replay.
  - **Read in the monograph:**
    - §11 `#dogrulama` → "Ek testler" row "Tam replay ↔ pencereli replay" (caught a V7 funding bug)
    - §09 → "v1.3: 10 bileşene Expert-MarketState eşlemesi" C-10 row (restart → same evidence hash)
    - §08A.5 (idempotent event id, checkpoint)
  - **Files:**
    ```text
    read: runner.py, ledger.py
    new: tests/test_replay.py
    ```
  - **Contract:**
    Split the synthetic period into windows with state carried over (positions, wallet); compare per-day equity and final hashes to the full replay. Kill/restart after a checkpoint and compare.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_replay.py
    ```
  - **PENDING / BLOCKED:** Window length is a test parameter (X3.a).
  - **Out of scope:** Modal parallelism.
  - **Depends on:** T4.3 [#24](https://github.com/ddawnlll/tycho/issues/24)
- [ ] **V6** M6 verifier (reference parity, replay) — [#37](https://github.com/ddawnlll/tycho/issues/37) · verifier
  - **Goal:** Confirm the reference ledger is truly independent and parity tolerances were fixed before the first run.
  - **Read in the monograph:**
    - §11 Parity / Bağımsızlık rows
  - **Files:**
    ```text
    src/tycho/reference_ledger.py, config/parity.toml, tests/test_parity_reference.py, tests/test_replay.py
    ```
  - **Contract:**
    Check git order (tolerance commit before parity code ran); seeded faults detected; no shared helper imported from ledger.py.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T6.1 [#35](https://github.com/ddawnlll/tycho/issues/35), T6.2 [#36](https://github.com/ddawnlll/tycho/issues/36)

### M7 — Nautilus parity (F4, last) — [milestone](https://github.com/ddawnlll/tycho/milestone/8)

Pinned stable nautilus_trader executes precomputed targets; parity within pre-written tolerance. Open: 2/2.

- [ ] **T7.1** Nautilus parity: precomputed targets with imposed fills + latency model — [#38](https://github.com/ddawnlll/tycho/issues/38)
  - **Goal:** Pinned stable nautilus_trader executes the precomputed target weights on the synthetic tape (it does not decide); parity vs the ledger within the pre-written tolerance.
  - **Read in the monograph:**
    - §10 `#altyapi` → "Nautilus: rolü ve hızlandırma" and "Nautilus uyarıları" (stable pin, latency model mandatory, bypass_logging, run_analysis=False)
    - §11 → "Ek testler" row "Parity" (imposed fills; limit fills and margin out of scope)
    - §08A.8 "Bağımsız parity"; registry `decisions[D-04]`
  - **Files:**
    ```text
    change: pyproject.toml (pin a stable nautilus_trader, not an RC)
    new: src/tycho/nautilus_parity.py, tests/test_parity_nautilus.py
    ```
  - **Contract:**
    Strategy reads the target array and submits orders; fills imposed from the same cost model; explicit latency model (no same-close fill); funding data supplied; compare daily PnL to the ledger within config/parity.toml.
  - **Acceptance:**
    ```bash
    uv run pytest -q tests/test_parity_nautilus.py
    ```
  - **PENDING / BLOCKED:** executable_latency → required arg (synthetic value in test).
  - **Out of scope:** Live adapters, exchange keys, stops/KS (E-002), Modal containers.
  - **Depends on:** T6.1 [#35](https://github.com/ddawnlll/tycho/issues/35)
- [ ] **V7** M7 final verifier (stop condition) — [#39](https://github.com/ddawnlll/tycho/issues/39) · verifier
  - **Goal:** Last milestone verifier. Stop condition: every non-cancelled item [x] and this verifier PASS.
  - **Read in the monograph:**
    - §08A.8 "Bağımsız parity"; §10 Nautilus warnings
  - **Files:**
    ```text
    src/tycho/nautilus_parity.py, tests/test_parity_nautilus.py, PLAN.md
    ```
  - **Contract:**
    Check the pinned version is stable; latency model present; tolerance file unchanged since M6; PLAN.md has no [x] without a passing test.
  - **Acceptance:**
    ```bash
    uv run pytest -q
    ```
  - **Out of scope:** Writing code.
  - **Depends on:** T7.1 [#38](https://github.com/ddawnlll/tycho/issues/38)

## 7. PENDING parameters (owner; code takes them as required arguments)

| Parameter | config/tycho.toml | Monograph source | Used by |
|---|---|---|---|
| dev_end_date (lock date − 18 months) | `periods.dev_end_date` | §14 Dönem; registry `first_experiment.period` | every real-data run |
| research nominal USD | `execution.research_nominal_usd` | §14; registry `first_experiment.research_notional_policy` | T4.1, T4.2 |
| absolute band NAV floor | `execution.absolute_band_nav` | §14 Karar ve dolum; registry `first_experiment.band` | T4.1 |
| partial trading ratio | `execution.partial_trading_ratio` | §09 C-06 A; P-07 | T4.1 |
| zero-crossing / zero-target rule | add `execution.zero_crossing_rule` in T4.1 | §14 "hedef sıfır, yön değişimi ve min lot için ayrı kural" (undefined) | T4.0, T4.1 |
| executable latency | `execution.executable_latency` | §14; registry `first_experiment.fill` | T4.1, T7.1 |
| max_staleness | `execution.max_staleness` | §08A.3 `"max_staleness": "PRE_REGISTERED"` | T3.1 |
| EWMA convention (span/halflife/com) | `sizing.ewma_convention` | §16 "EWMA varyantı"; registry `first_experiment.ewma_convention` | T2.2, T3.2 |
| DSR N_eff, trial Sharpe variance | `stats.dsr_trial_*` | §11 Çoklu deneme; registry `first_experiment.dsr_trial_effective_n` | T5.2 |
| bootstrap block rule | `stats.bootstrap_block_rule` | §11 Bootstrap | T5.2 |
| G0 sufficiency thresholds | `g0.data_sufficiency_thresholds` | §16 | T1.2 |
| ABSTAIN → target weight | required arg in T3.2 | §08A.4 forbids extending the old signal; flat vs hold unstated | T3.2 |

## 8. Changes from the previous PLAN

- Every item now has a GitHub issue; done items are closed as completed with commit evidence, owner-cancelled items closed as not planned.
- New: **T4.4** diagnostics (constant-notional variant, 1h delay, turnover — §14/§07) and **T5.3** subperiod + per-MA component check (criteria 4–5). Old T5.3 gates → **T5.4**, old T5.4 power → **T5.5**.
- New verifier issues **V1** (M1 re-run result not recorded), **V2** (no M2 verifier yet), V3–V7; **V0** re-runs after T0.5.
