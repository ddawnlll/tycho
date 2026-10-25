import pytest

import tycho
from tycho.config import PendingParameterError, load, require


def test_version():
    assert tycho.__version__ == "0.1.0"


def test_load():
    cfg = load()
    assert isinstance(cfg, dict)
    assert cfg["signal"]["ma_periods_days"] == [20, 50, 100, 200]


def test_require_pending_raises():
    cfg = load()
    with pytest.raises(PendingParameterError, match="dev_end_date"):
        require(cfg["periods"]["dev_end_date"], "dev_end_date")


def test_require_value():
    assert require(5, "x") == 5
