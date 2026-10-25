---
name: tycho
description: Rules for any agent working in the Tycho repo (E-001 crypto trend research, DEV only). Read before touching code, issues or worktrees.
---

# Tycho — agent rulebook (index)

Read this file fully. Open a `references/` file only when the task needs it.

## Hard rules (never broken, whatever the goal says)
1. Binding spec = `docs/tycho-monograf-v1.3.2.html` and its `#tycho-registry` JSON. Text vs registry conflict → open a BLOCKED issue; never pick an interpretation.
2. Never guess a PENDING parameter (config/tycho.toml values `"PENDING"`). Code takes it as a required argument with no default; real-data runs refuse.
3. Never read, list or download `data/holdout/`, `secrets/`, keys, or any Binance data. Synthetic tape only.
4. `tests/golden/` and `prereg/locked/` are write-once. If code disagrees with a golden case, fix the code.
5. Never edit `.claude/` (guard hook, settings, rules) or `config/budget.toml`. Owner-only.
6. Out of scope: live orders, exchange keys, stops/kill switch, Hermes/Telegram/Qdrant, Modal runs.
7. A PLAN.md item becomes `[x]` only when its acceptance test passes; close the issue with the command output.

## Where things are
| Need | File |
|---|---|
| Goal, rules, monograph reading map | `PLAN.md` §1–§4, `CLAUDE.md` |
| Which issue runs when, in which slot, with which files | `docs/dispatch-plan.md` |
| Ledger interface (golden cases are written from it) | `docs/ledger-spec.md` |
| History, decisions, verifier findings | `PROGRESS.md` (newest sections at the end) |
| Each task's contract and acceptance command | `gh issue view <n> --repo ddawnlll/tycho` |
| Commands, guard pitfalls, merge protocol | `references/workflow.md` |
| Reading the 360 KB monograph cheaply | `references/monograph.md` |
