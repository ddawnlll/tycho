"""Hash-chained append-only trial log (§19 deney kaydı)."""

import json

import pytest

from tycho.trials import GENESIS, append_trial, verify_chain


def test_append_chains_and_verifies(tmp_path):
    path = tmp_path / "trials.jsonl"
    first = append_trial(path, {"trial": 1, "sharpe": 0.4})
    second = append_trial(path, {"trial": 2, "sharpe": 0.5})

    assert first["prev_hash"] == GENESIS
    assert second["prev_hash"] == first["hash"]
    assert verify_chain(path)

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["trial"] for line in lines] == [1, 2]
    assert lines[1]["prev_hash"] == lines[0]["hash"]


def test_append_never_rewrites_existing_lines(tmp_path):
    path = tmp_path / "trials.jsonl"
    append_trial(path, {"trial": 1})
    before = path.read_text()
    append_trial(path, {"trial": 2})
    assert path.read_text().startswith(before)


def test_empty_or_missing_log_verifies(tmp_path):
    assert verify_chain(tmp_path / "missing.jsonl")
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert verify_chain(path)


def test_tampered_record_is_detected(tmp_path):
    path = tmp_path / "trials.jsonl"
    append_trial(path, {"trial": 1, "sharpe": 0.4})
    append_trial(path, {"trial": 2, "sharpe": 0.5})
    lines = path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["sharpe"] = 9.9  # edit without recomputing the hash
    path.write_text("\n".join([json.dumps(tampered), lines[1]]) + "\n")
    assert not verify_chain(path)


def test_deleted_middle_line_breaks_the_chain(tmp_path):
    path = tmp_path / "trials.jsonl"
    for trial in (1, 2, 3):
        append_trial(path, {"trial": trial})
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert not verify_chain(path)


def test_reserved_keys_are_refused(tmp_path):
    with pytest.raises(ValueError):
        append_trial(tmp_path / "trials.jsonl", {"hash": "forged"})
