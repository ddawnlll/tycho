"""Append-only hash-chained trial log (§19 deney kaydı, K-03).

Each line carries prev_hash (sha256 of the previous line's hash, GENESIS for the
first) and hash = sha256 of the canonical JSON of the record including prev_hash.
Existing lines are never rewritten: tampering breaks verify_chain.
"""

import hashlib
import json
from pathlib import Path

GENESIS = "GENESIS"
_RESERVED = ("prev_hash", "hash")


def _canonical(record) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"))


def _hash(record, prev_hash) -> str:
    return hashlib.sha256(
        _canonical({**record, "prev_hash": prev_hash}).encode()
    ).hexdigest()


def append_trial(path, record) -> dict:
    path = Path(path)
    reserved = [key for key in _RESERVED if key in record]
    if reserved:
        raise ValueError(f"record may not set reserved keys: {reserved}")
    record = dict(record)
    prev_hash = _last_hash(path)
    stored = {**record, "prev_hash": prev_hash, "hash": _hash(record, prev_hash)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(_canonical(stored) + "\n")
    return stored


def _last_hash(path) -> str:
    if not path.exists():
        return GENESIS
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1])["hash"] if lines else GENESIS


def verify_chain(path) -> bool:
    """True when every line links to its predecessor and its hash still matches."""
    path = Path(path)
    if not path.exists():
        return True
    prev_hash = GENESIS
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            stored = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(stored, dict) or stored.get("prev_hash") != prev_hash:
            return False
        body = {key: value for key, value in stored.items() if key != "hash"}
        if _hash(body, prev_hash) != stored.get("hash"):
            return False
        prev_hash = stored["hash"]
    return True
