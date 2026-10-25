# Workflow, commands and pitfalls

## Commands
- Setup: `uv sync`. Tests: `uv run pytest -q` (~40 s, 175+ tests). Run only the issue's acceptance command while iterating; the full suite once before a PR.
- Issues: `gh issue view <n> --repo ddawnlll/tycho`; close with `gh issue close <n> --reason completed --comment "<evidence>"`.
- Machine: 2 CPUs, ~2 GiB free RAM. At most **4 agent sessions** in parallel (monograph cap, D-27). More only slows the test runs.

## Guard pitfalls (`.claude/hooks/guard.py`, heuristic)
- A shell command that contains `tests/golden` **and** any of `>`, `2>&1`, `python -c`, `cp`, `mv`, `rm`, `tee` is blocked. Run `uv run pytest -q tests/golden` with no redirect. Put scripts that mention the golden path in a file and run the file.
- Paths are matched as text: mentioning `data/holdout/` or `secrets/` anywhere in a command is blocked.

## Parallel work
- One git worktree per writing agent; verifiers are read-only and may share the main checkout.
- Workers never edit `PLAN.md` or `PROGRESS.md` (every parallel PR would conflict). The orchestrator updates both after each merge.
- Exclusive file sets per slot are listed in `docs/dispatch-plan.md` §3. Two agents never write the same file at the same time.
- Do **not** run `scripts/cleanup.py --apply --include-worktrees` while agents are running: it force-removes locked worktrees.
- Other orchestrators may have left worktrees under `.claude/worktrees/` and branches named `worktree-wf_*`. Do not delete or rewrite them; inspect and merge finished ones (`git log main..<branch>`) if their tests pass.

## Merge protocol
1. One branch / PR per issue: `Closes #n`, acceptance output + full-suite output in the body.
2. Merge order on conflict: T3.4 before T6.2; T5.1/T5.2/T5.3 before T5.4; T6.1's `config/parity.toml` commit before T7.1.
3. After each merge: `uv run pytest -q` on main must pass; tick PLAN.md; add one PROGRESS.md line; push.
4. Verifier issues (V*) run in a fresh context that wrote none of the code. FAIL → open a task issue (agent-task form) per blocker/major in the same milestone; close the verifier only on PASS.

## Independence rules (science integrity)
- T6.1 reference ledger: written without reading `src/tycho/ledger.py` or `scanner.py`; tolerance file committed before the first parity run.
- Never change a test threshold, tolerance or golden value to make a run pass. That is a FAIL to report, not a fix.
