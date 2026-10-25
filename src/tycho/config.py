"""Config loading and PENDING-parameter refusal."""

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG = _REPO_ROOT / "config" / "tycho.toml"


class PendingParameterError(RuntimeError):
    """A monograph parameter is still PENDING."""


def load(path=None) -> dict:
    with open(path or _DEFAULT_CONFIG, "rb") as f:
        return tomllib.load(f)


def require(value, name):
    if value == "PENDING" or value == "":
        raise PendingParameterError(f"{name} is PENDING")
    return value
