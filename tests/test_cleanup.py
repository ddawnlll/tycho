"""Acceptance tests for scripts/cleanup.py."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup.py"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True)


def build_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "dev@example.test")
    git(repo, "config", "user.name", "Dev")
    (repo / "PROGRESS.md").write_text("# PROGRESS\n\noriginal progress\n")
    (repo / "PLAN.md").write_text("# PLAN\n")
    (repo / "config").mkdir()
    (repo / "config" / "tycho.toml").write_text('value = "PENDING"\n')
    (repo / "ledger").mkdir()
    (repo / "ledger" / "trials.jsonl").write_text("{}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")

    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "mod.pyc").write_bytes(b"pyc")
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "state").write_text("cache")
    (repo / "logs").mkdir()
    (repo / "logs" / "a.log.1").write_text("rotated")
    (repo / "logs" / "a.log").write_text("current")
    (repo / "tmp").mkdir()
    (repo / "tmp" / "junk.bin").write_bytes(b"junk")

    digest = hashlib.sha256(b"payload").hexdigest()
    verified = repo / "runs" / "modal" / "verified"
    verified.mkdir(parents=True)
    (verified / "VERIFIED").write_text(digest + "\n")
    (verified / "local.sha256").write_text(digest + "\n")
    unverified = repo / "runs" / "modal" / "unverified"
    unverified.mkdir()
    (unverified / "VERIFIED").write_text("a" * 64 + "\n")
    (unverified / "local.sha256").write_text("b" * 64 + "\n")

    merged = tmp_path / "wt_merged"
    git(repo, "worktree", "add", "-q", "-b", "merged", str(merged))
    (merged / "merged.txt").write_text("merged\n")
    git(merged, "add", "-A")
    git(merged, "commit", "-q", "-m", "merged work")
    git(repo, "merge", "--no-edit", "merged")

    unmerged = tmp_path / "wt_unmerged"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(unmerged))
    (unmerged / "feature.txt").write_text("feature\n")
    git(unmerged, "add", "-A")
    git(unmerged, "commit", "-q", "-m", "feature work")
    return repo, merged, unmerged


@pytest.fixture
def fake_cache_bin(tmp_path):
    """Stand-in uv/pip so --apply never touches the real caches."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    log = tmp_path / "cache_calls.log"
    for name in ("uv", "pip"):
        exe = bindir / name
        exe.write_text(f"#!/bin/sh\necho \"{name} $*\" >> '{log}'\n")
        exe.chmod(0o755)
    return bindir, log


def run_cleanup(repo, *args, pathdir=None):
    env = os.environ.copy()
    if pathdir is not None:
        env["PATH"] = str(pathdir) + os.pathsep + env.get("PATH", "")
    return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), *args],
                          capture_output=True, text=True, env=env)


def test_dry_run_then_apply(tmp_path, fake_cache_bin):
    bindir, cache_log = fake_cache_bin
    repo, merged, unmerged = build_repo(tmp_path)
    expected = {
        "__pycache__",
        ".pytest_cache",
        "logs/a.log.1",
        "runs/modal/verified",
        "tmp/junk.bin",
        str(merged.resolve()),
        str(unmerged.resolve()),
    }

    blocked = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)
    assert blocked.returncode != 0
    assert (repo / "__pycache__").exists()

    dry = run_cleanup(repo, "--include-worktrees", pathdir=bindir)
    assert dry.returncode == 0, dry.stderr
    record = json.loads((repo / "runs" / "cleanup" / "last_dryrun.json").read_text())
    rows = record["candidates"]
    assert {c["path"] for c in rows} == expected
    assert all(c["size"] > 0 for c in rows)

    progress = (repo / "PROGRESS.md").read_text()
    assert "### Cleanup dry-run " in progress
    for row in rows:
        assert f"`{row['path']}` ({row['kind']}, {row['size']} bytes)" in progress
        assert row["path"] in dry.stdout
    assert "command: `uv cache prune`" in progress
    assert "command: `pip cache purge`" in progress

    for rel in ("__pycache__", ".pytest_cache", "logs/a.log.1",
                "runs/modal/verified", "tmp/junk.bin"):
        assert (repo / rel).exists()
    assert merged.exists() and unmerged.exists()
    assert not cache_log.exists()

    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)
    assert applied.returncode == 0, applied.stderr
    for rel in ("__pycache__", ".pytest_cache", "logs/a.log.1",
                "runs/modal/verified", "tmp/junk.bin"):
        assert not (repo / rel).exists()
    assert not merged.exists() and not unmerged.exists()
    assert "wt_merged" not in git(repo, "worktree", "list", "--porcelain").stdout
    assert "wt_unmerged" not in git(repo, "worktree", "list", "--porcelain").stdout

    assert (repo / "runs" / "modal" / "unverified").is_dir()
    assert (repo / "logs" / "a.log").exists()
    assert (repo / "ledger" / "trials.jsonl").read_text() == "{}\n"
    assert 'value = "PENDING"' in (repo / "config" / "tycho.toml").read_text()
    assert (repo / "PLAN.md").exists()
    progress = (repo / "PROGRESS.md").read_text()
    assert "original progress" in progress
    assert "### Cleanup applied" in progress

    patch = repo / "runs" / "archive" / "wt_unmerged.patch"
    assert patch.is_file() and patch.stat().st_size > 0
    assert "feature work" in patch.read_text()
    assert not (repo / "runs" / "archive" / "wt_merged.patch").exists()

    calls = cache_log.read_text()
    assert "uv cache prune" in calls
    assert "pip cache purge" in calls


def test_protected_candidate_aborts(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, _, _ = build_repo(tmp_path)
    cached = repo / "ledger" / "__pycache__"
    cached.mkdir()
    (cached / "t.pyc").write_bytes(b"x")
    before = (repo / "PROGRESS.md").read_text()

    out = run_cleanup(repo, "--include-worktrees", pathdir=bindir)

    assert out.returncode != 0
    assert "protected" in (out.stdout + out.stderr).lower()
    assert (cached / "t.pyc").exists()
    assert (repo / "PROGRESS.md").read_text() == before
    assert not (repo / "runs" / "cleanup" / "last_dryrun.json").exists()
