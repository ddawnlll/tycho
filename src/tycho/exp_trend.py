"""C-03 EXP-TREND-E001: mean sign(P/MA_k - 1) trend Expert (08A.3-08A.4, s.14 E-001)."""

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .contracts import ExpertSignal, MarketStateRequest, canonical_hash
from .marketstate import UNIVERSE_ID
from .runtime import make_manifest

EXPERT_ID = "EXP-TREND-E001"
# Label for the SnapshotRef field layout in contracts.py (not a research parameter).
SNAPSHOT_SCHEMA = "1"
_FACTORS = ("ma_20", "ma_50", "ma_100", "ma_200", "ewma_vol_60d")
_CROSS_SECTION = ("listing_state", "contract_spec", "trailing_30d_liquidity")
_SOURCE = Path(__file__).read_text()


class TrendExpert:
    def __init__(self, ma_periods, max_staleness, pre_registration_id=None):
        self.ma_periods = list(ma_periods)
        self.max_staleness = pd.Timedelta(max_staleness)
        self.params = {"ma_periods": list(ma_periods)}
        self.manifest = make_manifest(
            _SOURCE,
            self.params,
            expert_id=EXPERT_ID,
            semantic_version="1.0-draft",
            snapshot_schema=SNAPSHOT_SCHEMA,
            owner="research",
            sleeve="T",
            research_status="DRAFT",
            request_schema_version="1",
            universe_contract=UNIVERSE_ID,
            decision_schedule="1d after closed bar",
            output_schema_version="1",
            pre_registration_id=pre_registration_id,
            evidence_ids=(),
            requires=("bars_1d",),
            portfolio_budget_class="T",
        )

    def request(self, as_of):
        return MarketStateRequest(
            expert_id=EXPERT_ID,
            request_version="1",
            universe_id=UNIVERSE_ID,
            as_of=pd.Timestamp(as_of),
            timeframes=(("1d", 260, ("close", "volume")),),
            factors=_FACTORS,
            cross_section=_CROSS_SECTION,
            max_staleness=self.max_staleness,
        )

    def signal(self, snapshot, factor_frame):
        manifest_hash = self.manifest.manifest_hash()
        closes = (
            snapshot.bars.sort_values(["instrument_id", "event_time"], kind="stable")
            .groupby("instrument_id").tail(1)
            .set_index("instrument_id")["close"]
        )
        signals = []
        for instrument_id in snapshot.universe:
            close = float(closes.get(instrument_id, math.nan))
            mas = {
                f"ma_{k}": _value(factor_frame.values, instrument_id, f"ma_{k}")
                for k in self.ma_periods
            }
            reason = _unavailable(snapshot.availability, instrument_id)
            if reason is None and math.isnan(close):
                reason = "nan_close"
            if reason is None and any(math.isnan(value) for value in mas.values()):
                reason = "nan_ma"

            if reason is not None:
                evidence = _evidence(snapshot, factor_frame, instrument_id, {"reason": reason})
                signals.append(
                    _signal(snapshot, manifest_hash, instrument_id, None, "ABSTAIN", reason, {}, evidence)
                )
                continue

            signs = {name: int(np.sign(close / value - 1.0)) for name, value in mas.items()}
            score = float(np.mean(list(signs.values())))
            direction = "LONG" if score > 0 else "SHORT" if score < 0 else "FLAT"
            evidence = _evidence(
                snapshot, factor_frame, instrument_id, {"close": close, "mas": mas}
            )
            signals.append(
                _signal(snapshot, manifest_hash, instrument_id, score, direction, None, signs, evidence)
            )
        return signals


def _value(values, instrument_id, name):
    if instrument_id not in values.index or name not in values.columns:
        return math.nan
    return float(values.at[instrument_id, name])


def _unavailable(availability, instrument_id):
    if instrument_id not in availability.index:
        return "missing_data"
    row = availability.loc[instrument_id]
    for column, reason in (
        ("missing", "missing_data"),
        ("stale", "stale_data"),
        ("delisted", "delisted"),
    ):
        if bool(row.get(column, False)):
            return reason
    return None


def _evidence(snapshot, factor_frame, instrument_id, inputs):
    return canonical_hash(
        {
            "snapshot_id": snapshot.snapshot_id,
            "feature_code_hash": factor_frame.feature_code_hash,
            "instrument_id": instrument_id,
            "inputs": inputs,
        }
    )


def _signal(snapshot, manifest_hash, instrument_id, score, direction, reason, diagnostics, evidence):
    return ExpertSignal(
        expert_id=EXPERT_ID,
        manifest_hash=manifest_hash,
        snapshot_id=snapshot.snapshot_id,
        as_of=snapshot.as_of,
        effective_at=snapshot.as_of,
        valid_until=snapshot.as_of + pd.Timedelta(days=1),
        instrument_id=instrument_id,
        score=score,
        direction=direction,
        abstain_reason=reason,
        diagnostics=diagnostics,
        source_evidence_hash=evidence,
    )
