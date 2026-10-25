Closes #

## What changed
<!-- one line per file or behaviour -->

## Monograph sections implemented
<!-- anchors + registry paths, same as the issue -->

## Evidence
```text
$ uv run pytest -q <acceptance test>
<paste tail>
$ uv run pytest -q
<paste tail>
```

## Checklist
- [ ] No PENDING parameter guessed (required args; real runs refuse)
- [ ] No access to `data/holdout/`, `secrets/`, keys; `tests/golden/` and `prereg/locked/` untouched
- [ ] PLAN.md item set to [x] only because the acceptance test passes
- [ ] PROGRESS.md updated (decisions, BLOCKED items, worker notes)
