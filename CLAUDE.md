# Tycho — research package (DEV only)

Binding spec: `docs/tycho-monograf-v1.3.2.html`; canonical registry = its `#tycho-registry` JSON. If text and registry conflict, that item is BLOCKED — never pick an interpretation.
Owner eki docs/owner-decisions-v1.3.3.md, monograf ve registry ile çeliştiği ya da onların sessiz kaldığı yerde önceliklidir.

- Layout: `src/tycho/` package, `tests/`, `config/` (PENDING values stay `"PENDING"`), `runs/` (operational state, gitignored), `ledger/trials.jsonl` (append-only trial log).
- Env: `uv sync`, tests: `uv run pytest -q`.
- No own engine: pandas/numpy; `arch` for block bootstrap, `statsmodels` for HAC/alpha. Hand-written only: DSR and null generators.
- Never guess PENDING monograph parameters (research nominal, absolute band, partial trading ratio, EWMA convention, N_eff, latency). Code takes them as required arguments; real runs refuse while config says PENDING.
- Never download or read holdout data. Real-data loaders refuse while `config/tycho.toml: dev_end_date` is PENDING; use the synthetic tape.
- Protected (guard hook): `data/holdout/`, `secrets/`, keys, `prereg/locked/` and `tests/golden/` (write-once), `.claude/` guard files. Golden cases are written blind to the implementation; if the ledger disagrees, fix the code, never the golden.
- Out of scope: live orders, exchange keys, Hermes/Telegram/Qdrant.
- A PLAN.md item becomes `[x]` only when its acceptance test passes.
