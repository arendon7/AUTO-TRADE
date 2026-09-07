"""OSS-3D2N pre-D2K economic prediction commitment.

This layer closes a governance gap between D2M and D2N.  D2M freezes the
market/economic protocol but intentionally does not contain prediction values.
D2N later consumes a QlibPredictionArtifact.  Without an additional durable
commitment, a prediction artifact with matching model metadata could be
substituted after predictive holdout results were known.

The precommit therefore freezes the exact prediction artifact and payload in
the same authoritative SQLite file after D2M, but before any D2K start.  It
never receives market bars or economic outcomes and never reads row scores
except indirectly through the artifact's canonical hashes.  A SQLite trigger
then requires every D2N start to use the exact precommitted prediction hash.

Research only: no broker, OMS, Safety, OrderIntent, network, PAPER, capital or
LIVE authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from autotrade.research.oss3_qlib_artifact import QlibPredictionArtifact

from .economic_holdout_evaluator import SQLiteOSS3EconomicHoldoutEvaluationRegistry
from .final_holdout_protocol import OSS3FinalHoldoutProtocolReceipt
from .predictive_economic_protocol import OSS3PredictiveEconomicProtocolReceipt
from .predictive_strategy_contract import OSS3PredictiveStrategyPreregistrationReceipt


OSS3D2N_PREDICTION_PRECOMMIT_VERSION = "OSS3D2N_ECONOMIC_PREDICTION_PRECOMMIT_V1"
OSS3D2N_PREDICTION_ORDERING_CONTRACT = "OSS3D2N_D2M_PREDICTION_D2K_SHARED_SQLITE_ORDERING_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")


class EconomicPredictionPrecommitError(RuntimeError):
    pass


class EconomicPredictionPrecommitIntegrityError(EconomicPredictionPrecommitError):
    pass


class EconomicPredictionPrecommitGovernanceError(EconomicPredictionPrecommitError):
    pass


class EconomicPredictionPrecommitConflict(EconomicPredictionPrecommitError):
    pass


@dataclass(frozen=True, slots=True)
class OSS3EconomicPredictionPrecommitReceipt:
    receipt_version: str
    ordering_contract: str
    precommit_id: str
    economic_protocol_id: str
    economic_protocol_receipt_hash: str
    source_d2l_receipt_hash: str
    source_d2l_binding_hash: str
    source_strategy_semantic_hash: str
    source_d2j_protocol_id: str
    source_d2j_protocol_receipt_hash: str
    model_family: str
    model_config_hash: str
    qlib_version: str
    training_dataset_hash: str
    feature_schema_hash: str
    producer_code_hash: str
    prediction_artifact_hash: str
    prediction_payload_hash: str
    prediction_support_hash: str
    registered_at: str
    frozen_before_d2k_start: bool
    prediction_values_frozen: bool
    economic_market_values_used: bool
    economic_outcomes_used: bool
    execution_authorized: bool
    paper_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_version != OSS3D2N_PREDICTION_PRECOMMIT_VERSION:
            raise EconomicPredictionPrecommitIntegrityError("noncanonical prediction precommit version")
        if self.ordering_contract != OSS3D2N_PREDICTION_ORDERING_CONTRACT:
            raise EconomicPredictionPrecommitIntegrityError("prediction precommit ordering contract drifted")
        for name in ("precommit_id", "economic_protocol_id", "source_d2j_protocol_id"):
            _require_id(getattr(self, name), name)
        if not self.model_family or not self.qlib_version:
            raise EconomicPredictionPrecommitIntegrityError("model family/version are required")
        for name in (
            "economic_protocol_receipt_hash",
            "source_d2l_receipt_hash",
            "source_d2l_binding_hash",
            "source_strategy_semantic_hash",
            "source_d2j_protocol_receipt_hash",
            "model_config_hash",
            "training_dataset_hash",
            "feature_schema_hash",
            "producer_code_hash",
            "prediction_artifact_hash",
            "prediction_payload_hash",
            "prediction_support_hash",
            "receipt_hash",
        ):
            _require_hash(getattr(self, name), name)
        _parse_utc(self.registered_at, "registered_at")
        if self.frozen_before_d2k_start is not True or self.prediction_values_frozen is not True:
            raise EconomicPredictionPrecommitGovernanceError("prediction must be frozen before D2K")
        if self.economic_market_values_used or self.economic_outcomes_used:
            raise EconomicPredictionPrecommitGovernanceError(
                "prediction precommit may not use economic market values/outcomes"
            )
        if self.execution_authorized or self.paper_execution_authorized:
            raise EconomicPredictionPrecommitGovernanceError("prediction precommit cannot authorize execution")
        if self.capital_authority != "NONE" or self.live_trading != "BLOCKED":
            raise EconomicPredictionPrecommitGovernanceError("prediction precommit cannot grant capital/LIVE")
        if self.receipt_hash != _hash(self.to_dict(include_hash=False)):
            raise EconomicPredictionPrecommitIntegrityError("prediction precommit receipt hash mismatch")

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "receipt_version": self.receipt_version,
            "ordering_contract": self.ordering_contract,
            "precommit_id": self.precommit_id,
            "economic_protocol_id": self.economic_protocol_id,
            "economic_protocol_receipt_hash": self.economic_protocol_receipt_hash,
            "source_d2l_receipt_hash": self.source_d2l_receipt_hash,
            "source_d2l_binding_hash": self.source_d2l_binding_hash,
            "source_strategy_semantic_hash": self.source_strategy_semantic_hash,
            "source_d2j_protocol_id": self.source_d2j_protocol_id,
            "source_d2j_protocol_receipt_hash": self.source_d2j_protocol_receipt_hash,
            "model_family": self.model_family,
            "model_config_hash": self.model_config_hash,
            "qlib_version": self.qlib_version,
            "training_dataset_hash": self.training_dataset_hash,
            "feature_schema_hash": self.feature_schema_hash,
            "producer_code_hash": self.producer_code_hash,
            "prediction_artifact_hash": self.prediction_artifact_hash,
            "prediction_payload_hash": self.prediction_payload_hash,
            "prediction_support_hash": self.prediction_support_hash,
            "registered_at": self.registered_at,
            "frozen_before_d2k_start": self.frozen_before_d2k_start,
            "prediction_values_frozen": self.prediction_values_frozen,
            "economic_market_values_used": self.economic_market_values_used,
            "economic_outcomes_used": self.economic_outcomes_used,
            "execution_authorized": self.execution_authorized,
            "paper_execution_authorized": self.paper_execution_authorized,
            "capital_authority": self.capital_authority,
            "live_trading": self.live_trading,
        }
        if include_hash:
            payload["receipt_hash"] = self.receipt_hash
        return payload


class SQLiteOSS3EconomicPredictionPrecommitRegistry:
    """Append-only prediction commitment with a DB-enforced D2N admission trigger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # Create the canonical D2N start table before installing the trigger.
        SQLiteOSS3EconomicHoldoutEvaluationRegistry(self.path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS oss3_economic_prediction_precommits (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    precommit_id TEXT NOT NULL UNIQUE,
                    economic_protocol_id TEXT NOT NULL UNIQUE,
                    economic_protocol_receipt_hash TEXT NOT NULL UNIQUE,
                    source_d2l_receipt_hash TEXT NOT NULL,
                    source_d2l_binding_hash TEXT NOT NULL,
                    source_strategy_semantic_hash TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_id TEXT NOT NULL UNIQUE,
                    source_d2j_protocol_receipt_hash TEXT NOT NULL,
                    model_config_hash TEXT NOT NULL,
                    prediction_artifact_hash TEXT NOT NULL UNIQUE,
                    prediction_payload_hash TEXT NOT NULL UNIQUE,
                    prediction_support_hash TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS oss3_economic_prediction_precommits_no_update
                BEFORE UPDATE ON oss3_economic_prediction_precommits
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2N prediction precommit registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_economic_prediction_precommits_no_delete
                BEFORE DELETE ON oss3_economic_prediction_precommits
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2N prediction precommit registry is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS oss3_d2n_start_requires_prediction_precommit
                BEFORE INSERT ON oss3_economic_holdout_evaluation_starts
                WHEN NOT EXISTS (
                    SELECT 1
                    FROM oss3_economic_prediction_precommits p
                    WHERE p.economic_protocol_id = NEW.economic_protocol_id
                      AND p.economic_protocol_receipt_hash = NEW.economic_protocol_receipt_hash
                      AND p.source_d2l_receipt_hash = NEW.source_d2l_receipt_hash
                      AND p.source_strategy_semantic_hash = NEW.source_strategy_semantic_hash
                      AND p.source_d2j_protocol_id = NEW.source_d2j_protocol_id
                      AND p.source_d2j_protocol_receipt_hash = NEW.source_d2j_protocol_receipt_hash
                      AND p.prediction_artifact_hash = NEW.economic_prediction_artifact_hash
                )
                BEGIN
                    SELECT RAISE(ABORT, 'OSS-3D2N exact economic prediction precommit required');
                END;
                """
            )
            conn.commit()
        finally:
            conn.close()

    def preregister(
        self,
        *,
        precommit_id: str,
        economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
        d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
        d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
        prediction: QlibPredictionArtifact,
        now: datetime,
    ) -> OSS3EconomicPredictionPrecommitReceipt:
        _require_id(precommit_id, "precommit_id")
        _require_aware(now, "now")
        if not isinstance(economic_protocol, OSS3PredictiveEconomicProtocolReceipt):
            raise TypeError("economic_protocol must be OSS3PredictiveEconomicProtocolReceipt")
        if not isinstance(d2l_receipt, OSS3PredictiveStrategyPreregistrationReceipt):
            raise TypeError("d2l_receipt must be OSS3PredictiveStrategyPreregistrationReceipt")
        if not isinstance(d2j_protocol, OSS3FinalHoldoutProtocolReceipt):
            raise TypeError("d2j_protocol must be OSS3FinalHoldoutProtocolReceipt")
        if not isinstance(prediction, QlibPredictionArtifact):
            raise TypeError("prediction must be QlibPredictionArtifact")
        _verify_chain(
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
        )
        support_hash = _verify_prediction_identity_and_support(
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            prediction=prediction,
        )
        candidate = _build_receipt(
            precommit_id=precommit_id,
            economic_protocol=economic_protocol,
            d2l_receipt=d2l_receipt,
            d2j_protocol=d2j_protocol,
            prediction=prediction,
            support_hash=support_hash,
            registered_at=now,
        )

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            _require_exact_durable_inputs(
                conn=conn,
                economic_protocol=economic_protocol,
                d2l_receipt=d2l_receipt,
            )
            _require_no_d2k_state(conn=conn, d2j_protocol=d2j_protocol)
            existing = conn.execute(
                "SELECT receipt_json FROM oss3_economic_prediction_precommits "
                "WHERE economic_protocol_id = ?",
                (economic_protocol.economic_protocol_id,),
            ).fetchone()
            if existing is not None:
                current = _receipt_from_row(existing)
                if current != candidate:
                    raise EconomicPredictionPrecommitConflict(
                        "economic protocol already has another prediction precommit"
                    )
                conn.execute("COMMIT")
                return current
            conn.execute(
                """
                INSERT INTO oss3_economic_prediction_precommits(
                    precommit_id, economic_protocol_id, economic_protocol_receipt_hash,
                    source_d2l_receipt_hash, source_d2l_binding_hash,
                    source_strategy_semantic_hash, source_d2j_protocol_id,
                    source_d2j_protocol_receipt_hash, model_config_hash,
                    prediction_artifact_hash, prediction_payload_hash,
                    prediction_support_hash, registered_at, receipt_hash, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.precommit_id,
                    candidate.economic_protocol_id,
                    candidate.economic_protocol_receipt_hash,
                    candidate.source_d2l_receipt_hash,
                    candidate.source_d2l_binding_hash,
                    candidate.source_strategy_semantic_hash,
                    candidate.source_d2j_protocol_id,
                    candidate.source_d2j_protocol_receipt_hash,
                    candidate.model_config_hash,
                    candidate.prediction_artifact_hash,
                    candidate.prediction_payload_hash,
                    candidate.prediction_support_hash,
                    candidate.registered_at,
                    candidate.receipt_hash,
                    _canonical_json(candidate.to_dict()),
                ),
            )
            conn.execute("COMMIT")
            return candidate
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise EconomicPredictionPrecommitConflict(
                "prediction precommit durable identity conflict"
            ) from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def read_oss3d2n_prediction_precommit_read_only(
    path: str | Path,
    *,
    economic_protocol_id: str,
) -> OSS3EconomicPredictionPrecommitReceipt | None:
    _require_id(economic_protocol_id, "economic_protocol_id")
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise EconomicPredictionPrecommitIntegrityError("prediction precommit registry does not exist")
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        if not _table_exists(conn, "oss3_economic_prediction_precommits"):
            return None
        row = conn.execute(
            "SELECT receipt_json FROM oss3_economic_prediction_precommits "
            "WHERE economic_protocol_id = ?",
            (economic_protocol_id,),
        ).fetchone()
        return _receipt_from_row(row) if row is not None else None
    finally:
        conn.close()


def _verify_chain(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    binding = d2l_receipt.binding
    if economic_protocol.source_d2l_receipt_hash != d2l_receipt.receipt_hash:
        raise EconomicPredictionPrecommitIntegrityError("D2M/D2L receipt mismatch")
    if economic_protocol.source_d2l_binding_hash != binding.binding_hash:
        raise EconomicPredictionPrecommitIntegrityError("D2M/D2L binding mismatch")
    if economic_protocol.source_strategy_semantic_hash != binding.strategy_semantic_hash:
        raise EconomicPredictionPrecommitIntegrityError("D2M strategy semantic mismatch")
    if economic_protocol.source_d2j_protocol_id != d2j_protocol.protocol_id:
        raise EconomicPredictionPrecommitIntegrityError("D2M/D2J protocol mismatch")
    if economic_protocol.source_d2j_protocol_receipt_hash != d2j_protocol.receipt_hash:
        raise EconomicPredictionPrecommitIntegrityError("D2M/D2J receipt mismatch")
    if d2l_receipt.protocol_id != d2j_protocol.protocol_id:
        raise EconomicPredictionPrecommitIntegrityError("D2L/D2J protocol mismatch")
    if d2l_receipt.protocol_receipt_hash != d2j_protocol.receipt_hash:
        raise EconomicPredictionPrecommitIntegrityError("D2L/D2J receipt hash mismatch")


def _verify_prediction_identity_and_support(
    *,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    prediction: QlibPredictionArtifact,
) -> str:
    binding = d2l_receipt.binding
    manifest = prediction.manifest
    expected = {
        "model_family": binding.model_family,
        "model_config_hash": binding.model_config_hash,
        "qlib_version": binding.qlib_version,
        "training_dataset_hash": binding.training_dataset_hash,
        "feature_schema_hash": binding.feature_schema_hash,
        "producer_code_hash": binding.shared_runner_code_hash,
    }
    for name, value in expected.items():
        if getattr(manifest, name) != value:
            raise EconomicPredictionPrecommitIntegrityError(
                f"economic prediction {name} differs from frozen D2L model identity"
            )

    commitment = economic_protocol.economic_holdout_commitment
    start = _parse_utc(commitment.partition_start, "economic partition_start")
    end = _parse_utc(commitment.partition_end, "economic partition_end")
    step = timedelta(seconds=commitment.timeframe_seconds)
    if start + step * commitment.bar_count != end:
        raise EconomicPredictionPrecommitIntegrityError(
            "economic commitment window/bar_count/timeframe are inconsistent"
        )
    expected_timestamps = tuple(
        (start + step * index).isoformat()
        for index in range(1, commitment.bar_count)
    )
    expected_keys = tuple(
        (timestamp, symbol)
        for timestamp in expected_timestamps
        for symbol in commitment.symbols
    )
    # Deliberately inspect only timestamp/symbol support here; row.score is not
    # used to choose policy or gates. Score values are frozen only through the
    # canonical artifact/payload hashes below.
    observed_keys = tuple((row.timestamp, row.symbol) for row in prediction.rows)
    if observed_keys != expected_keys:
        raise EconomicPredictionPrecommitIntegrityError(
            "economic prediction support differs from committed holdout clock/universe"
        )
    if manifest.inference_start != expected_timestamps[0] or manifest.inference_end != end.isoformat():
        raise EconomicPredictionPrecommitIntegrityError(
            "economic prediction inference window differs from committed holdout"
        )
    return _hash([[timestamp, symbol] for timestamp, symbol in observed_keys])


def _build_receipt(
    *,
    precommit_id: str,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
    prediction: QlibPredictionArtifact,
    support_hash: str,
    registered_at: datetime,
) -> OSS3EconomicPredictionPrecommitReceipt:
    binding = d2l_receipt.binding
    manifest = prediction.manifest
    values = {
        "receipt_version": OSS3D2N_PREDICTION_PRECOMMIT_VERSION,
        "ordering_contract": OSS3D2N_PREDICTION_ORDERING_CONTRACT,
        "precommit_id": precommit_id,
        "economic_protocol_id": economic_protocol.economic_protocol_id,
        "economic_protocol_receipt_hash": economic_protocol.receipt_hash,
        "source_d2l_receipt_hash": d2l_receipt.receipt_hash,
        "source_d2l_binding_hash": binding.binding_hash,
        "source_strategy_semantic_hash": binding.strategy_semantic_hash,
        "source_d2j_protocol_id": d2j_protocol.protocol_id,
        "source_d2j_protocol_receipt_hash": d2j_protocol.receipt_hash,
        "model_family": binding.model_family,
        "model_config_hash": binding.model_config_hash,
        "qlib_version": binding.qlib_version,
        "training_dataset_hash": binding.training_dataset_hash,
        "feature_schema_hash": binding.feature_schema_hash,
        "producer_code_hash": binding.shared_runner_code_hash,
        "prediction_artifact_hash": prediction.artifact_hash,
        "prediction_payload_hash": manifest.prediction_payload_hash,
        "prediction_support_hash": support_hash,
        "registered_at": _utc_iso(registered_at),
        "frozen_before_d2k_start": True,
        "prediction_values_frozen": True,
        "economic_market_values_used": False,
        "economic_outcomes_used": False,
        "execution_authorized": False,
        "paper_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return OSS3EconomicPredictionPrecommitReceipt(
        **values,
        receipt_hash=_hash(_receipt_payload_from_values(values)),
    )


def _require_exact_durable_inputs(
    *,
    conn: sqlite3.Connection,
    economic_protocol: OSS3PredictiveEconomicProtocolReceipt,
    d2l_receipt: OSS3PredictiveStrategyPreregistrationReceipt,
) -> None:
    if not _table_exists(conn, "oss3_predictive_economic_protocols"):
        raise EconomicPredictionPrecommitGovernanceError("D2M durable registry is missing")
    d2m = conn.execute(
        "SELECT receipt_hash FROM oss3_predictive_economic_protocols WHERE economic_protocol_id = ?",
        (economic_protocol.economic_protocol_id,),
    ).fetchone()
    if d2m is None or str(d2m["receipt_hash"]) != economic_protocol.receipt_hash:
        raise EconomicPredictionPrecommitIntegrityError("exact durable D2M receipt not proven")
    if not _table_exists(conn, "oss3_predictive_strategy_preregistrations"):
        raise EconomicPredictionPrecommitGovernanceError("D2L durable registry is missing")
    d2l = conn.execute(
        "SELECT receipt_hash FROM oss3_predictive_strategy_preregistrations WHERE protocol_id = ?",
        (d2l_receipt.protocol_id,),
    ).fetchone()
    if d2l is None or str(d2l["receipt_hash"]) != d2l_receipt.receipt_hash:
        raise EconomicPredictionPrecommitIntegrityError("exact durable D2L receipt not proven")


def _require_no_d2k_state(
    *,
    conn: sqlite3.Connection,
    d2j_protocol: OSS3FinalHoldoutProtocolReceipt,
) -> None:
    if _table_exists(conn, "oss3_final_holdout_evaluation_starts"):
        row = conn.execute(
            "SELECT start_hash FROM oss3_final_holdout_evaluation_starts WHERE protocol_id = ?",
            (d2j_protocol.protocol_id,),
        ).fetchone()
        if row is not None:
            raise EconomicPredictionPrecommitGovernanceError(
                "economic prediction must be precommitted before D2K start"
            )
    if _table_exists(conn, "holdout_permits"):
        row = conn.execute(
            "SELECT used_at FROM holdout_permits WHERE permit_id = ?",
            (d2j_protocol.expected_holdout_authorization_id,),
        ).fetchone()
        if row is not None and row["used_at"] is not None:
            raise EconomicPredictionPrecommitGovernanceError(
                "economic prediction precommit cannot follow holdout permit consumption"
            )


def _receipt_from_row(row: sqlite3.Row) -> OSS3EconomicPredictionPrecommitReceipt:
    payload = json.loads(str(row["receipt_json"]))
    return OSS3EconomicPredictionPrecommitReceipt(
        receipt_version=str(payload["receipt_version"]),
        ordering_contract=str(payload["ordering_contract"]),
        precommit_id=str(payload["precommit_id"]),
        economic_protocol_id=str(payload["economic_protocol_id"]),
        economic_protocol_receipt_hash=str(payload["economic_protocol_receipt_hash"]),
        source_d2l_receipt_hash=str(payload["source_d2l_receipt_hash"]),
        source_d2l_binding_hash=str(payload["source_d2l_binding_hash"]),
        source_strategy_semantic_hash=str(payload["source_strategy_semantic_hash"]),
        source_d2j_protocol_id=str(payload["source_d2j_protocol_id"]),
        source_d2j_protocol_receipt_hash=str(payload["source_d2j_protocol_receipt_hash"]),
        model_family=str(payload["model_family"]),
        model_config_hash=str(payload["model_config_hash"]),
        qlib_version=str(payload["qlib_version"]),
        training_dataset_hash=str(payload["training_dataset_hash"]),
        feature_schema_hash=str(payload["feature_schema_hash"]),
        producer_code_hash=str(payload["producer_code_hash"]),
        prediction_artifact_hash=str(payload["prediction_artifact_hash"]),
        prediction_payload_hash=str(payload["prediction_payload_hash"]),
        prediction_support_hash=str(payload["prediction_support_hash"]),
        registered_at=str(payload["registered_at"]),
        frozen_before_d2k_start=bool(payload["frozen_before_d2k_start"]),
        prediction_values_frozen=bool(payload["prediction_values_frozen"]),
        economic_market_values_used=bool(payload["economic_market_values_used"]),
        economic_outcomes_used=bool(payload["economic_outcomes_used"]),
        execution_authorized=bool(payload["execution_authorized"]),
        paper_execution_authorized=bool(payload["paper_execution_authorized"]),
        capital_authority=str(payload["capital_authority"]),
        live_trading=str(payload["live_trading"]),
        receipt_hash=str(payload["receipt_hash"]),
    )


def _receipt_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    return dict(values)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _require_id(value: object, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_hash(value: object, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"invalid {name}")


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _parse_utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name}") from exc
    _require_aware(parsed, name)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
