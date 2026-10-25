# Dispatch plan — 18 open issues (2026-10-25)

For the coordinator agent that spawns workers. Machine: **2 CPUs, ~2 GiB free RAM**. Monograph hard cap: **4 simultaneous LLM sessions** (D-27). Full suite takes ~40 s, so more than 4 parallel sessions only adds CPU contention.

## 0. Before spawning anything (coordinator, 5 min)

1. **In-flight work — do not duplicate:**
   - M5 workflow `wf_cba2f011-ece`: branch `worktree-wf_cba2f011-ece-2` has **T5.2 done** (commit 647d4ad, not merged). `-ece-1` is locked (likely T5.1 still running). Merge T5.2 and close #29 when its tests pass; spawn T5.1 only if `-ece-1` has stopped.
   - **V4 (#27)** is running. Do not respawn it.
   - `wf_58f3cda6-564-2` is locked at fe5cd86. Check whether it is T3.4 before spawning slot A.
2. **Rules that override the issue DoD** (to avoid merge conflicts between parallel workers):
   - Workers do **not** edit PLAN.md or PROGRESS.md. They put notes in the PR body or closing comment. The coordinator updates both on merge.
   - Workers run only their own acceptance test while iterating and the full suite **once** before the PR (CPU).
   - Any shell command that names `tests/golden` together with `>`, `2>&1` or `python -c` is blocked by the guard. Run `uv run pytest -q tests/golden` without redirects.
3. Each worker gets its own worktree. Verifiers are read-only and can share the main checkout.

## 1. Model tiers

| Tier | Use for | Why |
|---|---|---|
| **cheap** (DeepSeek V4.1 Flash or similar) | verifiers, T4.4, T6.2, T5.1, T5.3, T5.5 | Contract and acceptance test are fully specified in the issue |
| **mid / strong** | T3.4, T6.1, T5.4, T7.1 | Subtle point-in-time logic (T3.4), blind independence (T6.1), fail-closed evaluator (T5.4), unfamiliar Nautilus API (T7.1) |

A verifier must not use the same model **and** context as the writer of the code it checks.

## 2. Waves (at most 4 sessions at any time)

```text
WAVE 1 (now)            WAVE 2 (as slots free)                WAVE 3                 WAVE 4
A  T3.4 #43 ───────────► V3 #20 ─────► T6.2 #36 ─────────────► V6 #37 ──┐
B  T6.1 #35 ───────────► T7.1 #38 (needs config/parity.toml) ──────────┼──► V7 #39
C  T5.1 #28* ─► T5.3 #30 ─► T5.4 #31 ─► T5.5 #32 ─► V5 #34 ────────────┘
D  V0 #7 ─► V1 #11 ─► V2 #15 ─► T4.4 #25
   (V4 #27 already running; merge T5.2 #29 from its branch)
```
`*` T5.1: skip if the running M5 workflow finishes it.

**Critical path:** slot C (M5 chain, 4 sequential tasks + V5) and slot B (T6.1 → T7.1). Everything else fits beside them. Expect about 5 worker rounds instead of 18 sequential ones.

## 3. Slots

### Slot A — point-in-time fixes, then replay (writer, mid/strong → cheap)
| Order | Issue | Files (exclusive while running) | Start when | Accept |
|---|---|---|---|---|
| 1 | **T3.4** [#43](https://github.com/ddawnlll/tycho/issues/43) | src/tycho/vectorized.py, portfolio.py, exp_trend.py, factors.py + their tests | now | `uv run pytest -q tests/test_window_equality.py tests/test_portfolio.py tests/test_exp_trend.py` |
| 2 | **V3** [#20](https://github.com/ddawnlll/tycho/issues/20) (fresh cheap verifier) | read-only | T3.4 merged | `uv run pytest -q` |
| 3 | **T6.2** [#36](https://github.com/ddawnlll/tycho/issues/36) (cheap) | tests/test_replay.py (+ e001.py only if a checkpoint hook is needed) | T3.4 merged (runner outputs change) | `uv run pytest -q tests/test_replay.py` |

### Slot B — independent parity, then Nautilus (writer, mid)
| Order | Issue | Files | Start when | Accept |
|---|---|---|---|---|
| 1 | **T6.1** [#35](https://github.com/ddawnlll/tycho/issues/35) | src/tycho/reference_ledger.py, config/parity.toml, tests/test_parity_reference.py | now | `uv run pytest -q tests/test_parity_reference.py` |
| 2 | **T7.1** [#38](https://github.com/ddawnlll/tycho/issues/38) | pyproject.toml, uv.lock, src/tycho/nautilus_parity.py, tests/test_parity_nautilus.py | config/parity.toml merged (first commit of T6.1) | `uv run pytest -q tests/test_parity_nautilus.py` |

- T6.1 must be a **clean-context** session. It must not read `src/tycho/ledger.py` or `scanner.py`; it reads `docs/ledger-spec.md`, the golden cases and §07. Commit `config/parity.toml` **first**, as its own commit, before any parity run.
- T7.1: pin a **stable** `nautilus_trader` (not an RC). The install is large, so run `uv sync` while no other session is running tests. If the install or RAM fails, open a BLOCKED issue instead of downgrading the parity scope.

### Slot C — statistics chain (strict order, files do not overlap)
| Order | Issue | Files | Start when | Tier |
|---|---|---|---|---|
| 1 | **T5.1** [#28](https://github.com/ddawnlll/tycho/issues/28) | src/tycho/nulls.py, tests/test_nulls.py | now, if not in flight | cheap |
| 2 | **T5.3** [#30](https://github.com/ddawnlll/tycho/issues/30) | src/tycho/subperiods.py, tests/test_subperiods.py | now (parallel to T5.1 if a slot is free) | cheap |
| 3 | **T5.4** [#31](https://github.com/ddawnlll/tycho/issues/31) | src/tycho/gates.py, tests/test_gates.py | T5.1 + T5.2 + T5.3 merged | mid |
| 4 | **T5.5** [#32](https://github.com/ddawnlll/tycho/issues/32) | src/tycho/power.py, tests/test_power.py | T5.4 merged | cheap |
| 5 | **V5** [#34](https://github.com/ddawnlll/tycho/issues/34) | read-only | T5.5 merged | cheap, fresh |

- The running M5 workflow numbers tasks differently (its T5.3 = gates, its T5.4 = power). Map its branches to plan T5.4/T5.5 before spawning, so no task runs twice.
- T5.3 re-runs the pipeline per MA period. If `e001.py` needs a parameter for that, coordinate with slot A (T3.4 does not touch e001.py; T6.2 might).

### Slot D — cheap sweep: old verifiers, then T4.4 (one cheap session, sequential)
| Order | Issue | Notes |
|---|---|---|
| 1 | **V0** [#7](https://github.com/ddawnlll/tycho/issues/7) | cleanup.py. Also assess the locked-worktree `--force --force` behaviour flagged in the audit. |
| 2 | **V1** [#11](https://github.com/ddawnlll/tycho/issues/11) | Re-verify after T1.3 (#40) |
| 3 | **V2** [#15](https://github.com/ddawnlll/tycho/issues/15) | Re-verify after T2.4/T2.5 (#41, #42) |
| 4 | **T4.4** [#25](https://github.com/ddawnlll/tycho/issues/25) | PROGRESS says constant-notional + turnover already exist in T4.3. Add `tests/test_diagnostics.py` that proves both; open a BLOCKED issue for the 1h-delay diagnostic (daily tape only) and close #25. |

Verifier output: set the Verdict in the issue. Every blocker/major becomes a new task issue (agent-task form) in the same milestone. **Close the verifier issue only on PASS.**

### Last — V6 [#37](https://github.com/ddawnlll/tycho/issues/37) and V7 [#39](https://github.com/ddawnlll/tycho/issues/39)
- V6 after T6.1 and T6.2 are merged. V7 after **everything else** is closed; V7 PASS is the project stop condition.

## 4. Merge protocol (coordinator)

1. One PR per issue: `Closes #n`, evidence in the body.
2. Merge order on conflict: T3.4 before T6.2; T5.1/T5.2/T5.3 before T5.4; T6.1's tolerance commit before T7.1.
3. After each merge: run `uv run pytest -q` on main, then tick PLAN.md and add a PROGRESS.md line.
4. A verifier FAIL creates fix issues and puts them at the head of the same slot. Do not start the next wave item that depends on that milestone until the fix is merged.

## 5. Spawn prompt template (per worker)

```text
You are a Tycho worker. Repo: /home/admin/final (own git worktree).
Task: GitHub issue #<n> in ddawnlll/tycho. Run `gh issue view <n>` and follow it exactly.
Read CLAUDE.md first. Read only the monograph sections the issue lists (grep by phrase; never read the whole HTML).
Never guess PENDING values; never touch data/holdout, tests/golden, prereg/locked, .claude/.
Do NOT edit PLAN.md or PROGRESS.md; put notes in the PR body.
Run your acceptance command while iterating, the full suite once at the end.
Finish with a PR "Closes #<n>" containing the command output.
```
For verifiers, add: `You review only. Write no code. Try to refute the work. Record the verdict and findings in the issue; open a task issue per blocker/major.`
