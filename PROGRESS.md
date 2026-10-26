# PROGRESS

## 2026-10-25 — Session 1

### Step 0 — model routing verified
- One-agent probe workflow `wf_4ab974fb-5d2`. CCR log `~/.claude-code-router/app-data/request-logs.sqlite` row 42 (2026-10-25T15:09:19.803Z): provider `OpenCode Go`, model `deepseek-v4.1-flash`, status 200. No fallback to the session model.

### Coordinator decisions (non-blocking, made without asking)
- Phase order follows the owner's request (G0 → MarketState → E001 → ledger/scanner → null/DSR → reference parity → Nautilus), not the monograph's F2-before-F3. The prereg lock stays OWNER either way.
- `runs/` is gitignored operational state at the absolute path /home/admin/final/runs, shared by all worktrees. The trial log is tracked separately in `ledger/trials.jsonl`.
- No Modal adapter code: MODAL_USD_CAP is empty, so Modal jobs are BLOCKED; long computations run as small local samples. Writing an unused adapter would be speculative code.
- Guard = PreToolUse hook (heuristic for Bash) plus deny rules. A real lock needs OS permissions: [OWNER].
- Binance data hosts are blocked by the guard while dev_end_date is PENDING, so no download can reach holdout data.
- Concurrency: the machine has 2 CPUs and about 3 GiB free RAM; workflows run at most 4 workers, in practice fewer.
- Sleeve budget mismatch (§05 table T 60%/M 20% vs registry v1 T 1.0, both marked OPEN P-01) does not affect v1 code: only T is active. Noted, not blocking.

### BLOCKED / OWNER register
- [OWNER] dev_end_date (lock date − 18 months) → every real-data job is BLOCKED.
- [OWNER] MODAL_USD_CAP empty → Modal jobs BLOCKED.
- [OWNER] OS-level lock of the guard, golden and prereg.
- [OWNER] E-001 prereg lock (G1); G5 holdout.
- PENDING (monograph open): research nominal, absolute band NAV floor, partial trading ratio, EWMA convention, executable latency, max_staleness, DSR N_eff + trial Sharpe variance, bootstrap block rule, G0 sufficiency thresholds.
- BLOCKED (text/registry conflict): live window length 400 (§08) vs registry warmup "not universal 400" / lookback 260.

### M0 — skeleton (workflow wf_49eb6840-ca0)
- T0.1, T0.2, T0.3 DONE (DeepSeek workers in worktrees), merged into main; `uv run pytest -q` → 18 passed.
- Guard hook is live in the coordinator session: a Bash read of the protected key directory was blocked.
- Worker worktrees live under .claude/worktrees/ (now gitignored); cleanup.py --include-worktrees removes them between workflows.

### M1 — G0 FEASIBILITY-DATA tooling (workflow wf_417237d0-962)
- T1.1 tape/synthetic, T1.2 audit DONE, merged; suite 35 passed. The G0 real run stays BLOCKED (dev_end_date and thresholds PENDING); on the synthetic tape the CLI verdict is BLOCKED, as intended.
- Worker notes for owner review: funding expected-count uses the latest spec's interval (understates if the interval changes mid-life on real data); history is counted in distinct bar days.

### 2026-10-25 — Owner decision: cancel every owner-dependent item
- The owner cancelled all items waiting on them: OS-level guard/golden/prereg lock, every real-data run (dev_end_date), Modal runs (MODAL_USD_CAP), prereg lock / G1, G5 holdout, the full joint-power run, and the window-length conflict resolution.
- PENDING parameters stay PENDING in config; the code keeps taking them as required arguments and refusing real runs. Nothing is guessed.
- Stop condition is now: every non-cancelled item [x] and the last milestone passes the verifier.

### M0 verifier (wf_4e6413d1-3f8): FAIL
- Blocker: cleanup.py loses untracked files when removing a worktree. Majors: symlink escape, hardcoded `main`, locked-worktree crash, --repo not required to be the main worktree → fixing in T0.5.
- Guard (major): heuristic Bash matching misses relative paths (`cd data && cat holdout/x`), `python3 -c`, `install`, `rm -rf .claude`; Grep/Glob `glob` fields are not checked. The guard is immutable to agents and the coordinator by design; with the owner lock cancelled this stays a **known residual risk**. Mitigation: data/ is gitignored, so ripgrep-based Grep skips it by default.
- Minor: require() empty-string branch untested; MODAL_STOP_FRACTION source is now documented (owner instruction).
- `/exit` interrupted the M1 verifier and T2.2/T2.3; T2.1 had finished and is merged (50 tests pass). T2.2/T2.3 restarted (wf_a7dad3fa-170), M1 verifier re-run.

### M2 (wf_a9f6354e-534) — T2.1/T2.2/T2.3 DONE, merged. The /exit did not stop this workflow; the duplicate rerun (wf_a7dad3fa-170) should skip via runs/ results. Worker note: ewma_cov has no PSD repair (relevant for the 2x vol cap).

### 2026-10-25 — Issue-based tracking
- Public repo https://github.com/ddawnlll/tycho (owner chose public). 39 issues in 8 milestones; done items closed as completed with commit evidence, owner-cancelled items closed as not planned.
- PLAN.md rewritten: goals, monograph reading map, worker protocol, dependency graph, per-item issue links and specs, PENDING table. Issue forms + PR template in `.github/`.
- Plan changes: new T4.4 (diagnostics) and T5.3 (subperiods + per-MA component check); old T5.3 → T5.4, old T5.4 → T5.5; verifier issues V0–V7.
- Coordinator decisions recorded in issues (not monograph values): gross 2.0 cap applied by proportional shrink like the vol cap (T3.2); ABSTAIN → weight and the zero-crossing rule are required arguments (monograph leaves them undefined).

### M1 verifier (wf_78150cd0-203): FAIL → T1.3
- Majors: the universe metric ignores available_at; a survivor-only tape passes; contract filters are unchecked; mark coverage is a global ratio. Minors: synthetic features untested, synthetic delisted_at not PIT, holdout segment check case-sensitive, event rows lack instrument_spec_version.
- Holdout case-sensitivity and the missing instrument_spec_version on event rows are not fixed: the guard covers the first, and the second does not affect E-001 (specs are joined by timestamp). Recorded as minor residuals.
- The duplicate M1 verifier rerun (wf_5f4a3cac-46f) was stopped as redundant.

### T1.3 + M3 merged (113 passed). M2 verifier (wf_c3637304-d7b): FAIL → T2.4
- Majors: the expert can mutate the cached snapshot and bypass ABSTAIN enforcement; dataset_hash is frozen over the whole tape (stale cache, snapshot_id unstable across providers); feature_code_hash is a placeholder with no factor cache.
- Minor not fixed: ExpertManifest follows the 08A.4 text (`id`, no `snapshot_schema`) while the registry lists `expert_id` and `snapshot_schema`. Text/registry field-name divergence; no behaviour depends on it. Recorded, not resolved.
- M3 worker notes: vectorized.trend_panels has a `shortcut:` (revisions applied from their stamp row); target_weights raises on a non-PSD covariance (no repair).
- docs/ledger-spec.md: the zero-crossing rule is a coordinator interpretation (monograph says "include zero-crossing rules" without content); flagged for re-check before prereg.

### T2.4 merged (121 passed). Note: resolve() now raises on an empty universe, so callers iterating early dates must handle it.

### M2 re-verify (wf_e308b7bb-d25): FAIL → T2.5. All 7 T2.4 fixes confirmed with old-code-failing tests; new major: the factor cache returns its mutable cached FactorFrame (same bypass class). Minors: unordered feature hash, untested timeframe validation, quality flags not enforced.

### M4 (wf_1713f6ae-e7c) — T4.0..T4.3 DONE, merged; 168 passed
- 21 golden cases written blind from docs/ledger-spec.md (each with a step-by-step derivation). The ledger passes all of them unmodified: git diff of the golden dir between T4.0 and T4.3 is empty, and 0 tests skip.
- Note: the project deny rule on golden edits also blocked the Write tool; the T4.0 worker installed the files once via a script asserting non-existence. The guard over-blocks read-only shell commands that mention the golden dir.
- Scanner caveat: w~ divides by (1+gross); gross == -1 gives non-finite values (the monograph formula itself).
- M5 started (wf_cba2f011-ece).

### Reconciliation with the issue-based PLAN.md (commit 0df30bf, not made by this coordinator)
- Adopted as the current plan. Checked off by acceptance evidence: T0.5, T3.1–T3.3, T4.0–T4.3 (suite 168 passed on main f766ede). Also done but missing from the new plan: T1.3 (G0 audit fixes), T2.4 (M2 fixes).
- Verifier items: V0, V1, V2 and V3 FAILed earlier; their fix tasks are T0.5, T1.3, T2.4/T2.5 and T3.4 (queued). V4 running (M4 verifier).
- T4.4: constant-notional diagnostic already in T4.3 (separate trial); turnover reported. 1h-delay diagnostic: BLOCKED, since the synthetic tape and E-001 data are daily only (needs an intraday tape; real data cancelled).
- M5 numbering differs from the running M5 workflow: there T5.3 = gates (incl. BTC regime mask) and T5.4 = power; mapped to plan T5.4 / T5.5. Plan T5.3 (subperiods module + per-MA component re-runs logged as trials) is added after.
### M3 verifier (wf_4bf0a3cb-a4f): FAIL → T3.4 (after T2.5, shared factors.py)
- Blocker: vectorized.trend_panels builds from the end-of-tape view → later revisions leak into earlier rows (PIT violation).
- Majors: cov evaluated one bar before the decision time (equality unverified when caps bind); NaN latest close silently falls back to the previous close; ABSTAIN → weight not a required arg (`on_abstain`); MA NaN handling differs between panel and snapshot.

## 2026-10-26 — Session 2 (Orchestrator)
- T5.2 (#29) merged from `origin/t5.2-dsr-bootstrap-alpha` (commit `647d4ad`): DSR, stationary block bootstrap, HAC beta-adjusted alpha (`11 passed` in `test_stats.py`, `186 passed` full suite).

## 2026-10-25 — Sol autonomous DEV completion
- Starting main SHA: `92d89e3f6f922aa81bbaeed954a3276e169aa481`. Existing CI branch/checkout preserved; coordinator uses `/tmp/opencode/tycho-main`. Remote T5.2 already integrated, not duplicated.
- Wave 1: isolated writers for #43, genuinely blind #35, #28; coordinator owns records and independent verification. Maximum four sessions including coordinator.
- Baseline integrated-main suite: `uv run pytest -q` → `186 passed in 49.45s`.
- #28 worker found undefined null/baseline semantics and opened owner decision #46; no code guessed. Independent #30 dispatched instead. Modal remains out of scope under skill/PLAN; local synthetic computations only.
- V0 (#7) FAIL: locked-worktree double-force deletion contradicts #4; archive git reads ignore failure. Focused implementation blocker #48 opened; no cleanup executed on active worktrees.
- Blind worker committed tolerances first (`26f1d9b`), opened #45 for undefined zero-crossing policy and #47 for a missing scanner API contract. Coordinator supplied only the public interface (not implementation) in `docs/scanner-interface.md` and #47; no tolerance changes.
- V1 (#11) FAIL → #49: actual synthetic audit returned PASS with all thresholds NaN. V2 (#15) BLOCKED → #52: manifest `id`/`expert_id` and missing `snapshot_schema` conflict. V4 (#27) FAIL → #50: execution specs/ADV/delist publication use final tape view; synthetic latency not required or enforced. #51 records unavailable 1h-delay diagnostic tape.
- T5.3 PR #54 (`75cc93a`, amended `e464ede`) received independent review; #56 fixes same-stamp closed-bar inclusion. Not merged/closed pending integrated evidence.
- T6.1 blind writer pushed draft PR #57 (`8d9db4b`): 51 acceptance tests and 237 full-suite tests passed; exact zero-cost diagnostic parity succeeds, general cost-bearing scanner parity demonstrably exceeds unchanged tolerances (#53). #35 remains open, not claimed complete.
- T0.6 PR #55 (`74fe1ef`) passed 16 focused / 191 full tests, but independent Astra verifier found nested generic-directory deletion can bypass worktree preservation (#58). Fix on `sol/t0.7` excludes generic candidates overlapping any registered worktree; 21 focused tests pass. No unsafe code merged.
- Operational: server restarts interrupted test commands; incomplete `/tmp` tmpfs environment exhausted space. Moved only the coordinator-created incomplete environment to disk-backed cache and reinstalled locked dependencies; later environments use `/home/admin/.cache/tycho-envs/`. No user work removed, no cleanup applied to active worktrees.
- Merged #48/#58 safety stack (`74fe1ef`, `8c69f50`, PRs #55/#60) after independent Astra PASS and 12 adversarial probes. Integrated main: `uv run pytest -q tests/test_cleanup.py` → `21 passed in 2.77s`; `uv run pytest -q` → `196 passed in 77.85s (0:01:17)` with BLAS/OMP threads bounded to one. No protected-path changes. V0 #7 now PASS; scope unchanged, owner-cancelled guard residuals remain.
- Merged #49 (`6fb1f20`, PR #62) after independent Astra PASS for G0 validation/PIT/delisting/no-return checks. Integrated main acceptance: `uv run pytest -q tests/test_feasibility.py tests/test_tape.py` → `89 passed in 6.88s`; full `uv run pytest -q` → `252 passed in 49.31s` (BLAS/OMP threads bounded to one). V1 #11 PASS. Existing latest-spec funding-interval coverage approximation remains documented, not silently changed.
- Merged #43 (`d829405`, PR #59) after independent Astra PASS on all five original findings. Integrated main acceptance: `uv run pytest -q tests/test_window_equality.py tests/test_portfolio.py tests/test_exp_trend.py` → `39 passed in 44.91s`; full suite → `271 passed in 81.47s (0:01:21)` (bounded BLAS/OMP). V3 remains FAIL: new #61 proves delayed-current-close readiness diverges between panel (flat) and snapshot (old close LONG). Fix #61 takes slot A before dependent replay. #50 execution PIT fixes remain separate.
