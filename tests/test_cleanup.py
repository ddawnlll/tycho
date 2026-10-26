"""Acceptance tests for scripts/cleanup.py."""

import hashlib
import json
import os
import importlib.util
import subprocess
import sys
import tarfile
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


def test_untracked_files_archived_before_worktree_removal(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, _, unmerged = build_repo(tmp_path)
    (unmerged / "scratch.txt").write_text("unsaved notes\n")

    assert run_cleanup(repo, "--include-worktrees", pathdir=bindir).returncode == 0
    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)

    assert applied.returncode == 0, applied.stderr
    assert not unmerged.exists()
    archive = repo / "runs" / "archive" / "wt_unmerged.untracked.tar.gz"
    assert archive.is_file()
    with tarfile.open(archive) as tar:
        assert tar.extractfile("scratch.txt").read() == b"unsaved notes\n"
    assert "wt_unmerged.untracked.tar.gz" in applied.stdout


def test_archive_failure_keeps_worktree(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, merged, unmerged = build_repo(tmp_path)
    (unmerged / "scratch.txt").write_text("unsaved\n")
    # A directory where the untracked archive must be written makes that write fail.
    (repo / "runs" / "archive" / "wt_unmerged.untracked.tar.gz").mkdir(parents=True)

    assert run_cleanup(repo, "--include-worktrees", pathdir=bindir).returncode == 0
    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)

    assert applied.returncode == 1
    assert unmerged.exists() and (unmerged / "scratch.txt").exists()
    assert not merged.exists()
    progress = (repo / "PROGRESS.md").read_text()
    assert "### Cleanup applied" in progress
    assert "kept: archive failed" in progress
    assert "wt_unmerged" in progress


def test_symlinked_candidates_and_roots_skipped(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, _, _ = build_repo(tmp_path)
    outside = tmp_path / "outside"
    (outside / "modalcopy").mkdir(parents=True)
    digest = hashlib.sha256(b"outside").hexdigest()
    (outside / "modalcopy" / "VERIFIED").write_text(digest + "\n")
    (outside / "modalcopy" / "local.sha256").write_text(digest + "\n")
    (outside / "keep.txt").write_text("must survive\n")

    (repo / "logs" / "escape.log.1").symlink_to(outside / "keep.txt")
    (repo / "runs" / "modal" / "linkdir").symlink_to(outside / "modalcopy")
    fake_tmp = tmp_path / "fake_tmp"
    fake_tmp.mkdir()
    (fake_tmp / "victim.txt").write_text("must survive\n")
    (repo / "tmp").rename(tmp_path / "real_tmp")
    (repo / "tmp").symlink_to(fake_tmp)

    dry = run_cleanup(repo, "--include-worktrees", pathdir=bindir)
    assert dry.returncode == 0, dry.stderr
    rows = json.loads((repo / "runs" / "cleanup" / "last_dryrun.json").read_text())["candidates"]
    paths = {c["path"] for c in rows}
    assert "logs/escape.log.1" not in paths
    assert "runs/modal/linkdir" not in paths
    assert not any(p.startswith("tmp/") for p in paths)

    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)
    assert applied.returncode == 0, applied.stderr
    assert (repo / "logs" / "escape.log.1").is_symlink()
    assert (repo / "runs" / "modal" / "linkdir").is_symlink()
    assert (outside / "keep.txt").read_text() == "must survive\n"
    assert (outside / "modalcopy" / "VERIFIED").is_file()
    assert (fake_tmp / "victim.txt").read_text() == "must survive\n"


def test_protected_check_uses_realpath(tmp_path):
    spec = importlib.util.spec_from_file_location("cleanup", SCRIPT)
    cleanup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cleanup)
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "link").symlink_to(repo / "config")

    assert cleanup.protected(repo, repo / "config" / "__pycache__")
    assert cleanup.protected(repo, repo / "link" / "__pycache__")
    assert not cleanup.protected(repo, repo / "tmp" / "__pycache__")


def test_base_branch_detected_from_main_worktree(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, merged, unmerged = build_repo(tmp_path)
    git(repo, "branch", "-m", "main", "trunk")  # base is not hardcoded to 'main'

    assert run_cleanup(repo, "--include-worktrees", pathdir=bindir).returncode == 0
    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)

    assert applied.returncode == 0, applied.stderr
    assert not merged.exists() and not unmerged.exists()
    assert "feature work" in (repo / "runs" / "archive" / "wt_unmerged.patch").read_text()


def test_undetectable_base_keeps_worktree_with_commits(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, merged, unmerged = build_repo(tmp_path)
    assert run_cleanup(repo, "--include-worktrees", pathdir=bindir).returncode == 0
    # A detached main worktree has no branch to serve as the base.
    git(repo, "checkout", "-q", "--detach", "HEAD")

    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)

    assert applied.returncode == 1
    assert unmerged.exists()
    assert not merged.exists()
    progress = (repo / "PROGRESS.md").read_text()
    assert "### Cleanup applied" in progress
    assert "base branch undetected" in progress


def test_locked_worktree_is_kept(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, _, unmerged = build_repo(tmp_path)
    git(repo, "worktree", "lock", str(unmerged))

    assert run_cleanup(repo, "--include-worktrees", pathdir=bindir).returncode == 0
    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)

    assert applied.returncode == 0, applied.stderr
    assert unmerged.exists()
    assert "kept: locked" in applied.stdout
    assert "locked" in git(repo, "worktree", "list", "--porcelain").stdout


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("include_worktrees", [False, True])
def test_generic_cleanup_cannot_delete_locked_worktrees(tmp_path, fake_cache_bin,
                                                       nested, include_worktrees):
    bindir, _ = fake_cache_bin
    repo, _, unmerged = build_repo(tmp_path)
    parent = repo / "tmp" / "container" if nested else repo / "tmp"
    parent.mkdir(exist_ok=True)
    moved = parent / "locked-wt"
    git(repo, "worktree", "move", str(unmerged), str(moved))
    git(repo, "worktree", "lock", "--reason", "active worker", str(moved))
    (moved / "scratch.txt").write_bytes(b"must survive\x00\xff")
    cache = moved / "__pycache__"
    cache.mkdir()
    (cache / "work.pyc").write_bytes(b"locked cache")
    args = ["--include-worktrees"] if include_worktrees else []
    assert run_cleanup(repo, *args, pathdir=bindir).returncode == 0
    result = run_cleanup(repo, "--apply", *args, pathdir=bindir)
    assert result.returncode == 0, result.stderr
    assert (moved / "scratch.txt").read_bytes() == b"must survive\x00\xff"
    assert (cache / "work.pyc").read_bytes() == b"locked cache"
    assert "locked active worker" in git(repo, "worktree", "list", "--porcelain").stdout
    if include_worktrees:
        assert "kept: locked" in result.stdout
    assert not (repo / "tmp" / "junk.bin").exists()


def test_generic_parent_cannot_delete_archive_failed_worktree(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, _, unmerged = build_repo(tmp_path)
    parent = repo / "tmp" / "container"
    parent.mkdir()
    moved = parent / "nested-wt"
    git(repo, "worktree", "move", str(unmerged), str(moved))
    (moved / "scratch.txt").write_bytes(b"unsaved\x00\xff")
    (repo / "runs" / "archive" / "nested-wt.untracked.tar.gz").mkdir(parents=True)
    assert run_cleanup(repo, "--include-worktrees", pathdir=bindir).returncode == 0
    result = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)
    assert result.returncode == 1
    assert "kept: archive failed" in result.stdout
    assert (moved / "scratch.txt").read_bytes() == b"unsaved\x00\xff"
    assert str(moved) in git(repo, "worktree", "list", "--porcelain").stdout


@pytest.mark.parametrize("failed_command", ["status", "rev-list", "diff", "format-patch", "ls-files"])
def test_failed_archive_git_read_keeps_worktree(tmp_path, fake_cache_bin, monkeypatch,
                                               failed_command):
    bindir, _ = fake_cache_bin
    repo, _, unmerged = build_repo(tmp_path)
    (unmerged / "scratch.txt").write_text("unsaved notes\n")
    spec = importlib.util.spec_from_file_location("cleanup", SCRIPT)
    cleanup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cleanup)
    real_git = cleanup.git

    def failing_git(cwd, *args, **kwargs):
        if args[0] == failed_command:
            raise subprocess.CalledProcessError(128, ["git", *args], stderr="read failed")
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    assert cleanup.main(["--repo", str(repo), "--include-worktrees"]) == 0
    monkeypatch.setattr(cleanup, "git", failing_git)
    assert cleanup.main(["--repo", str(repo), "--apply", "--include-worktrees"]) == 1
    assert unmerged.exists()
    assert (unmerged / "scratch.txt").read_text() == "unsaved notes\n"
    assert "kept: archive failed" in (repo / "PROGRESS.md").read_text()


def test_corrupt_worktree_kept_others_continue(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, merged, unmerged = build_repo(tmp_path)
    # A bogus .git file must stop archival/removal for this worktree only.
    (unmerged / ".git").write_text("garbage\n")

    assert run_cleanup(repo, "--include-worktrees", pathdir=bindir).returncode == 0
    applied = run_cleanup(repo, "--apply", "--include-worktrees", pathdir=bindir)

    assert applied.returncode == 1
    assert unmerged.exists()
    assert not merged.exists()
    progress = (repo / "PROGRESS.md").read_text()
    assert "### Cleanup applied" in progress
    assert "wt_unmerged" in progress and "kept: archive failed" in progress
    assert "wt_merged" in progress and "removed" in progress


def test_repo_must_be_main_worktree(tmp_path, fake_cache_bin):
    bindir, _ = fake_cache_bin
    repo, _, unmerged = build_repo(tmp_path)

    out = run_cleanup(unmerged, "--include-worktrees", pathdir=bindir)

    assert out.returncode != 0
    assert "main worktree" in out.stderr
    assert not (repo / "runs" / "cleanup" / "last_dryrun.json").exists()
