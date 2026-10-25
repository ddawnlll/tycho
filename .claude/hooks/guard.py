#!/usr/bin/env python3
"""PreToolUse guard: protected paths, write-once areas, immutable guard files, blocked hosts."""

import fnmatch
import json
import os
import re
import sys
from pathlib import Path

RULES = Path(__file__).resolve().parent.parent / "guard-rules.json"
FILE_TOOLS = {"Read", "Grep", "Glob", "Edit", "Write", "MultiEdit", "NotebookEdit"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
MUTATING = ["rm", "mv", "cp", "sed -i", "perl -i", ">", ">>", "tee", "truncate",
            "chmod", "chown", "ln", "dd", "unlink", "rsync", "git rm", "git mv",
            "git checkout", "git restore", "python -c"]
TOKEN = re.compile(r"[^\w.\-/]+")


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def resolve(raw, cwd):
    p = Path(raw)
    if not p.is_absolute():
        p = Path(cwd) / p
    return p.resolve().as_posix()


def path_matches(pat, path):
    # dir pattern: matches at any depth, so it holds inside worktrees too
    if pat.endswith("/"):
        return "/" + pat in "/" + path + "/"
    # glob/literal without '/': basename only
    if "/" not in pat:
        return fnmatch.fnmatchcase(os.path.basename(path), pat)
    # path pattern: suffix match on path components
    return path == pat or path.endswith("/" + pat)


def cmd_matches(pat, cmd):
    if pat.endswith("/"):
        bare = pat.rstrip("/")
        return pat in cmd or bare in cmd
    if "*" in pat or "?" in pat:
        return any(fnmatch.fnmatchcase(t, pat) or fnmatch.fnmatchcase(os.path.basename(t), pat)
                   for t in TOKEN.split(cmd) if t)
    # path-style patterns are literal substrings of a shell command
    return pat in cmd


def main():
    try:
        event = json.load(sys.stdin)
        tool = event["tool_name"]
        tool_input = event.get("tool_input") or {}
        cwd = event.get("cwd") or os.getcwd()
        rules = json.loads(RULES.read_text())
        deny, once = rules["deny"], rules["write_once"]
        immutable, hosts = rules["immutable"], rules["blocked_hosts"]
    except Exception:
        block("guard: unparsable hook input or missing/invalid rules")

    raw = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("notebook_path")
    path = resolve(raw, cwd) if raw and tool in FILE_TOOLS else None
    cmd = tool_input.get("command") if tool == "Bash" else None
    url = tool_input.get("url") if tool == "WebFetch" else None
    text = " ".join(x for x in (cmd, url) if x).lower()
    mutating = cmd is not None and any(t in cmd for t in MUTATING)

    if tool in ("Bash", "WebFetch"):
        for host in hosts:
            if host.lower() in text:
                block("real market data BLOCKED until owner sets dev_end_date")

    for pat in deny:
        if (path and path_matches(pat, path)) or (cmd is not None and cmd_matches(pat, cmd)):
            block(f"deny rule: {pat} is protected")

    for pat in immutable:
        if tool in WRITE_TOOLS and path and path_matches(pat, path):
            block(f"immutable: {pat} may not be modified")
        if mutating and cmd_matches(pat, cmd):
            block(f"immutable: {pat} may not be modified")

    for pat in once:
        if path and path_matches(pat, path):
            if tool in ("Edit", "MultiEdit", "NotebookEdit"):
                block(f"write-once: {pat} only accepts new files")
            if tool == "Write" and os.path.exists(path):
                block(f"write-once: {pat} only accepts new files")
        if mutating and cmd_matches(pat, cmd):
            block(f"write-once: {pat} only accepts new files")

    sys.exit(0)


if __name__ == "__main__":
    main()
