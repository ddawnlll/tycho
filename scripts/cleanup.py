#!/usr/bin/env python3
"""Allowlisted disk cleanup. Dry-run by default; --apply deletes."""

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKIP = {".git", ".venv"}
CACHE_DIRS = {"__pycache__", ".pytest_cache"}
PROTECTED = (".git", "ledger", "prereg", "tests/golden", "config",
             "PROGRESS.md", "PLAN.md", "data", "results")
HOME_PROTECTED = Path.home() / ".claude" / "projects"


def git(cwd, *args, check=True):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=check)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_size(path):
    p = Path(path)
    if p.is_symlink() or not p.is_dir():
        return p.lstat().st_size
    total = 0
    for root, _, files in os.walk(p):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def is_verified(d):
    """A modal run is a verified local copy only if VERIFIED matches local.sha256."""
    try:
        verified = (d / "VERIFIED").read_text().strip().lower()
        recorded = (d / "local.sha256").read_text().strip().lower()
    except OSError:
        return False
    return bool(verified) and verified == recorded


def worktrees(repo):
    out = git(repo, "worktree", "list", "--porcelain").stdout
    entries = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            entries.append({"path": line[len("worktree "):], "branch": None})
        elif line.startswith("branch ") and entries:
            entries[-1]["branch"] = line[len("branch "):].removeprefix("refs/heads/")
    return entries


def protected(repo, path):
    # Lexical containment: a symlink inside a protected dir must not escape the check.
    p = Path(os.path.abspath(path))
    if "negative" in p.as_posix().lower():
        return True
    home = Path(os.path.abspath(HOME_PROTECTED))
    if p == home or home in p.parents:
        return True
    try:
        rel = p.relative_to(repo)
    except ValueError:
        return False
    text = rel.as_posix()
    return any(text == x or text.startswith(x + "/") for x in PROTECTED)


def scan(repo, include_worktrees):
    found = []

    def add(path, kind, branch=None):
        found.append({"path": path, "kind": kind, "branch": branch})

    for root, dirs, _ in os.walk(repo):
        for name in list(dirs):
            if name in SKIP:
                dirs.remove(name)
            elif name in CACHE_DIRS:
                add(os.path.join(root, name), "dir")
                dirs.remove(name)

    tmp = repo / "tmp"
    if tmp.is_dir():
        for p in sorted(tmp.iterdir()):
            kind = "dir" if p.is_dir() and not p.is_symlink() else "file"
            add(str(p), kind)

    for root, _, files in os.walk(repo / "logs"):
        for name in files:
            if fnmatch.fnmatch(name, "*.log.[0-9]*") or fnmatch.fnmatch(name, "*.log.*.gz"):
                add(os.path.join(root, name), "file")

    modal = repo / "runs" / "modal"
    if modal.is_dir():
        for d in sorted(modal.iterdir()):
            if d.is_dir() and not d.is_symlink() and is_verified(d):
                add(str(d), "dir")

    if include_worktrees:
        # Filter by path, not list position: the main worktree is the one at --repo.
        for wt in worktrees(repo):
            p = str(Path(wt["path"]).resolve())
            if p != str(repo) and os.path.isdir(p):
                add(p, "worktree", wt["branch"])

    seen, out = set(), []
    for c in found:
        key = os.path.realpath(c["path"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    # Children first: a nested candidate (e.g. __pycache__ inside a removed dir) goes before its parent.
    out.sort(key=lambda c: len(Path(c["path"]).parts), reverse=True)
    return out


def shown(repo, path):
    try:
        return Path(path).relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def append_progress(repo, lines):
    with open(repo / "PROGRESS.md", "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines) + "\n")


def remove(path):
    p = Path(path)
    if p.is_symlink() or not p.is_dir():
        p.unlink(missing_ok=True)
    else:
        shutil.rmtree(p)


def archive_worktree(repo, entry):
    """Save dirty and unmerged work before --force removal so nothing is lost."""
    wt = Path(entry["path"])
    branch = entry["branch"]
    status = git(wt, "status", "--porcelain").stdout.strip()
    unmerged = ""
    if branch:
        rev = git(repo, "rev-list", f"main..{branch}", check=False)
        if rev.returncode == 0:
            unmerged = rev.stdout.strip()
    if not status and not unmerged:
        return None
    parts = [git(wt, "diff", "HEAD", check=False).stdout]
    if branch:
        parts.append(git(wt, "format-patch", "--stdout", f"main..{branch}", check=False).stdout)
    patch = repo / "runs" / "archive" / (wt.name + ".patch")
    patch.parent.mkdir(parents=True, exist_ok=True)
    patch.write_text("\n".join(parts), encoding="utf-8")
    return patch


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--include-worktrees", action="store_true")
    args = ap.parse_args(argv)
    repo = Path(os.path.abspath(args.repo))

    found = scan(repo, args.include_worktrees)
    bad = [c for c in found if protected(repo, c["path"])]
    if bad:
        print("ABORT: candidate inside a protected path:", file=sys.stderr)
        for c in bad:
            print("  " + shown(repo, c["path"]), file=sys.stderr)
        return 1
    rows = [{"path": shown(repo, c["path"]), "kind": c["kind"], "size": path_size(c["path"])}
            for c in found]
    commands = [c for c, ok in (("uv cache prune", shutil.which("uv")),
                                ("pip cache purge", shutil.which("pip"))) if ok]
    state = repo / "runs" / "cleanup" / "last_dryrun.json"

    if not args.apply:
        now = utc_now()
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"time": now, "candidates": rows}, indent=2) + "\n",
                         encoding="utf-8")
        lines = [f"### Cleanup dry-run {now}"]
        lines += [f"- `{r['path']}` ({r['kind']}, {r['size']} bytes)" for r in rows]
        lines += [f"- command: `{c}`" for c in commands]
        append_progress(repo, lines)
        print("\n".join(lines))
        return 0

    if not state.exists():
        print(f"refusing: no dry-run record at {state}", file=sys.stderr)
        return 1
    recorded = {c["path"] for c in json.loads(state.read_text(encoding="utf-8"))["candidates"]}
    current = {r["path"] for r in rows}
    if recorded != current:
        print("refusing: candidate set differs from the recorded dry run", file=sys.stderr)
        for p in sorted(recorded - current):
            print(f"  only in dry run: {p}", file=sys.stderr)
        for p in sorted(current - recorded):
            print(f"  only now: {p}", file=sys.stderr)
        return 1

    lines = ["### Cleanup applied", f"- time: {utc_now()}"]
    for c in found:
        if c["kind"] == "worktree":
            patch = archive_worktree(repo, c)
            git(repo, "worktree", "remove", "--force", c["path"])
            note = f", archived `{shown(repo, patch)}`" if patch else ""
            lines.append(f"- worktree `{shown(repo, c['path'])}` removed{note}")
        else:
            remove(c["path"])
            lines.append(f"- `{shown(repo, c['path'])}` ({c['kind']})")
    for cmd in commands:
        done = subprocess.run(cmd.split(), capture_output=True, text=True)
        lines.append(f"- command: `{cmd}` (exit {done.returncode})")
    append_progress(repo, lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
