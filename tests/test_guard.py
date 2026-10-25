import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard.py"


def event(tool, cwd, **tool_input):
    return {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)}


def run(event, cwd):
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        cwd=cwd,
    )


DENY_CASES = [
    ("data/holdout/", "data/holdout/tape.parquet", "cat data/holdout/tape.parquet"),
    ("secrets/", "secrets/api.json", "cat secrets/api.json"),
    (".env", ".env", "cat .env"),
    ("*.key", "config/signing.key", "cat config/signing.key"),
    ("*.pem", "config/cert.pem", "cat config/cert.pem"),
    (".modal.toml", ".modal.toml", "cat .modal.toml"),
]


@pytest.mark.parametrize("pattern,path,command", DENY_CASES)
def test_deny_read_grep_bash(tmp_path, pattern, path, command):
    for tool, kwargs in (("Read", {"file_path": path}), ("Grep", {"path": path})):
        result = run(event(tool, tmp_path, **kwargs), tmp_path)
        assert result.returncode == 2, (pattern, tool, result.stderr)
        assert pattern in result.stderr
    result = run(event("Bash", tmp_path, command=command), tmp_path)
    assert result.returncode == 2, (pattern, result.stderr)


def test_write_once(tmp_path):
    golden = tmp_path / "tests" / "golden"
    golden.mkdir(parents=True)
    (golden / "a.json").write_text("{}")

    assert run(event("Write", tmp_path, file_path="tests/golden/new.json"), tmp_path).returncode == 0
    assert run(event("Write", tmp_path, file_path="tests/golden/a.json"), tmp_path).returncode == 2
    assert run(event("Edit", tmp_path, file_path="tests/golden/a.json"), tmp_path).returncode == 2
    assert run(event("Read", tmp_path, file_path="tests/golden/a.json"), tmp_path).returncode == 0

    assert run(event("Bash", tmp_path, command="rm tests/golden/a.json"), tmp_path).returncode == 2
    assert run(event("Bash", tmp_path, command="cat tests/golden/a.json"), tmp_path).returncode == 0

    worktree_path = "/x/.claude/worktrees/a/tests/golden/c.json"
    assert run(event("Edit", tmp_path, file_path=worktree_path), tmp_path).returncode == 2
    assert run(event("Read", tmp_path, file_path=worktree_path), tmp_path).returncode == 0
    assert run(event("Write", tmp_path, file_path=worktree_path), tmp_path).returncode == 0


def test_immutable_edit_blocked(tmp_path):
    result = run(event("Edit", tmp_path, file_path=".claude/settings.json"), tmp_path)
    assert result.returncode == 2
    assert "immutable" in result.stderr
    assert run(event("Bash", tmp_path, command="rm .claude/hooks/guard.py"), tmp_path).returncode == 2


def test_blocked_hosts(tmp_path):
    result = run(event("Bash", tmp_path, command="curl https://api.binance.com/api/v3/ping"), tmp_path)
    assert result.returncode == 2
    assert "real market data BLOCKED until owner sets dev_end_date" in result.stderr
    result = run(event("WebFetch", tmp_path, url="https://www.binance.vision/data/spot"), tmp_path)
    assert result.returncode == 2
    assert "real market data BLOCKED until owner sets dev_end_date" in result.stderr


def test_benign_allowed(tmp_path):
    assert run(event("Write", tmp_path, file_path="src/tycho/x.py", content=""), tmp_path).returncode == 0
    assert run(event("Bash", tmp_path, command="uv run pytest -q"), tmp_path).returncode == 0


def test_malformed_stdin_blocked(tmp_path):
    result = subprocess.run(
        [sys.executable, str(GUARD)], input="{not json", text=True, capture_output=True, cwd=tmp_path
    )
    assert result.returncode == 2
    assert result.stderr


def test_missing_rules_blocked(tmp_path):
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shutil.copy(GUARD, hooks / "guard.py")
    result = subprocess.run(
        [sys.executable, str(hooks / "guard.py")],
        input=json.dumps(event("Read", tmp_path, file_path="x")),
        text=True,
        capture_output=True,
        cwd=tmp_path,
    )
    assert result.returncode == 2
