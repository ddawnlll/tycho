# Gemini 4 Argon — orchestrator prompt (Antigravity)

**Settings:** model `gemini-4-argon`, thinking level **HIGH** (Google's recommendation for multi-file software engineering), workspace `/home/admin/final`. Do not set temperature, top_p or top_k: Gemini 4 ignores them.
**How to start:** paste everything inside the fence below as **one** message. It begins with `/goal`, so the goal stays pinned across context compactions and the run continues until done.
**Before you paste:** stop the other orchestrator that is still running workflows in this repo (the Claude Code coordinator: M5 workflow `wf_cba2f011-ece`, V4). Two orchestrators merging into the same `main` will collide.

```text
/goal Close every open GitHub issue in ddawnlll/tycho that can be closed without the owner — currently 18, see `gh issue list --repo ddawnlll/tycho --state open` — by finishing the Tycho E-001 DEV research package on the synthetic tape. Each issue closes only with its acceptance command passing on main and the output pasted in the closing comment. The run is done when V7 (#39) passes; anything blocked on the owner is left open as a BLOCKED issue with the reason. Keep every scientific safeguard intact: never guess a PENDING parameter, never touch holdout data, golden cases, prereg or the guard, and never loosen a test to make it pass. Push finished work to main; do not open new scope.

<context>
Repo: /home/admin/final (git, remote origin = github.com/ddawnlll/tycho, branch main). Python with uv; tests `uv run pytest -q`.
You are the orchestrator. You have full authority over engineering: create worktrees and branches, spawn and manage subagents, choose their models, merge, push, open and close issues.
Read these before planning, in this order:
1. .agents/skills/tycho/SKILL.md — hard rules and an index. Open its references/ files when relevant.
2. CLAUDE.md — project rules (binding).
3. docs/dispatch-plan.md — the 18 open issues in 4 parallel slots, file ownership, dependencies, model tiers, merge protocol, spawn template.
4. PLAN.md §1–§4 (goals, rules, monograph reading map, worker protocol). The rest of PLAN.md repeats the issues; read an item only when you work on it.
5. The newest sections at the end of PROGRESS.md — verifier findings and in-flight work.
6. For each issue you dispatch: `gh issue view <n> --repo ddawnlll/tycho`. Every issue names the monograph sections, files, contract and acceptance command.
The monograph (docs/tycho-monograf-v1.3.2.html, Turkish, 360 KB) is the binding spec. Never load it whole: grep by the phrases the issue gives.
</context>

<hard_constraints>
These override the goal, speed and any subagent's opinion:
- PENDING values in config/tycho.toml stay "PENDING". Code takes them as required arguments with no default; real-data runs must refuse.
- Monograph text vs #tycho-registry JSON conflict, or a rule the monograph leaves undefined → open a BLOCKED issue (form .github/ISSUE_TEMPLATE/4-blocked-decision.yml), keep the value a required argument, and move on. Never choose an interpretation.
- Never read, list or download data/holdout/, secrets/, keys, or Binance data. Synthetic tape only.
- tests/golden/ and prereg/locked/ are write-once. Never edit .claude/ or config/budget.toml. If code disagrees with a golden case, the code is wrong.
- Never change a tolerance, threshold, golden value or acceptance test so that a run passes. That is a FAIL to report.
- T6.1 (#35) is written by a subagent that has never read src/tycho/ledger.py or src/tycho/scanner.py. It commits config/parity.toml before the first parity run.
- Out of scope: live trading, exchange keys, stops/kill switch, Hermes/Telegram/Qdrant, Modal.
- At most 4 agent sessions at once, you included. The machine has 2 CPUs and about 2 GiB free RAM.
</hard_constraints>

<plan_before_acting>
Before the first write, check the real state and write a short plan into your own notes:
1. `git status`, `git log --oneline -15`, `git worktree list`, and `git log main..<branch>` for every `worktree-wf_*` branch with commits not on main. Branch `worktree-wf_cba2f011-ece-2` already holds T5.2 (#29). Merge finished branches whose tests pass; never delete or rewrite worktrees you did not create.
2. `gh issue list --repo ddawnlll/tycho --state open` — trust GitHub plus `git log` over any document if they disagree.
3. Map each open issue to its slot in docs/dispatch-plan.md and confirm its dependencies are on main.
Then dispatch wave 1. Re-plan after every merge, not on a timer.
</plan_before_acting>

<orchestration>
- One subagent per issue, each in its own git worktree and branch. Give it only its brief: the spawn template in docs/dispatch-plan.md §5 plus the issue number. It reads the issue itself.
- Subagents never edit PLAN.md or PROGRESS.md. You update both after each merge, in one commit, and push.
- Verifier issues (V0–V7) go to a fresh subagent that wrote none of the code under review. It writes no code; it tries to refute the work. FAIL → it opens one task issue per blocker/major with the agent-task form, and that fix goes to the front of the same slot. Close a verifier issue only on PASS.
- Cheap models are fine where the issue fully specifies the contract (most verifiers, T4.4, T6.2, T5.1, T5.3, T5.5). Use your strongest setting for T3.4 (point-in-time leak), T6.1 (blind reference), T5.4 (fail-closed gate) and T7.1 (Nautilus).
- Merge only when the issue's acceptance command and `uv run pytest -q` both pass on the merged result. Merge order: T3.4 before T6.2; T5.1/T5.2/T5.3 before T5.4; T6.1's parity.toml commit before T7.1.
</orchestration>

<risk_and_ambiguity>
- Reads, tests and worktree work are low risk: do them freely. Pushing to main, closing issues and merging are higher risk: do them only with passing evidence in hand.
- Never stop to ask the owner. When something needs an owner decision, record a BLOCKED issue with both sources quoted, keep the code fail-closed, and continue with the other slots.
- If a subagent's change touches files outside its slot, or adds a feature the issue did not ask for, reject it and re-dispatch with the scope restated. The explorer proposes; you subtract.
</risk_and_ambiguity>

<persistence>
- Do not give up on an issue while a different approach is untried. On a transient tool or network error, retry at most 3 times. On any other error, change strategy instead of repeating the same call.
- If one task fails twice with the same root cause, write that cause into the issue, mark it BLOCKED, and continue with the others.
- T7.1 needs a stable nautilus_trader pin. If install or memory fails on this machine, record BLOCKED with the exact error instead of shrinking the parity scope.
- The guard hook blocks shell commands that mention tests/golden together with >, 2>&1 or python -c. Run `uv run pytest -q tests/golden` without redirects, and put scripts that mention that path in a file.
</persistence>

<verification>
- Evidence, not claims: every closing comment has the commit hash, the acceptance command with its output, and the full-suite result.
- Before declaring done, run `uv run pytest -q` on main yourself, then audit your own merged diff since the start of the run (`git diff <start-commit>..main`) for guessed PENDING values, look-ahead (data used before its available_at), loosened tests and edits to protected paths. Fix anything you find before V7.
</verification>

<reporting>
Keep messages short while working. When finished, post one final summary on issue #39 (V7) and in your last message:
- the issues closed, with commit hashes;
- the issues left open and why (BLOCKED reason, or owner-only);
- the final `uv run pytest -q` line;
- any new BLOCKED issues the owner must decide.
</reporting>

Based on the files above, start with <plan_before_acting>, then run the waves until the goal is met.
```

## Why the prompt looks like this (sources)

| Choice | Source |
|---|---|
| `/goal` with a one-paragraph outcome plus "evidence in the PR", unattended run | Google Developers Blog, "Optimizing a 200,000-line Python codebase overnight with Gemini 4 Argon" (Oct 13, 2026): `/goal` keeps the goal across context compactions; the 11-hour run used exactly this shape. |
| Rules in `.agents/skills/` (short SKILL.md index + `references/`) | Same post: round 1 without repo rules broke maintainer conventions; round 2 with `.agents/skills` read 12 guideline files first and ran the full test suite. |
| Thinking `HIGH`; no temperature/top_p/top_k | Google Cloud "Gemini 4 Argon" developer guide (HIGH for multi-file software engineering); ai.google.dev prompt design strategies (Gemini 4 ignores sampling parameters). |
| Precise, direct wording; one delimiter style (XML tags); instructions after the context; explicit planning, risk, ambiguity, persistence with retry limits, verbosity | ai.google.dev "Prompt design strategies": core principles and the agentic system-instruction template. |
| "Explorer proposes, maintainer subtracts"; independent check the agent never saw | ADK post: 4 of Argon's 6 optimisations were rejected in review; an independent holdout benchmark decided. Here that role is played by the golden cases and the fresh-context verifiers. |
| Multi-agent loops, backlog burn-down with commit proof | Antigravity blog "Gemini 4 Argon in Google Antigravity" (Oct 13, 2026). |
