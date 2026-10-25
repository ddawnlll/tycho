"""Section 08A.3-08A.4 contracts: request, snapshot, factor frame, manifest, signal."""

import hashlib
import json
from dataclasses import dataclass

import pandas as pd

RESEARCH_STATUSES = frozenset(
    {"DRAFT", "CANDIDATE", "TESTED", "APPROVED", "DEFERRED", "REJECTED"}
)
DIRECTIONS = ("SHORT", "FLAT", "LONG", "ABSTAIN")


def canonical_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def frame_hash(df: pd.DataFrame) -> str:
    """Content digest of a frame; the index is excluded so reindexing keeps identity."""
    digest = hashlib.sha256()
    digest.update(json.dumps([str(column) for column in df.columns]).encode())
    digest.update(pd.util.hash_pandas_object(df, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def snapshot_id_for(dataset_hash, as_of, universe_hash, request_hash, bars) -> str:
    return canonical_hash(
        {
            "dataset_hash": dataset_hash,
            "as_of": pd.Timestamp(as_of).isoformat(),
            "universe_hash": universe_hash,
            "request_hash": request_hash,
            "bars_hash": frame_hash(bars),
        }
    )


@dataclass(frozen=True)
class MarketStateRequest:
    expert_id: str
    request_version: str
    universe_id: str
    as_of: pd.Timestamp
    timeframes: tuple
    factors: tuple
    cross_section: tuple
    max_staleness: pd.Timedelta  # REQUIRED: the monograph value is PENDING
    point_in_time: bool = True
    closed_bars_only: bool = True
    on_missing: str = "ABSTAIN_AND_REPORT"

    def request_hash(self) -> str:
        # as_of is excluded: it is a separate cache key element, so the same
        # declarative request keeps one hash across decision times.
        return canonical_hash(
            {
                "expert_id": self.expert_id,
                "request_version": self.request_version,
                "universe_id": self.universe_id,
                "timeframes": [
                    [tf, int(n), list(fields)] for tf, n, fields in self.timeframes
                ],
                "factors": list(self.factors),
                "cross_section": list(self.cross_section),
                "max_staleness": pd.Timedelta(self.max_staleness).isoformat(),
                "point_in_time": bool(self.point_in_time),
                "closed_bars_only": bool(self.closed_bars_only),
                "on_missing": self.on_missing,
            }
        )


@dataclass(frozen=True, eq=False)  # frames: compare with .frame().equals, not ==
class SnapshotRef:
    snapshot_id: str
    as_of: pd.Timestamp
    dataset_hash: str
    universe_hash: str
    request_hash: str
    universe: tuple
    bars: pd.DataFrame
    availability: pd.DataFrame

    def frame(self) -> pd.DataFrame:
        return self.bars.copy()


@dataclass(frozen=True, eq=False)
class FactorFrame:
    snapshot_id: str
    feature_code_hash: str
    values: pd.DataFrame

    def frame(self) -> pd.DataFrame:
        return self.values.copy()


@dataclass(frozen=True)
class ExpertManifest:
    id: str
    semantic_version: str
    code_hash: str
    params_hash: str
    owner: str
    sleeve: str
    research_status: str
    request_schema_version: str
    universe_contract: str
    decision_schedule: str
    output_schema_version: str
    pre_registration_id: str
    evidence_ids: tuple
    requires: tuple
    portfolio_budget_class: str

    def __post_init__(self):
        if self.research_status not in RESEARCH_STATUSES:
            raise ValueError(f"unknown research_status: {self.research_status!r}")

    def manifest_hash(self) -> str:
        return canonical_hash(
            {
                "id": self.id,
                "semantic_version": self.semantic_version,
                "code_hash": self.code_hash,
                "params_hash": self.params_hash,
                "owner": self.owner,
                "sleeve": self.sleeve,
                "research_status": self.research_status,
                "request_schema_version": self.request_schema_version,
                "universe_contract": self.universe_contract,
                "decision_schedule": self.decision_schedule,
                "output_schema_version": self.output_schema_version,
                "pre_registration_id": self.pre_registration_id,
                "evidence_ids": list(self.evidence_ids),
                "requires": list(self.requires),
                "portfolio_budget_class": self.portfolio_budget_class,
            }
        )


@dataclass(frozen=True)
class ExpertSignal:
    expert_id: str
    manifest_hash: str
    snapshot_id: str
    as_of: pd.Timestamp
    effective_at: pd.Timestamp
    valid_until: pd.Timestamp
    instrument_id: str
    score: float | None
    direction: str
    abstain_reason: str | None
    diagnostics: dict
    source_evidence_hash: str

    def __post_init__(self):
        if self.direction not in DIRECTIONS:
            raise ValueError(f"unknown direction: {self.direction!r}")
        if self.score is not None and not -1.0 <= self.score <= 1.0:
            raise ValueError(f"score outside [-1,1]: {self.score!r}")
        if self.direction == "ABSTAIN":
            if self.score is not None or not self.abstain_reason:
                raise ValueError("ABSTAIN requires score None and an abstain_reason")
            return
        if self.score is None:
            raise ValueError(f"{self.direction} requires a score")
        if self.direction == "LONG" and not self.score > 0:
            raise ValueError("LONG requires score > 0")
        if self.direction == "SHORT" and not self.score < 0:
            raise ValueError("SHORT requires score < 0")
        if self.direction == "FLAT" and self.score != 0:
            raise ValueError("FLAT requires score == 0")
