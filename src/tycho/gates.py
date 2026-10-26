"""E-001 §14 six-criteria gate evaluator (PASS / FAIL / BLOCKED / INVALID).

This module only judges results produced upstream (``stats.py``, ``nulls.py``,
``subperiods.py``); it never computes DSR, alpha, nulls or bootstrap. Thresholds
and levels are required arguments: their G1-frozen values live in config, not
here.

Precedence is fail-closed: INVALID > BLOCKED > FAIL > PASS. Any missing,
unavailable or ``"PENDING"`` input is BLOCKED. Structurally malformed or
inconsistent input is INVALID, which is deliberately distinct from BLOCKED.

Criterion 6 is never evaluated by code: a positive owner attestation makes it
OWNER, a signed negative attestation makes it FAIL, and missing/unsigned stays
BLOCKED. No holdout data is accepted or read here.
"""

import math
from collections.abc import Iterable, Mapping, Sequence

import numpy as np

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
INVALID = "INVALID"
OWNER = "OWNER"

_CRITERIA = ("dsr", "alpha", "null_top1", "ma_components", "subperiods", "holdout")


class _Rejected(Exception):
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason


def evaluate(
    *,
    dsr,
    alpha,
    strategy_metric,
    null_metrics,
    component_net_pnl,
    component_periods,
    subperiod_net_pnl,
    holdout_attestation,
    dsr_threshold,
    alpha_level,
    null_top_fraction,
    null_reps,
):
    """Judge the six E-001 criteria as one deterministic, JSON-safe report.

    ``dsr`` is the deflated-Sharpe probability. ``alpha`` is either the
    projection ``{"alpha": float, "p_one_sided": float}`` or the full
    ``stats.beta_adjusted_alpha`` result (``alpha``, ``alpha_annualized``,
    ``beta``, ``t``, ``p_one_sided``, ``pass_90``); only the two gate fields
    are read. ``null_metrics`` is
    ``{"scramble": [...], "random_entry": [...]}`` with exactly ``null_reps``
    finite samples in each null.

    ``component_net_pnl`` maps each MA period to its net P&L (a mapping or a
    sequence of ``(period, net_pnl)`` pairs). ``component_periods`` is the
    required config ``signal.ma_periods_days`` set (four distinct positive
    integers); the component key set must equal it exactly.

    ``subperiod_net_pnl`` accepts either ``{"up": float, "down": float}`` or
    the full ``subperiods.net_by_subperiod`` result
    (``up``/``down``/``flat``/``unclassified``, each with ``observations`` and
    ``net_pnl``); only up/down ``net_pnl`` are criteria and flat/unclassified
    are ignored. ``alpha_level`` is the one-sided confidence level (0.90 means
    p < 0.10). ``null_top_fraction`` is 0.01 for the top 1% test.

    ``holdout_attestation`` must be
    ``{"owner": str, "signed": bool, "net_sharpe_positive": bool}``. Missing,
    PENDING or unsigned is BLOCKED; a signed negative attestation is FAIL;
    malformed structure is INVALID. The attestation is trusted input supplied
    by the protected owner/judge layer.
    """
    criteria = {
        "dsr": _dsr_criterion(dsr, dsr_threshold),
        "alpha": _alpha_criterion(alpha, alpha_level),
        "null_top1": _null_criterion(
            strategy_metric, null_metrics, null_reps, null_top_fraction
        ),
        "ma_components": _components_criterion(component_net_pnl, component_periods),
        "subperiods": _subperiods_criterion(subperiod_net_pnl),
        "holdout": _holdout_criterion(holdout_attestation),
    }
    return {
        "verdict": _verdict(criteria),
        "criteria": criteria,
        "thresholds": {
            "dsr": _report_value(dsr_threshold),
            "alpha_level": _report_value(alpha_level),
            "null_top_fraction": _report_value(null_top_fraction),
        },
    }


def _verdict(criteria):
    statuses = [criteria[name]["status"] for name in _CRITERIA]
    if INVALID in statuses:
        return INVALID
    if BLOCKED in statuses:
        return BLOCKED
    if FAIL in statuses:
        return FAIL
    # Criteria 1-5 are PASS (no FAIL/INVALID/BLOCKED reached here); criterion 6
    # can only be OWNER from a valid attestation.
    return PASS if criteria["holdout"]["status"] == OWNER else BLOCKED


def _criterion(status, reason):
    return {"status": status, "reason": reason}


def _report_value(value):
    """Keep echoed thresholds JSON-safe even when the input itself is invalid."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float, np.integer, np.floating)):
        value = float(value)
        return value if math.isfinite(value) else str(value)
    return str(value)


def _fmt(value):
    return f"{value:.6g}"


def _reject(status, reason):
    raise _Rejected(status, reason)


def _is_pending(value):
    return isinstance(value, str) and value == "PENDING"


def _num(value, name):
    if value is None or _is_pending(value):
        _reject(BLOCKED, f"{name} is missing/PENDING")
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        _reject(INVALID, f"{name} must be a real number")
    value = float(value)
    if not math.isfinite(value):
        _reject(INVALID, f"{name} must be finite")
    return value


def _threshold(value, name, low, high, inclusive=False):
    value = _num(value, name)
    valid = low <= value <= high if inclusive else low < value < high
    if not valid:
        _reject(INVALID, f"{name} is outside its valid range")
    return value


def _positive_int(value, name):
    if value is None or _is_pending(value):
        _reject(BLOCKED, f"{name} is missing/PENDING")
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        _reject(INVALID, f"{name} must be an integer")
    if int(value) < 1:
        _reject(INVALID, f"{name} must be >= 1")
    return int(value)


def _mapping(value, name, allowed):
    if value is None or _is_pending(value):
        _reject(BLOCKED, f"{name} is missing/PENDING")
    if not isinstance(value, Mapping):
        _reject(INVALID, f"{name} must be a mapping")
    unknown = sorted({str(key) for key in value} - set(allowed))
    if unknown:
        _reject(INVALID, f"{name} has unknown keys: {', '.join(unknown)}")
    return value


def _entry(mapping, key, name):
    if key not in mapping or mapping[key] is None:
        _reject(BLOCKED, f"{name} is missing")
    return mapping[key]


def _distribution(value, name):
    if value is None or _is_pending(value):
        _reject(BLOCKED, f"{name} is missing/PENDING")
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        _reject(INVALID, f"{name} must be a sequence of numbers")
    values = list(value)
    if not values:
        _reject(BLOCKED, f"{name} is empty")
    return [_num(item, f"{name} value") for item in values]


def _expected_periods(value):
    if value is None or _is_pending(value):
        _reject(BLOCKED, "component_periods is missing/PENDING")
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        _reject(INVALID, "component_periods must be a sequence of periods")
    periods = list(value)
    if len(periods) != 4:
        _reject(INVALID, "component_periods must contain the four MA periods")
    seen = set()
    for period in periods:
        if type(period) is not int or period <= 0:
            _reject(INVALID, "component_periods must be positive integers")
        if period in seen:
            _reject(INVALID, f"component_periods has a duplicate period: {period}")
        seen.add(period)
    return seen


def _pairs(value, name):
    if value is None or _is_pending(value):
        _reject(BLOCKED, f"{name} is missing/PENDING")
    if isinstance(value, Mapping):
        items = list(value.items())
    elif isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        _reject(INVALID, f"{name} must be a mapping or (key, value) pairs")
    else:
        items = []
        for item in value:
            if isinstance(item, (str, bytes)) or not isinstance(item, Sequence) or len(item) != 2:
                _reject(INVALID, f"{name} must be a mapping or (key, value) pairs")
            items.append((item[0], item[1]))
    if not items:
        _reject(INVALID, f"{name} is empty")
    seen = set()
    for key, _ in items:
        if type(key) is not int or key <= 0:
            _reject(INVALID, f"{name} keys must be positive integers")
        if key in seen:
            _reject(INVALID, f"{name} has a duplicate key: {key}")
        seen.add(key)
    return items


def _dsr_criterion(dsr, threshold):
    try:
        value = _num(dsr, "dsr")
        if not 0.0 <= value <= 1.0:
            _reject(INVALID, "dsr must be a probability in [0, 1]")
        cut = _threshold(threshold, "dsr_threshold", 0.0, 1.0, inclusive=True)
    except _Rejected as exc:
        return _criterion(exc.status, exc.reason)
    if value >= cut:
        return _criterion(PASS, f"DSR {_fmt(value)} >= {_fmt(cut)}")
    return _criterion(FAIL, f"DSR {_fmt(value)} < {_fmt(cut)}")


def _alpha_criterion(alpha, level):
    try:
        confidence = _threshold(level, "alpha_level", 0.0, 1.0)
        values = _mapping(
            alpha,
            "alpha",
            {"alpha", "alpha_annualized", "beta", "t", "p_one_sided", "pass_90"},
        )
        point = _num(_entry(values, "alpha", "alpha value"), "alpha value")
        p_value = _num(_entry(values, "p_one_sided", "alpha p_one_sided"), "alpha p_one_sided")
        if not 0.0 <= p_value <= 1.0:
            _reject(INVALID, "alpha p_one_sided is outside [0, 1]")
    except _Rejected as exc:
        return _criterion(exc.status, exc.reason)
    cutoff = 1.0 - confidence
    if point > 0.0 and p_value < cutoff:
        return _criterion(
            PASS,
            f"alpha {_fmt(point)} > 0 and p {_fmt(p_value)} < {_fmt(cutoff)}",
        )
    if point <= 0.0:
        return _criterion(FAIL, f"alpha {_fmt(point)} is not > 0")
    return _criterion(FAIL, f"p {_fmt(p_value)} is not < {_fmt(cutoff)}")


def _null_criterion(metric, null_metrics, reps, fraction):
    try:
        strategy = _num(metric, "strategy_metric")
        top = _threshold(fraction, "null_top_fraction", 0.0, 1.0)
        reps = _positive_int(reps, "null_reps")
        values = _mapping(null_metrics, "null_metrics", {"scramble", "random_entry"})
        samples = {
            "run-scramble": _distribution(
                _entry(values, "scramble", "scramble null"), "scramble null"
            ),
            "random-entry": _distribution(
                _entry(values, "random_entry", "random entry null"), "random entry null"
            ),
        }
        for name, sample in samples.items():
            if len(sample) != reps:
                _reject(
                    BLOCKED,
                    f"{name} has {len(sample)} samples, expected null_reps={reps}",
                )
    except _Rejected as exc:
        return _criterion(exc.status, exc.reason)
    cutoffs = {
        name: float(np.quantile(np.asarray(sample, dtype=float), 1.0 - top))
        for name, sample in samples.items()
    }
    failed = [
        f"{name} cutoff {_fmt(cutoff)}"
        for name, cutoff in cutoffs.items()
        if not strategy > cutoff
    ]
    if failed:
        return _criterion(
            FAIL,
            f"strategy {_fmt(strategy)} is not strictly above " + " and ".join(failed),
        )
    return _criterion(
        PASS,
        f"strategy {_fmt(strategy)} is strictly above both top-{_fmt(top * 100)}% cutoffs",
    )


def _components_criterion(component_net_pnl, component_periods):
    try:
        expected = _expected_periods(component_periods)
        items = _pairs(component_net_pnl, "component_net_pnl")
        supplied = {period for period, _ in items}
        extra = supplied - expected
        if extra:
            _reject(INVALID, f"component_net_pnl has unexpected periods: {sorted(extra)}")
        missing = expected - supplied
        if missing:
            _reject(BLOCKED, f"component_net_pnl is missing periods: {sorted(missing)}")
        values = [
            (period, _num(value, f"MA {period} net PnL")) for period, value in items
        ]
    except _Rejected as exc:
        return _criterion(exc.status, exc.reason)
    for period, value in values:
        if value <= 0.0:
            return _criterion(FAIL, f"MA {period} net {_fmt(value)} is not > 0")
    return _criterion(PASS, f"all {len(values)} MA components have net PnL > 0")


def _subperiods_criterion(subperiod_net_pnl):
    try:
        values = _mapping(
            subperiod_net_pnl,
            "subperiod_net_pnl",
            {"up", "down", "flat", "unclassified"},
        )
        net = {}
        for label in ("up", "down"):
            item = _entry(values, label, f"{label} net PnL")
            if isinstance(item, Mapping):
                # net_by_subperiod wraps each label as {observations, net_pnl}.
                item = _mapping(item, f"{label} subperiod", {"observations", "net_pnl"})
                item = _entry(item, "net_pnl", f"{label} net PnL")
            net[label] = _num(item, f"{label} net PnL")
    except _Rejected as exc:
        return _criterion(exc.status, exc.reason)
    for label in ("up", "down"):
        if net[label] <= 0.0:
            return _criterion(FAIL, f"{label} net {_fmt(net[label])} is not > 0")
    return _criterion(PASS, "both BTC-up and BTC-down net PnL are > 0")


def _holdout_criterion(attestation):
    try:
        values = _mapping(
            attestation,
            "holdout_attestation",
            {"owner", "signed", "net_sharpe_positive"},
        )
        for key in ("owner", "signed", "net_sharpe_positive"):
            if key not in values or values[key] is None:
                _reject(BLOCKED, f"holdout_attestation.{key} is missing")
        if not isinstance(values["owner"], str) or not values["owner"].strip():
            _reject(INVALID, "holdout_attestation.owner must be a non-empty string")
        for key in ("signed", "net_sharpe_positive"):
            if not isinstance(values[key], bool):
                _reject(INVALID, f"holdout_attestation.{key} must be a boolean")
        if not values["signed"]:
            _reject(BLOCKED, "holdout_attestation is unsigned")
        if not values["net_sharpe_positive"]:
            _reject(FAIL, "signed holdout attestation reports net Sharpe <= 0")
    except _Rejected as exc:
        return _criterion(exc.status, exc.reason)
    return _criterion(
        OWNER,
        f"owner {values['owner']!r} attests sealed-holdout net Sharpe > 0; data not read",
    )
