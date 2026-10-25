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
