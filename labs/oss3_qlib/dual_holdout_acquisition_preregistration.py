"""OSS-3D3D dual holdout acquisition preregistration.

D3D freezes the real raw-data geometry for two future, mutually-exclusive
holdout surfaces before any network acquisition:

* Q1 2026 -> predictive FINAL_HOLDOUT for D2J/D2K;
* Q2 2026 -> ECONOMIC_HOLDOUT reserved for D2M/D2N.

The module is deliberately offline.  It constructs only Binance public-archive
descriptors and an append-only preregistration record.  It never downloads
market bytes, opens archive payloads, materializes features/labels/predictions,
computes metrics, issues/consumes holdout permits, or grants execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from autotrade.research.oss3_market_collection import (
    CANONICAL_DEVELOPMENT_END,
    CANONICAL_INTERVAL,
    CANONICAL_SYMBOLS,
    canonical_oss3d2u_collection_plan,
)
from autotrade.research.oss3_market_snapshot import (
    PROVIDER_ID,
    BinanceSpotArchiveDescriptor,
)


OSS3D3D_PLAN_VERSION = "OSS3D3D_DUAL_HOLDOUT_ACQUISITION_PREREGISTRATION_V1"
OSS3D3D_WINDOW_VERSION = "OSS3D3D_RESERVED_HOLDOUT_WINDOW_V1"
OSS3D3D_REGISTRY_TABLE = "oss3d3d_dual_holdout_acquisition_plans"
PLAN_ID = "oss3d3d-binance-btc-eth-sol-q1q2-2026-v1"
PREDICTIVE_PURPOSE = "PREDICTIVE_FINAL_HOLDOUT"
ECONOMIC_PURPOSE = "ECONOMIC_HOLDOUT"
PREDICTIVE_MONTHS = ("2026-01", "2026-02", "2026-03")
ECONOMIC_MONTHS = ("2026-04", "2026-05", "2026-06")
PREDICTIVE_START = "2026-01-01T00:00:00+00:00"
PREDICTIVE_END = "2026-04-01T00:00:00+00:00"
ECONOMIC_START = "2026-04-01T00:00:00+00:00"
ECONOMIC_END = "2026-07-01T00:00:00+00:00"
UNTOUCHED_FUTURE_START = ECONOMIC_END
WINDOW_POLICY = "CONTIGUOUS_NONOVERLAPPING_CALENDAR_QUARTERS_AFTER_DEVELOPMENT_V1"
ACQUISITION_POLICY = "EXACT_MONTHLY_BINANCE_PUBLIC_ARCHIVES_PREREGISTERED_BEFORE_NETWORK_V1"
SEPARATION_POLICY = "PREDICTIVE_Q1_AND_ECONOMIC_Q2_DISJOINT_NO_REUSE_V1"
POST_RESERVATION_POLICY = "DATA_AT_OR_AFTER_2026_07_01_NOT_RESERVED_BY_D3D_V1"

SOURCE_D3C_CERTIFIED_HEAD = "2f168513482e188ed381550ba4ab3780011e600d"
SOURCE_D3C_ARTIFACT_HASH = "1d271c31f506be21d2a99c857a1007cc5676cfcb5ab83fb6a524171a38dbaf43"
SOURCE_D3C_SCIENTIFIC_OUTCOME_HASH = "5b4261b186e30cbdc30ec2c3025305fbb96650247a066cdf470ce53e80f86ea7"
SOURCE_D3A_CERTIFIED_BASELINE_HASH = "ce703916e83161cc71e73e56e4a9ffab5c0989bf630cfbae260a90a5846ca9cc"

# D2J minimums are deliberately copied as numeric safeguards rather than
# importing the D2J module into this pre-acquisition layer.
D2J_MIN_CROSS_SECTIONS = 30
D2J_MIN_TOTAL_OBSERVATIONS = 90
D2J_MIN_SECTION_OBSERVATIONS = 3
EXPECTED_PREDICTIVE_BARS_PER_SYMBOL = 2160
EXPECTED_PREDICTIVE_CROSS_SECTIONS = EXPECTED_PREDICTIVE_BARS_PER_SYMBOL - 1
EXPECTED_PREDICTIVE_TOTAL_OBSERVATIONS = EXPECTED_PREDICTIVE_CROSS_SECTIONS * len(CANONICAL_SYMBOLS)
EXPECTED_ECONOMIC_BARS_PER_SYMBOL = 2184

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


class DualHoldoutAcquisitionError(RuntimeError):
    """Base D3D failure."""


class DualHoldoutAcquisitionIntegrityError(DualHoldoutAcquisitionError):
    """Frozen lineage, descriptor family or durable serialization drifted."""


class DualHoldoutAcquisitionGovernanceError(DualHoldoutAcquisitionError):
    """Requested plan violates pre-acquisition holdout governance."""


@dataclass(frozen=True, slots=True)
class ReservedHoldoutWindow:
    window_version: str
    purpose: str
    partition_start: str
    partition_end: str
    months: tuple[str, ...]
    descriptors: tuple[BinanceSpotArchiveDescriptor, ...]
    expected_bars_per_symbol: int
    market_values_exposed: bool = False
    feature_artifacts_materialized: bool = False
    label_artifacts_materialized: bool = False
    prediction_values_materialized: bool = False
    metrics_computed: bool = False
    holdout_observed: bool = False

    def __post_init__(self) -> None:
        if self.window_version != OSS3D3D_WINDOW_VERSION:
            raise DualHoldoutAcquisitionIntegrityError("noncanonical D3D window version")
        if self.purpose not in {PREDICTIVE_PURPOSE, ECONOMIC_PURPOSE}:
            raise DualHoldoutAcquisitionGovernanceError("invalid D3D holdout purpose")
        start = _parse_utc(self.partition_start, "partition_start")
        end = _parse_utc(self.partition_end, "partition_end")
        if not start < end:
            raise DualHoldoutAcquisitionGovernanceError("D3D holdout window must be positive")
        if not self.months or self.months != tuple(sorted(self.months)):
            raise DualHoldoutAcquisitionIntegrityError("D3D months must be non-empty and ordered")
        expected_descriptors = len(self.months) * len(CANONICAL_SYMBOLS)
        if len(self.descriptors) != expected_descriptors:
            raise DualHoldoutAcquisitionIntegrityError("D3D descriptor family is incomplete")
        if tuple(sorted(self.descriptors, key=lambda d: (d.period, d.instrument.symbol))) != self.descriptors:
            raise DualHoldoutAcquisitionIntegrityError("D3D descriptors are not canonically ordered")
        if len({item.fingerprint for item in self.descriptors}) != len(self.descriptors):
            raise DualHoldoutAcquisitionIntegrityError("D3D descriptor fingerprints are not unique")
        pairs = {(item.period, item.instrument.symbol) for item in self.descriptors}
        expected_pairs = {(month, symbol) for month in self.months for symbol in CANONICAL_SYMBOLS}
        if pairs != expected_pairs:
            raise DualHoldoutAcquisitionIntegrityError("D3D descriptor month/symbol support drifted")
        for descriptor in self.descriptors:
            if descriptor.interval != CANONICAL_INTERVAL or descriptor.granularity != "monthly":
                raise DualHoldoutAcquisitionGovernanceError("D3D requires exact monthly 1h descriptors")
            if descriptor.period not in self.months:
                raise DualHoldoutAcquisitionIntegrityError("D3D descriptor is outside reserved months")
            if descriptor.period_start < start or descriptor.period_end > end:
                raise DualHoldoutAcquisitionIntegrityError("D3D descriptor is outside reserved window")
        first = min(item.period_start for item in self.descriptors)
        last = max(item.period_end for item in self.descriptors)
        if first != start or last != end:
            raise DualHoldoutAcquisitionIntegrityError("D3D descriptor coverage differs from window")
        bars = sum(
            descriptor.expected_rows
            for descriptor in self.descriptors
            if descriptor.instrument.symbol == CANONICAL_SYMBOLS[0]
        )
        if bars != self.expected_bars_per_symbol:
            raise DualHoldoutAcquisitionIntegrityError("D3D expected bar count drifted")
        if (
            self.market_values_exposed
            or self.feature_artifacts_materialized
            or self.label_artifacts_materialized
            or self.prediction_values_materialized
            or self.metrics_computed
            or self.holdout_observed
        ):
            raise DualHoldoutAcquisitionGovernanceError("D3D window must remain value-opaque and unobserved")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    @property
    def descriptor_fingerprints(self) -> tuple[str, ...]:
        return tuple(item.fingerprint for item in self.descriptors)

    def to_dict(self) -> dict[str, object]:
        return {
            "window_version": self.window_version,
            "purpose": self.purpose,
            "partition_start": self.partition_start,
            "partition_end": self.partition_end,
            "months": list(self.months),
            "descriptors": [item.to_dict() for item in self.descriptors],
            "expected_bars_per_symbol": self.expected_bars_per_symbol,
            "market_values_exposed": self.market_values_exposed,
            "feature_artifacts_materialized": self.feature_artifacts_materialized,
            "label_artifacts_materialized": self.label_artifacts_materialized,
            "prediction_values_materialized": self.prediction_values_materialized,
            "metrics_computed": self.metrics_computed,
            "holdout_observed": self.holdout_observed,
        }


@dataclass(frozen=True, slots=True)
class DualHoldoutAcquisitionPlan:
    plan_version: str
    plan_id: str
    source_d3c_certified_head: str
    source_d3c_artifact_hash: str
    source_d3c_scientific_outcome_hash: str
    source_d3a_certified_baseline_hash: str
    source_d2u_plan_fingerprint: str
    development_end: str
    provider_id: str
    symbols: tuple[str, ...]
    interval: str
    granularity: str
    predictive: ReservedHoldoutWindow
    economic: ReservedHoldoutWindow
    window_policy: str
    acquisition_policy: str
    separation_policy: str
    untouched_future_start: str
    post_reservation_policy: str
    predictive_d2j_sample_adequacy_preregistered: bool
    future_network_acquisition_scope_frozen: bool
    network_acquisition_performed: bool
    final_holdout_observed: bool
    economic_holdout_observed: bool
    holdout_permit_issued: bool
    holdout_permit_consumed: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str

    def __post_init__(self) -> None:
        if self.plan_version != OSS3D3D_PLAN_VERSION:
            raise DualHoldoutAcquisitionIntegrityError("noncanonical D3D plan version")
        if not _ID_RE.fullmatch(self.plan_id):
            raise ValueError("invalid D3D plan_id")
        if not _GIT_SHA_RE.fullmatch(self.source_d3c_certified_head):
            raise ValueError("invalid D3C certified head")
        if self.source_d3c_certified_head != SOURCE_D3C_CERTIFIED_HEAD:
            raise DualHoldoutAcquisitionIntegrityError("D3D D3C source head drifted")
        for name in (
            "source_d3c_artifact_hash",
            "source_d3c_scientific_outcome_hash",
            "source_d3a_certified_baseline_hash",
            "source_d2u_plan_fingerprint",
        ):
            _require_hash(getattr(self, name), name)
        if self.source_d3c_artifact_hash != SOURCE_D3C_ARTIFACT_HASH:
            raise DualHoldoutAcquisitionIntegrityError("D3D D3C artifact identity drifted")
        if self.source_d3c_scientific_outcome_hash != SOURCE_D3C_SCIENTIFIC_OUTCOME_HASH:
            raise DualHoldoutAcquisitionIntegrityError("D3D scientific outcome identity drifted")
        if self.source_d3a_certified_baseline_hash != SOURCE_D3A_CERTIFIED_BASELINE_HASH:
            raise DualHoldoutAcquisitionIntegrityError("D3D D3A baseline identity drifted")
        d2u = canonical_oss3d2u_collection_plan()
        if self.source_d2u_plan_fingerprint != d2u.fingerprint:
            raise DualHoldoutAcquisitionIntegrityError("D3D D2U plan identity drifted")
        if self.development_end != CANONICAL_DEVELOPMENT_END.isoformat():
            raise DualHoldoutAcquisitionGovernanceError("D3D must start after exact real DEVELOPMENT end")
        if self.provider_id != PROVIDER_ID:
            raise DualHoldoutAcquisitionGovernanceError("D3D provider drifted")
        if self.symbols != CANONICAL_SYMBOLS or self.interval != CANONICAL_INTERVAL or self.granularity != "monthly":
            raise DualHoldoutAcquisitionGovernanceError("D3D market geometry must match real D2U universe")
        if self.window_policy != WINDOW_POLICY or self.acquisition_policy != ACQUISITION_POLICY:
            raise DualHoldoutAcquisitionGovernanceError("D3D window/acquisition policy drifted")
        if self.separation_policy != SEPARATION_POLICY:
            raise DualHoldoutAcquisitionGovernanceError("D3D separation policy drifted")
        if self.predictive.purpose != PREDICTIVE_PURPOSE or self.economic.purpose != ECONOMIC_PURPOSE:
            raise DualHoldoutAcquisitionIntegrityError("D3D predictive/economic purpose cross-wire")
        if self.predictive.partition_start != self.development_end:
            raise DualHoldoutAcquisitionGovernanceError("predictive holdout must start exactly at DEVELOPMENT end")
        if self.predictive.partition_end != self.economic.partition_start:
            raise DualHoldoutAcquisitionGovernanceError("D3D reserved windows must be contiguous")
        if _parse_utc(self.predictive.partition_end, "predictive end") > _parse_utc(self.economic.partition_start, "economic start"):
            raise DualHoldoutAcquisitionGovernanceError("D3D predictive/economic windows overlap")
        if set(self.predictive.descriptor_fingerprints) & set(self.economic.descriptor_fingerprints):
            raise DualHoldoutAcquisitionGovernanceError("D3D descriptor reuse across holdouts is forbidden")
        if self.untouched_future_start != self.economic.partition_end or self.untouched_future_start != UNTOUCHED_FUTURE_START:
            raise DualHoldoutAcquisitionGovernanceError("D3D post-reservation boundary drifted")
        if self.post_reservation_policy != POST_RESERVATION_POLICY:
            raise DualHoldoutAcquisitionGovernanceError("D3D post-reservation policy drifted")
        if self.predictive.expected_bars_per_symbol != EXPECTED_PREDICTIVE_BARS_PER_SYMBOL:
            raise DualHoldoutAcquisitionIntegrityError("D3D predictive bar geometry drifted")
        if self.economic.expected_bars_per_symbol != EXPECTED_ECONOMIC_BARS_PER_SYMBOL:
            raise DualHoldoutAcquisitionIntegrityError("D3D economic bar geometry drifted")
        if not (
            EXPECTED_PREDICTIVE_CROSS_SECTIONS >= D2J_MIN_CROSS_SECTIONS
            and EXPECTED_PREDICTIVE_TOTAL_OBSERVATIONS >= D2J_MIN_TOTAL_OBSERVATIONS
            and len(self.symbols) >= D2J_MIN_SECTION_OBSERVATIONS
            and self.predictive_d2j_sample_adequacy_preregistered
        ):
            raise DualHoldoutAcquisitionGovernanceError("D3D predictive holdout does not satisfy preregistered D2J sample floors")
        if not self.future_network_acquisition_scope_frozen:
            raise DualHoldoutAcquisitionGovernanceError("D3D must freeze future acquisition scope")
        if (
            self.network_acquisition_performed
            or self.final_holdout_observed
            or self.economic_holdout_observed
            or self.holdout_permit_issued
            or self.holdout_permit_consumed
            or self.execution_authorized
            or self.paper_execution_authorized
        ):
            raise DualHoldoutAcquisitionGovernanceError("D3D preregistration cannot acquire/observe/authorize holdouts")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise DualHoldoutAcquisitionGovernanceError("D3D cannot grant capital or LIVE authority")

    @property
    def fingerprint(self) -> str:
        return _hash(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_version": self.plan_version,
            "plan_id": self.plan_id,
            "source_d3c_certified_head": self.source_d3c_certified_head,
            "source_d3c_artifact_hash": self.source_d3c_artifact_hash,
            "source_d3c_scientific_outcome_hash": self.source_d3c_scientific_outcome_hash,
            "source_d3a_certified_baseline_hash": self.source_d3a_certified_baseline_hash,
            "source_d2u_plan_fingerprint": self.source_d2u_plan_fingerprint,
            "development_end": self.development_end,
            "provider_id": self.provider_id,
            "symbols": list(self.symbols),
            "interval": self.interval,
            "granularity": self.granularity,
            "predictive": self.predictive.to_dict(),
            "predictive_fingerprint": self.predictive.fingerprint,
            "economic": self.economic.to_dict(),
            "economic_fingerprint": self.economic.fingerprint,
            "window_policy": self.window_policy,
            "acquisition_policy": self.acquisition_policy,
            "separation_policy": self.separation_policy,
            "untouched_future_start": self.untouched_future_start,
            "post_reservation_policy": self.post_reservation_policy,
            "predictive_d2j_sample_adequacy_preregistered": self.predictive_d2j_sample_adequacy_preregistered,
            "future_network_acquisition_scope_frozen": self.future_network_acquisition_scope_frozen,
            "network_acquisition_performed": self.network_acquisition_performed,
            "final_holdout_observed": self.final_holdout_observed,
            "economic_holdout_observed": self.economic_holdout_observed,
            "holdout_permit_issued": self.holdout_permit_issued,
            "holdout_permit_consumed": self.holdout_permit_consumed,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }


class SQLiteDualHoldoutAcquisitionPlanRegistry:
    """Append-only pre-network D3D plan registry."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {OSS3D3D_REGISTRY_TABLE} (
                    plan_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    canonical_json TEXT NOT NULL,
                    preregistered_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS {OSS3D3D_REGISTRY_TABLE}_no_update
                BEFORE UPDATE ON {OSS3D3D_REGISTRY_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, 'OSS3D3D_APPEND_ONLY');
                END;
                CREATE TRIGGER IF NOT EXISTS {OSS3D3D_REGISTRY_TABLE}_no_delete
                BEFORE DELETE ON {OSS3D3D_REGISTRY_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, 'OSS3D3D_APPEND_ONLY');
                END;
                """
            )

    def preregister(self, plan: DualHoldoutAcquisitionPlan, *, now: datetime) -> None:
        if not isinstance(plan, DualHoldoutAcquisitionPlan):
            raise TypeError("plan must be DualHoldoutAcquisitionPlan")
        _require_aware(now, "now")
        canonical = _canonical_json(plan.to_dict())
        timestamp = now.astimezone(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT fingerprint, canonical_json FROM {OSS3D3D_REGISTRY_TABLE} WHERE plan_id = ?",
                (plan.plan_id,),
            ).fetchone()
            if row is not None:
                if str(row["fingerprint"]) != plan.fingerprint or str(row["canonical_json"]) != canonical:
                    raise DualHoldoutAcquisitionGovernanceError("D3D plan_id already binds different acquisition geometry")
                return
            connection.execute(
                f"INSERT INTO {OSS3D3D_REGISTRY_TABLE} (plan_id, fingerprint, canonical_json, preregistered_at) VALUES (?, ?, ?, ?)",
                (plan.plan_id, plan.fingerprint, canonical, timestamp),
            )

    def require_exact(self, plan: DualHoldoutAcquisitionPlan) -> None:
        canonical = _canonical_json(plan.to_dict())
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT fingerprint, canonical_json FROM {OSS3D3D_REGISTRY_TABLE} WHERE plan_id = ?",
                (plan.plan_id,),
            ).fetchone()
        if row is None:
            raise DualHoldoutAcquisitionGovernanceError("durable D3D preregistration is required before acquisition")
        if str(row["fingerprint"]) != plan.fingerprint or str(row["canonical_json"]) != canonical:
            raise DualHoldoutAcquisitionIntegrityError("durable D3D plan differs from supplied plan")


def canonical_oss3d3d_dual_holdout_plan() -> DualHoldoutAcquisitionPlan:
    """Build the exact value-opaque Q1/Q2 2026 holdout reservation."""
    d2u = canonical_oss3d2u_collection_plan()
    instruments = {
        symbol: next(
            descriptor.instrument
            for descriptor in d2u.descriptors
            if descriptor.instrument.symbol == symbol
        )
        for symbol in CANONICAL_SYMBOLS
    }

    def window(*, purpose: str, months: tuple[str, ...], start: str, end: str, bars: int) -> ReservedHoldoutWindow:
        descriptors = tuple(
            BinanceSpotArchiveDescriptor.monthly(
                instrument=instruments[symbol],
                interval=CANONICAL_INTERVAL,
                month=month,
            )
            for month in months
            for symbol in CANONICAL_SYMBOLS
        )
        return ReservedHoldoutWindow(
            window_version=OSS3D3D_WINDOW_VERSION,
            purpose=purpose,
            partition_start=start,
            partition_end=end,
            months=months,
            descriptors=descriptors,
            expected_bars_per_symbol=bars,
        )

    predictive = window(
        purpose=PREDICTIVE_PURPOSE,
        months=PREDICTIVE_MONTHS,
        start=PREDICTIVE_START,
        end=PREDICTIVE_END,
        bars=EXPECTED_PREDICTIVE_BARS_PER_SYMBOL,
    )
    economic = window(
        purpose=ECONOMIC_PURPOSE,
        months=ECONOMIC_MONTHS,
        start=ECONOMIC_START,
        end=ECONOMIC_END,
        bars=EXPECTED_ECONOMIC_BARS_PER_SYMBOL,
    )
    return DualHoldoutAcquisitionPlan(
        plan_version=OSS3D3D_PLAN_VERSION,
        plan_id=PLAN_ID,
        source_d3c_certified_head=SOURCE_D3C_CERTIFIED_HEAD,
        source_d3c_artifact_hash=SOURCE_D3C_ARTIFACT_HASH,
        source_d3c_scientific_outcome_hash=SOURCE_D3C_SCIENTIFIC_OUTCOME_HASH,
        source_d3a_certified_baseline_hash=SOURCE_D3A_CERTIFIED_BASELINE_HASH,
        source_d2u_plan_fingerprint=d2u.fingerprint,
        development_end=CANONICAL_DEVELOPMENT_END.isoformat(),
        provider_id=PROVIDER_ID,
        symbols=CANONICAL_SYMBOLS,
        interval=CANONICAL_INTERVAL,
        granularity="monthly",
        predictive=predictive,
        economic=economic,
        window_policy=WINDOW_POLICY,
        acquisition_policy=ACQUISITION_POLICY,
        separation_policy=SEPARATION_POLICY,
        untouched_future_start=UNTOUCHED_FUTURE_START,
        post_reservation_policy=POST_RESERVATION_POLICY,
        predictive_d2j_sample_adequacy_preregistered=True,
        future_network_acquisition_scope_frozen=True,
        network_acquisition_performed=False,
        final_holdout_observed=False,
        economic_holdout_observed=False,
        holdout_permit_issued=False,
        holdout_permit_consumed=False,
        execution_authorized=False,
        paper_execution_authorized=False,
        capital_authority="NONE",
        live_trading="BLOCKED",
    )


def write_dual_holdout_plan(plan: DualHoldoutAcquisitionPlan, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = plan.to_dict()
    payload["fingerprint"] = plan.fingerprint
    raw = _canonical_json(payload) + "\n"
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(raw, encoding="utf-8")
    temporary.replace(target)


def _parse_utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DualHoldoutAcquisitionIntegrityError(f"invalid D3D {name}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DualHoldoutAcquisitionIntegrityError(f"D3D {name} must be timezone-aware")
    normalized = parsed.astimezone(timezone.utc)
    if parsed != normalized or value != normalized.isoformat():
        raise DualHoldoutAcquisitionIntegrityError(f"D3D {name} must be canonical UTC")
    return normalized


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase sha256")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()
