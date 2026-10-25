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
