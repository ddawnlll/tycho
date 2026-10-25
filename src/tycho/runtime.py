"""C-03 expert runtime: manifest hashing, isolated resolution, purity checks (08A.3-08A.8)."""

import dataclasses
import hashlib

from .contracts import (
    ExpertManifest,
    ExpertSignal,
    FactorFrame,
    SnapshotRef,
    canonical_hash,
)


def make_manifest(expert_module_source: str, params: dict, **fields) -> ExpertManifest:
    return ExpertManifest(
        code_hash=hashlib.sha256(expert_module_source.encode()).hexdigest(),
        params_hash=canonical_hash(params),
        **fields,
    )


def run_expert(expert, provider, as_of, factor_fn) -> list[ExpertSignal]:
    """Resolve the expert's request, hand it a snapshot and factor frame, enforce the contract.

    The expert only ever sees SnapshotRef and FactorFrame: the tape and the
    provider stay behind this boundary.
    """
    manifest = expert.manifest
    # Hash the live params: a param changed after the manifest was hashed would
    # make the run contradict the manifest it claims to be.
    if canonical_hash(expert.params) != manifest.params_hash:
        raise ValueError("expert params changed after manifest hashing")

    snapshot = provider.resolve(expert.request(as_of))
    # Capture before the expert sees the snapshot: an expert that mutates its
    # arguments must not be able to relax the ABSTAIN rules it is judged by.
    availability = snapshot.availability.copy()
    universe = snapshot.universe
    # The provider's cached snapshot must never leave this function mutable.
    handoff = _defensive_copy(snapshot)
    # A factor_fn may hand back a cached frame: the expert gets a copy, never it.
    factor_frame = _copy_frame(factor_fn(handoff))
    signals = expert.signal(handoff, factor_frame)
    _enforce(signals, manifest, snapshot, availability, universe)
    return signals


def _defensive_copy(snapshot: SnapshotRef) -> SnapshotRef:
    return dataclasses.replace(
        snapshot,
        bars=snapshot.bars.copy(),
        availability=snapshot.availability.copy(),
    )


def _copy_frame(frame: FactorFrame) -> FactorFrame:
    return dataclasses.replace(frame, values=frame.values.copy())


def _enforce(signals, manifest, snapshot, availability, universe) -> None:
    manifest_hash = manifest.manifest_hash()
    universe = set(universe)
    seen = set()
    for signal in signals:
        if signal.expert_id != manifest.id:
            raise ValueError(
                f"signal expert_id {signal.expert_id!r} != manifest id {manifest.id!r}"
            )
        if signal.manifest_hash != manifest_hash:
            raise ValueError("signal manifest_hash does not match the manifest")
        if signal.snapshot_id != snapshot.snapshot_id:
            raise ValueError("signal snapshot_id does not match the snapshot")
        if signal.instrument_id not in universe:
            raise ValueError(
                f"signal for instrument outside universe: {signal.instrument_id!r}"
            )
        if signal.instrument_id in seen:
            raise ValueError(f"more than one signal for {signal.instrument_id!r}")
        seen.add(signal.instrument_id)
        row = availability.loc[signal.instrument_id]
        # bad_quality is read defensively: hand-built availability frames from
        # before that column existed must keep working.
        unavailable = bool(
            row["missing"]
            or row["stale"]
            or row["delisted"]
            or row.get("bad_quality", False)
        )
        if unavailable and signal.direction != "ABSTAIN":
            # An earlier signal is never silently extended over bad data.
            raise ValueError(
                f"{signal.instrument_id}: non-ABSTAIN signal on missing/stale/delisted/bad-quality data"
            )
    absent = universe - seen
    if absent:
        raise ValueError(f"no signal for universe instruments: {sorted(absent)}")
