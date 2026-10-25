"""C-02 shared factors: PIT MA, close, EWMA vol and liquidity (08A.6)."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import PendingParameterError
from .contracts import FactorFrame

_MA_PERIODS = {"ma_20": 20, "ma_50": 50, "ma_100": 100, "ma_200": 200}
_OTHER_FACTORS = ("close", "ewma_vol_60d", "trailing_30d_liquidity")
_CONVENTION_KEYS = ("kind", "value", "returns", "min_periods")
_KINDS = ("span", "halflife", "com")
_ANNUALIZATION_DAYS = 365  # crypto trades every day


def compute_factors(snapshot, factors, ewma_convention) -> FactorFrame:
    convention = _validate(ewma_convention)
    names = list(factors)
    for name in names:
        if name not in _MA_PERIODS and name not in _OTHER_FACTORS:
            raise ValueError(f"unknown factor: {name!r}")
    groups = _groups(snapshot)

    values = pd.DataFrame(
        np.nan,
        index=pd.Index(snapshot.universe, name="instrument_id"),
        columns=names,
        dtype=float,
    )
    for iid in values.index:
        group = groups.get(iid)
        closes = group["close"] if group is not None else pd.Series(dtype=float)
        for name in names:
            if name in _MA_PERIODS:
                k = _MA_PERIODS[name]
                values.loc[iid, name] = (
                    float(closes.tail(k).mean()) if len(closes) >= k else np.nan
                )
            elif name == "close":
                values.loc[iid, name] = float(closes.iloc[-1]) if len(closes) else np.nan
            elif name == "trailing_30d_liquidity":
                volume = (
                    group["quote_volume"] if group is not None else pd.Series(dtype=float)
                )
                values.loc[iid, name] = (
                    float(volume.tail(30).mean()) if len(volume) >= 30 else np.nan
                )
            else:
                values.loc[iid, name] = _ewma_vol(closes, convention)

    return FactorFrame(
        snapshot_id=snapshot.snapshot_id,
        feature_code_hash=feature_code_hash(convention, names),
        values=values,
    )


def ewma_cov(snapshot, instruments, ewma_convention) -> pd.DataFrame:
    """Annualized EWMA covariance on returns aligned by bar time."""
    convention = _validate(ewma_convention)
    instruments = list(instruments)
    groups = _groups(snapshot)
    returns = {}
    for iid in instruments:
        group = groups.get(iid)
        if group is None or group.empty:
            continue
        series = _returns(group["close"], convention)
        series.index = pd.DatetimeIndex(group["event_time"].to_numpy())
        returns[iid] = series
    aligned = pd.DataFrame(returns)
    if aligned.empty:
        return pd.DataFrame(
            np.nan, index=instruments, columns=instruments, dtype=float
        )

    cov = pd.DataFrame(
        np.nan, index=instruments, columns=instruments, dtype=float
    )
    for i, left in enumerate(instruments):
        if left not in aligned:
            continue
        for right in instruments[i:]:
            if right not in aligned:
                continue
            # pair on common observations only, so a diagonal pair reproduces
            # the compact series that _ewma_vol uses (gap-insensitive)
            products = (aligned[left] * aligned[right]).dropna()
            if products.empty:
                continue
            variance = _ewma(products, convention).iloc[-1]
            value = float(variance * _ANNUALIZATION_DAYS)
            cov.loc[left, right] = cov.loc[right, left] = value
    return cov


def _groups(snapshot) -> dict:
    bars = snapshot.bars.sort_values(["instrument_id", "event_time"], kind="stable")
    return {iid: group for iid, group in bars.groupby("instrument_id", sort=True)}


def _returns(closes, convention) -> pd.Series:
    closes = pd.Series(closes, dtype=float).reset_index(drop=True)
    if convention["returns"] == "log":
        return np.log(closes).diff()
    return closes.pct_change()


def _ewma(values: pd.Series, convention) -> pd.Series:
    kwargs = {convention["kind"]: float(convention["value"])}
    # ignore_na=True: a NaN return is a skipped observation, so a compacted pair
    # series (ewma_cov) and the raw series (_ewma_vol) decay identically.
    return values.ewm(
        adjust=False,
        ignore_na=True,
        min_periods=int(convention["min_periods"]),
        **kwargs,
    ).mean()


def _ewma_vol(closes, convention) -> float:
    # Zero-mean convention: variance of r around zero (E[r^2]), never demeaned.
    variance = _ewma(_returns(closes, convention) ** 2, convention)
    if variance.empty or pd.isna(variance.iloc[-1]):
        return np.nan
    return float(np.sqrt(variance.iloc[-1] * _ANNUALIZATION_DAYS))


def feature_code_hash(convention, names) -> str:
    """Hash of the factor code plus the convention and requested names."""
    convention = _validate(convention)
    digest = hashlib.sha256(Path(__file__).read_bytes())
    canonical = json.dumps(
        [
            {
                "kind": convention["kind"],
                "value": float(convention["value"]),
                "returns": convention["returns"],
                "min_periods": int(convention["min_periods"]),
            },
            sorted(names),
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    digest.update(canonical.encode())
    return digest.hexdigest()


def _validate(convention):
    if convention is None:
        raise PendingParameterError("ewma_convention is PENDING")
    missing = [key for key in _CONVENTION_KEYS if key not in convention]
    if missing:
        raise PendingParameterError(f"ewma_convention missing: {missing}")
    if any(convention[key] == "PENDING" for key in _CONVENTION_KEYS):
        raise PendingParameterError("ewma_convention is PENDING")
    if convention["kind"] not in _KINDS:
        raise ValueError(f"unknown ewma kind: {convention['kind']!r}")
    if convention["returns"] not in ("log", "simple"):
        raise ValueError(f"unknown ewma returns: {convention['returns']!r}")
    return convention
