from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Mapping

from autotrade.brokers.alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from autotrade.brokers.alpaca_paper_connectivity_prepare import _read_account
from autotrade.brokers.alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from autotrade.brokers.alpaca_paper_gateway import AlpacaPaperCredentials
from autotrade.brokers.alpaca_paper_market_evidence import PaperMarketEvidenceStore
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _file_sha256,
    _read_json_object,
    _write_json_idempotent,
)
from autotrade.connectivity_execution_freshness_binding import (
    ConnectivityBoundFinalFreshnessGuard,
    ConnectivityBoundFinalFreshnessResult,
)
from autotrade.connectivity_execution_intent import (
    ConnectivityExecutionIntentBridge,
    ConnectivityExecutionIntentContext,
    ConnectivityExecutionIntentState,
    SQLiteConnectivityExecutionIntentRegistry,
)
from autotrade.connectivity_operator_decision import (
    ConnectivityOperatorBridge,
    ConnectivityOperatorDecisionStatus,
    SQLiteConnectivityOperatorDecisionRegistry,
)
from autotrade.domain import market_fingerprint
from autotrade.persistence import SQLiteRuntime

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_RECEIPT = "connectivity_operator_review_receipt.json"
_REVIEW_DB = "connectivity_execution_review_binding.sqlite3"
_REVIEW_BINDING = "connectivity_execution_review_binding.json"
_REVIEW_FRESHNESS = "connectivity_review_final_freshness_binding.json"
_OPERATOR_DB = "connectivity_operator.sqlite3"
_OPERATOR_ARTIFACT = "connectivity_operator_decision.json"
_EXECUTION_INTENT_DB = "connectivity_execution_intent.sqlite3"
_EXECUTION_INTENT_ARTIFACT = "connectivity_execution_intent.json"
_PREPARATION = "connectivity_preparation.json"
_ASSET = "asset_attestation.json"
_FLAT = "flat_account_attestation.json"
_MARKET = "market_snapshot.json"


class ConnectivityOperatorReviewError(RuntimeError):
    pass


class ConnectivityOperatorReviewRejected(ConnectivityOperatorReviewError):
    pass


class ConnectivityOperatorReviewConflict(ConnectivityOperatorReviewError):
    pass


@dataclass(frozen=True, slots=True)
class ConnectivityOperatorReviewReceipt:
    body: Mapping[str, object]
    receipt_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, Mapping):
            raise TypeError("review receipt body must be mapping")
        _validate_receipt_body(self.body)
        _validate_hash(self.receipt_hash, "receipt_hash")
        if self.receipt_hash != _hash(dict(self.body)):
            raise ValueError("operator review receipt hash mismatch")

    @property
    def order_id(self) -> str:
        return _required_str(self.body, "order_id")

    @property
    def client_order_id(self) -> str:
        return _required_str(self.body, "client_order_id")

    @property
    def attempt_id(self) -> str:
        return _required_str(self.body, "attempt_id")

    def document(self) -> dict[str, object]:
        return {**dict(self.body), "receipt_hash": self.receipt_hash}


@dataclass(frozen=True, slots=True)
class ConnectivityExecutionReviewBinding:
    order_id: str
    client_order_id: str
    attempt_id: str
    receipt_hash: str
    receipt_artifact_sha256: str
    execution_intent_context_hash: str
    execution_intent_decision_hash: str
    execution_intent_event_hash: str
    execution_intent_artifact_sha256: str
    operator_id: str
    bound_at: datetime
    binding_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("attempt_id", self.attempt_id),
            ("operator_id", self.operator_id),
        ):
            _validate_id(value, label)
        for label, value in (
            ("receipt_hash", self.receipt_hash),
            ("receipt_artifact_sha256", self.receipt_artifact_sha256),
            ("execution_intent_context_hash", self.execution_intent_context_hash),
            ("execution_intent_decision_hash", self.execution_intent_decision_hash),
            ("execution_intent_event_hash", self.execution_intent_event_hash),
            ("execution_intent_artifact_sha256", self.execution_intent_artifact_sha256),
            ("binding_hash", self.binding_hash),
        ):
            _validate_hash(value, label)
        _require_aware(self.bound_at, "bound_at")
        if self.binding_hash != _hash(_review_binding_body(self)):
            raise ValueError("execution review binding hash mismatch")

    def payload(self) -> dict[str, object]:
        return {**_review_binding_body(self), "binding_hash": self.binding_hash}


class SQLiteConnectivityExecutionReviewBindingStore:
    """One immutable receipt->human-intent binding. No network or execution API."""

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS connectivity_execution_review_binding (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    payload_json TEXT NOT NULL,
                    binding_hash TEXT NOT NULL UNIQUE
                )
                """
            )
        finally:
            conn.close()

    def record(self, binding: ConnectivityExecutionReviewBinding) -> ConnectivityExecutionReviewBinding:
        if not isinstance(binding, ConnectivityExecutionReviewBinding):
            raise TypeError("ConnectivityExecutionReviewBinding is required")
        payload_json = _canonical(binding.payload())
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload_json,binding_hash FROM connectivity_execution_review_binding WHERE singleton=1"
            ).fetchone()
            if row is not None:
                existing = _review_binding_from_payload(_json_object(str(row["payload_json"])))
                if str(row["binding_hash"]) != existing.binding_hash:
                    raise ConnectivityOperatorReviewConflict("review binding row hash mismatch")
                if existing != binding:
                    raise ConnectivityOperatorReviewConflict("workspace already contains different review binding")
                conn.execute("COMMIT")
                return existing
            conn.execute(
                "INSERT INTO connectivity_execution_review_binding(singleton,payload_json,binding_hash) VALUES(1,?,?)",
                (payload_json, binding.binding_hash),
            )
            conn.execute("COMMIT")
            return binding
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self) -> ConnectivityExecutionReviewBinding:
        conn = self._runtime.connect()
        try:
            rows = conn.execute(
                "SELECT payload_json,binding_hash FROM connectivity_execution_review_binding"
            ).fetchall()
            if len(rows) != 1:
                raise ConnectivityOperatorReviewRejected("exactly one execution review binding is required")
            binding = _review_binding_from_payload(_json_object(str(rows[0]["payload_json"])))
            if str(rows[0]["binding_hash"]) != binding.binding_hash:
                raise ConnectivityOperatorReviewConflict("review binding durable hash mismatch")
            return binding
        finally:
            conn.close()


class ConnectivityOperatorReviewReceiptBuilder:
    """Credential-free, network-free review document for the exact prepared canary."""

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    @property
    def path(self) -> Path:
        return self._workspace.root / _RECEIPT

    def build(self, *, now: datetime) -> ConnectivityOperatorReviewReceipt:
        _require_aware(now, "now")
        for name in (
            _EXECUTION_INTENT_DB,
            _EXECUTION_INTENT_ARTIFACT,
            "connectivity_final_freshness.sqlite3",
            "connectivity_final_freshness.json",
            "connectivity_execution_freshness_binding.sqlite3",
            "connectivity_execution_freshness_binding.json",
            "connectivity_staging.json",
            "connectivity_post_observation.json",
            "connectivity_post_ambiguity.json",
        ):
            if (self._workspace.root / name).exists():
                raise ConnectivityOperatorReviewRejected(
                    "operator review receipt must be frozen before second execution intent/freshness/staging"
                )

        operator_context = ConnectivityOperatorBridge(self._workspace).prepare_context(now=now)
        state = _load_operator_state(self._workspace)
        if state.status is not ConnectivityOperatorDecisionStatus.ISSUED:
            raise ConnectivityOperatorReviewRejected("first operator decision is not ISSUED")
        if not state.decision.is_valid_at(now):
            raise ConnectivityOperatorReviewRejected("first operator decision expired before review receipt")
        if state.decision.context != operator_context:
            raise ConnectivityOperatorReviewConflict("operator registry/context changed before review")

        preparation = _read_json_object(self._workspace.root / _PREPARATION)
        preparation_hash = _required_hash(preparation, "preparation_hash")
        unsigned_preparation = dict(preparation)
        unsigned_preparation.pop("preparation_hash", None)
        if preparation_hash != _hash(unsigned_preparation):
            raise ConnectivityOperatorReviewConflict("connectivity preparation hash mismatch")
        if preparation_hash != operator_context.connectivity_preparation_hash:
            raise ConnectivityOperatorReviewConflict("operator decision/preparation binding mismatch")

        package = _mapping(preparation, "standard_prepared_package")
        bracket = _mapping(preparation, "expected_bracket")
        bracket_hash = _hash(bracket)
        if bracket_hash != _required_hash(preparation, "expected_bracket_payload_hash"):
            raise ConnectivityOperatorReviewConflict("review bracket payload hash mismatch")
        if bracket_hash != operator_context.bracket_payload_hash:
            raise ConnectivityOperatorReviewConflict("operator context/bracket hash mismatch")

        account = _read_account(self._workspace)
        asset = PaperAssetEvidenceStore(self._workspace).read()
        flat = PaperFlatAccountEvidenceStore(self._workspace).read()
        market_attestation = PaperMarketEvidenceStore(self._workspace).read()
        market = market_attestation.market

        if account.fingerprint != operator_context.account_attestation_fingerprint:
            raise ConnectivityOperatorReviewConflict("review account fingerprint changed")
        if _required_str(package, "account_attestation_fingerprint") != account.fingerprint:
            raise ConnectivityOperatorReviewConflict("prepared package/account binding changed")
        if _required_str(package, "market_fingerprint") != market_fingerprint(market):
            raise ConnectivityOperatorReviewConflict("prepared package/market binding changed")
        if not flat.clean_for_first_canary or flat.position_count != 0 or flat.open_order_count != 0:
            raise ConnectivityOperatorReviewRejected("review requires exact flat account 0 positions / 0 open orders")
        if asset.symbol != market.symbol or asset.symbol != _required_str(bracket, "symbol"):
            raise ConnectivityOperatorReviewConflict("review symbol/evidence mismatch")
        if _required_str(bracket, "client_order_id") != operator_context.client_order_id:
            raise ConnectivityOperatorReviewConflict("review client_order_id mismatch")
        if _required_str(package, "order_id") != operator_context.order_id:
            raise ConnectivityOperatorReviewConflict("review order_id mismatch")
        if _required_str(package, "attempt_id") != operator_context.attempt_id:
            raise ConnectivityOperatorReviewConflict("review attempt_id mismatch")
        if _required_hash(package, "package_hash") != operator_context.standard_package_hash:
            raise ConnectivityOperatorReviewConflict("review package hash mismatch")

        take_profit = _mapping(bracket, "take_profit")
        stop_loss = _mapping(bracket, "stop_loss")
        notional = _decimal(package.get("notional"), "notional")
        cap = _decimal(package.get("effective_notional_cap"), "effective_notional_cap")
        if notional != operator_context.notional or notional > cap:
            raise ConnectivityOperatorReviewRejected("review notional/cap binding is invalid")

        body: dict[str, object] = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "review_type": "PRE_FINAL_FRESHNESS_HUMAN_ORDER_REVIEW",
            "order_id": operator_context.order_id,
            "client_order_id": operator_context.client_order_id,
            "attempt_id": operator_context.attempt_id,
            "operator_context_hash": operator_context.context_hash,
            "operator_decision_hash": state.decision.decision_hash,
            "operator_event_hash": state.event_hash,
            "preparation_hash": preparation_hash,
            "standard_package_hash": operator_context.standard_package_hash,
            "bracket_payload_hash": bracket_hash,
            "account_attestation_fingerprint": account.fingerprint,
            "asset_attestation_fingerprint": asset.fingerprint,
            "flat_account_attestation_fingerprint": flat.fingerprint,
            "market_attestation_fingerprint": market_attestation.fingerprint,
            "account_artifact_sha256": _file_sha256(self._workspace.account_attestation_path),
            "asset_artifact_sha256": _file_sha256(self._workspace.root / _ASSET),
            "flat_account_artifact_sha256": _file_sha256(self._workspace.root / _FLAT),
            "market_artifact_sha256": _file_sha256(self._workspace.root / _MARKET),
            "symbol": _required_str(bracket, "symbol"),
            "side": _required_str(bracket, "side"),
            "quantity": _required_str(bracket, "qty"),
            "order_type": _required_str(bracket, "type"),
            "time_in_force": _required_str(bracket, "time_in_force"),
            "order_class": _required_str(bracket, "order_class"),
            "extended_hours": bracket.get("extended_hours"),
            "limit_price": _required_str(bracket, "limit_price"),
            "take_profit_price": _required_str(take_profit, "limit_price"),
            "stop_loss_price": _required_str(stop_loss, "stop_price"),
            "notional": str(notional),
            "effective_notional_cap": str(cap),
            "risk_decision_safety_state_version": _required_int(
                package, "risk_decision_safety_state_version"
            ),
            "market_bid": str(market.bid),
            "market_ask": str(market.ask),
            "market_last": str(market.last),
            "market_observed_at": market.observed_at.isoformat(),
            "flat_position_count": flat.position_count,
            "flat_open_order_count": flat.open_order_count,
            "flat_clean_for_first_canary": flat.clean_for_first_canary,
            "reviewed_snapshot_at": now.astimezone(timezone.utc).isoformat(),
            "initial_evidence_may_expire": True,
            "final_freshness_reacquisition_required": True,
            "max_external_post_attempts": 1,
            "human_execution_intent_recorded": False,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
            "next_action": "SECOND_HUMAN_EXECUTION_INTENT_REQUIRED",
        }
        receipt = ConnectivityOperatorReviewReceipt(body=body, receipt_hash=_hash(body))
        _write_json_idempotent(self.path, receipt.document())
        return load_operator_review_receipt(self._workspace)


def load_operator_review_receipt(
    workspace: PaperOperationalWorkspace,
) -> ConnectivityOperatorReviewReceipt:
    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("PaperOperationalWorkspace is required")
    path = workspace.root / _RECEIPT
    if not path.is_file() or path.is_symlink():
        raise ConnectivityOperatorReviewRejected("canonical operator review receipt is required")
    raw = _read_json_object(path)
    receipt_hash = _required_hash(raw, "receipt_hash")
    body = dict(raw)
    body.pop("receipt_hash", None)
    receipt = ConnectivityOperatorReviewReceipt(body=body, receipt_hash=receipt_hash)

    preparation = _read_json_object(workspace.root / _PREPARATION)
    if receipt.body.get("preparation_hash") != preparation.get("preparation_hash"):
        raise ConnectivityOperatorReviewConflict("review receipt/preparation hash changed")
    for field, path in (
        ("account_artifact_sha256", workspace.account_attestation_path),
        ("asset_artifact_sha256", workspace.root / _ASSET),
        ("flat_account_artifact_sha256", workspace.root / _FLAT),
        ("market_artifact_sha256", workspace.root / _MARKET),
    ):
        if receipt.body.get(field) != _file_sha256(path):
            raise ConnectivityOperatorReviewConflict(f"reviewed evidence changed after receipt: {field}")

    operator_state = _load_operator_state(workspace)
    if receipt.body.get("operator_context_hash") != operator_state.decision.context.context_hash:
        raise ConnectivityOperatorReviewConflict("review receipt/operator context changed")
    if receipt.body.get("operator_decision_hash") != operator_state.decision.decision_hash:
        raise ConnectivityOperatorReviewConflict("review receipt/operator decision changed")
    if receipt.body.get("operator_event_hash") != operator_state.event_hash:
        raise ConnectivityOperatorReviewConflict("review receipt/operator event changed")
    return receipt


class ConnectivityReviewedExecutionIntentBridge:
    """Bind the second human execution intent to the exact reviewed receipt."""

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace
        self._intent = ConnectivityExecutionIntentBridge(workspace)

    @property
    def receipt_path(self) -> Path:
        return self._workspace.root / _RECEIPT

    @property
    def binding_path(self) -> Path:
        return self._workspace.root / _REVIEW_BINDING

    @property
    def registry_path(self) -> Path:
        return self._workspace.root / _REVIEW_DB

    def prepare(
        self, *, now: datetime
    ) -> tuple[ConnectivityExecutionIntentContext, ConnectivityOperatorReviewReceipt]:
        receipt = load_operator_review_receipt(self._workspace)
        context = self._intent.prepare_context(now=now)
        _verify_receipt_matches_execution_context(receipt, context)
        return context, receipt

    def issue(
        self,
        *,
        context: ConnectivityExecutionIntentContext,
        receipt_hash: str,
        operator_id: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> tuple[ConnectivityExecutionIntentState, ConnectivityExecutionReviewBinding]:
        receipt_before = load_operator_review_receipt(self._workspace)
        if receipt_before.receipt_hash != receipt_hash:
            raise ConnectivityOperatorReviewRejected("human confirmation receipt hash changed")
        _verify_receipt_matches_execution_context(receipt_before, context)
        state = self._intent.issue(
            context=context,
            operator_id=operator_id,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        receipt_after = load_operator_review_receipt(self._workspace)
        if receipt_after != receipt_before:
            raise ConnectivityOperatorReviewConflict(
                "review receipt changed while second human intent was being recorded"
            )
        binding = _build_execution_review_binding(
            workspace=self._workspace,
            receipt=receipt_after,
            state=state,
            bound_at=issued_at,
        )
        persisted = SQLiteConnectivityExecutionReviewBindingStore(
            SQLiteRuntime(self.registry_path)
        ).record(binding)
        artifact = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "binding": persisted.payload(),
            "operator_review_receipt_bound": True,
            "second_human_execution_intent_bound": True,
            "max_external_post_attempts": 1,
            "final_freshness_required": True,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
            "next_action": "REVIEWED_BOUND_FINAL_FRESHNESS_REQUIRED",
        }
        _write_json_idempotent(self.binding_path, artifact)
        return state, persisted


def reviewed_execution_intent_challenge(
    context: ConnectivityExecutionIntentContext,
    receipt: ConnectivityOperatorReviewReceipt,
) -> str:
    if not isinstance(context, ConnectivityExecutionIntentContext):
        raise TypeError("ConnectivityExecutionIntentContext is required")
    if not isinstance(receipt, ConnectivityOperatorReviewReceipt):
        raise TypeError("ConnectivityOperatorReviewReceipt is required")
    _verify_receipt_matches_execution_context(receipt, context)
    return (
        f"CONFIRM PAPER EXECUTION {context.context_hash[:12]} "
        f"REVIEW {receipt.receipt_hash[:12]}"
    )


def verify_execution_review_binding(
    workspace: PaperOperationalWorkspace,
) -> ConnectivityExecutionReviewBinding:
    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("PaperOperationalWorkspace is required")
    receipt = load_operator_review_receipt(workspace)
    review_path = workspace.root / _REVIEW_BINDING
    registry_path = workspace.root / _REVIEW_DB
    if not review_path.is_file() or review_path.is_symlink():
        raise ConnectivityOperatorReviewRejected("execution review binding artifact is required")
    if not registry_path.is_file() or registry_path.is_symlink():
        raise ConnectivityOperatorReviewRejected("execution review binding registry is required")
    raw = _read_json_object(review_path)
    for key, expected in (
        ("schema_version", 1),
        ("environment", "PAPER"),
        ("purpose", "CONNECTIVITY_CANARY"),
        ("operator_review_receipt_bound", True),
        ("second_human_execution_intent_bound", True),
        ("max_external_post_attempts", 1),
        ("final_freshness_required", True),
        ("oms_staging_authorized", False),
        ("external_post_authorized", False),
        ("external_order_submitted", False),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
        ("next_action", "REVIEWED_BOUND_FINAL_FRESHNESS_REQUIRED"),
    ):
        if raw.get(key) != expected:
            raise ConnectivityOperatorReviewConflict(f"unsafe execution review binding field: {key}")
    artifact_binding = _review_binding_from_payload(_mapping(raw, "binding"))
    durable = SQLiteConnectivityExecutionReviewBindingStore(
        SQLiteRuntime(registry_path)
    ).get()
    if artifact_binding != durable:
        raise ConnectivityOperatorReviewConflict("execution review artifact/registry mismatch")
    if durable.receipt_hash != receipt.receipt_hash:
        raise ConnectivityOperatorReviewConflict("execution review receipt binding changed")
    if durable.receipt_artifact_sha256 != _file_sha256(workspace.root / _RECEIPT):
        raise ConnectivityOperatorReviewConflict("review receipt file changed after binding")

    intent_states = SQLiteConnectivityExecutionIntentRegistry(
        SQLiteRuntime(workspace.root / _EXECUTION_INTENT_DB)
    ).list_states()
    if len(intent_states) != 1:
        raise ConnectivityOperatorReviewRejected("exactly one second execution intent is required")
    state = intent_states[0]
    if (
        durable.execution_intent_context_hash != state.decision.context.context_hash
        or durable.execution_intent_decision_hash != state.decision.decision_hash
        or durable.execution_intent_event_hash != state.event_hash
        or durable.execution_intent_artifact_sha256
        != _file_sha256(workspace.root / _EXECUTION_INTENT_ARTIFACT)
        or durable.operator_id != state.decision.operator_id
    ):
        raise ConnectivityOperatorReviewConflict("execution intent changed after review binding")
    _verify_receipt_matches_execution_context(receipt, state.decision.context)
    return durable


class ConnectivityReviewedBoundFinalFreshnessGuard:
    """Require review binding before GETs and bind it to the resulting <=5s authority."""

    def __init__(
        self,
        workspace: PaperOperationalWorkspace,
        *,
        base_guard: ConnectivityBoundFinalFreshnessGuard | None = None,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace
        self._base_guard = base_guard or ConnectivityBoundFinalFreshnessGuard(workspace)

    @property
    def artifact_path(self) -> Path:
        return self._workspace.root / _REVIEW_FRESHNESS

    def acquire(
        self, *, credentials: AlpacaPaperCredentials
    ) -> ConnectivityBoundFinalFreshnessResult:
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("AlpacaPaperCredentials are required")
        if self.artifact_path.exists():
            raise ConnectivityOperatorReviewRejected(
                "review/freshness binding already exists; never refresh in-place"
            )
        review_binding_before = verify_execution_review_binding(self._workspace)
        result = self._base_guard.acquire(credentials=credentials)
        review_binding_after = verify_execution_review_binding(self._workspace)
        if review_binding_after != review_binding_before:
            raise ConnectivityOperatorReviewConflict("review binding changed during Final Freshness")
        body = {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "operator_review_receipt_hash": review_binding_after.receipt_hash,
            "execution_review_binding_hash": review_binding_after.binding_hash,
            "execution_freshness_binding_hash": result.binding.binding_hash,
            "final_freshness_permit_hash": result.binding.final_freshness_permit_hash,
            "reviewed_human_intent_freshness_chain_bound": True,
            "max_external_post_attempts": 1,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
            "next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED",
        }
        artifact = {**body, "binding_hash": _hash(body)}
        _write_json_idempotent(self.artifact_path, artifact)
        verify_reviewed_final_freshness_binding(self._workspace, result)
        return result


def verify_reviewed_final_freshness_binding(
    workspace: PaperOperationalWorkspace,
    result: ConnectivityBoundFinalFreshnessResult,
) -> str:
    if not isinstance(workspace, PaperOperationalWorkspace):
        raise TypeError("PaperOperationalWorkspace is required")
    if not isinstance(result, ConnectivityBoundFinalFreshnessResult):
        raise TypeError("ConnectivityBoundFinalFreshnessResult is required")
    review_binding = verify_execution_review_binding(workspace)
    path = workspace.root / _REVIEW_FRESHNESS
    if not path.is_file() or path.is_symlink():
        raise ConnectivityOperatorReviewRejected("review/freshness binding artifact is required")
    raw = _read_json_object(path)
    binding_hash = _required_hash(raw, "binding_hash")
    body = dict(raw)
    body.pop("binding_hash", None)
    if binding_hash != _hash(body):
        raise ConnectivityOperatorReviewConflict("review/freshness binding hash mismatch")
    expected = {
        "schema_version": 1,
        "environment": "PAPER",
        "purpose": "CONNECTIVITY_CANARY",
        "operator_review_receipt_hash": review_binding.receipt_hash,
        "execution_review_binding_hash": review_binding.binding_hash,
        "execution_freshness_binding_hash": result.binding.binding_hash,
        "final_freshness_permit_hash": result.binding.final_freshness_permit_hash,
        "reviewed_human_intent_freshness_chain_bound": True,
        "max_external_post_attempts": 1,
        "oms_staging_authorized": False,
        "external_post_authorized": False,
        "external_order_submitted": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "next_action": "CONNECTIVITY_STAGING_BRIDGE_REQUIRED",
    }
    if body != expected:
        raise ConnectivityOperatorReviewConflict("review/freshness binding is non-canonical or changed")
    return binding_hash


def _load_operator_state(workspace: PaperOperationalWorkspace):
    registry_path = workspace.root / _OPERATOR_DB
    artifact_path = workspace.root / _OPERATOR_ARTIFACT
    if not registry_path.is_file() or registry_path.is_symlink():
        raise ConnectivityOperatorReviewRejected("first operator registry is required before review")
    if not artifact_path.is_file() or artifact_path.is_symlink():
        raise ConnectivityOperatorReviewRejected("first operator decision artifact is required before review")
    states = SQLiteConnectivityOperatorDecisionRegistry(
        SQLiteRuntime(registry_path)
    ).list_states()
    if len(states) != 1:
        raise ConnectivityOperatorReviewRejected("exactly one first operator decision is required")
    state = states[0]
    artifact = _read_json_object(artifact_path)
    if artifact.get("decision") != state.decision.payload() or artifact.get("event_hash") != state.event_hash:
        raise ConnectivityOperatorReviewConflict("first operator artifact/registry mismatch")
    return state


def _build_execution_review_binding(
    *,
    workspace: PaperOperationalWorkspace,
    receipt: ConnectivityOperatorReviewReceipt,
    state: ConnectivityExecutionIntentState,
    bound_at: datetime,
) -> ConnectivityExecutionReviewBinding:
    _require_aware(bound_at, "bound_at")
    values = {
        "order_id": state.decision.context.order_id,
        "client_order_id": state.decision.context.client_order_id,
        "attempt_id": state.decision.context.attempt_id,
        "receipt_hash": receipt.receipt_hash,
        "receipt_artifact_sha256": _file_sha256(workspace.root / _RECEIPT),
        "execution_intent_context_hash": state.decision.context.context_hash,
        "execution_intent_decision_hash": state.decision.decision_hash,
        "execution_intent_event_hash": state.event_hash,
        "execution_intent_artifact_sha256": _file_sha256(workspace.root / _EXECUTION_INTENT_ARTIFACT),
        "operator_id": state.decision.operator_id,
        "bound_at": bound_at.astimezone(timezone.utc),
    }
    values["binding_hash"] = _hash(_review_binding_body_from_values(values))
    return ConnectivityExecutionReviewBinding(**values)  # type: ignore[arg-type]


def _review_binding_body(binding: ConnectivityExecutionReviewBinding) -> dict[str, object]:
    return _review_binding_body_from_values(
        {
            "order_id": binding.order_id,
            "client_order_id": binding.client_order_id,
            "attempt_id": binding.attempt_id,
            "receipt_hash": binding.receipt_hash,
            "receipt_artifact_sha256": binding.receipt_artifact_sha256,
            "execution_intent_context_hash": binding.execution_intent_context_hash,
            "execution_intent_decision_hash": binding.execution_intent_decision_hash,
            "execution_intent_event_hash": binding.execution_intent_event_hash,
            "execution_intent_artifact_sha256": binding.execution_intent_artifact_sha256,
            "operator_id": binding.operator_id,
            "bound_at": binding.bound_at,
        }
    )


def _review_binding_body_from_values(values: Mapping[str, object]) -> dict[str, object]:
    bound_at = values["bound_at"]
    if not isinstance(bound_at, datetime):
        raise ValueError("bound_at must be datetime")
    return {
        "order_id": values["order_id"],
        "client_order_id": values["client_order_id"],
        "attempt_id": values["attempt_id"],
        "receipt_hash": values["receipt_hash"],
        "receipt_artifact_sha256": values["receipt_artifact_sha256"],
        "execution_intent_context_hash": values["execution_intent_context_hash"],
        "execution_intent_decision_hash": values["execution_intent_decision_hash"],
        "execution_intent_event_hash": values["execution_intent_event_hash"],
        "execution_intent_artifact_sha256": values["execution_intent_artifact_sha256"],
        "operator_id": values["operator_id"],
        "bound_at": bound_at.astimezone(timezone.utc).isoformat(),
    }


def _review_binding_from_payload(payload: Mapping[str, object]) -> ConnectivityExecutionReviewBinding:
    expected = {
        "order_id",
        "client_order_id",
        "attempt_id",
        "receipt_hash",
        "receipt_artifact_sha256",
        "execution_intent_context_hash",
        "execution_intent_decision_hash",
        "execution_intent_event_hash",
        "execution_intent_artifact_sha256",
        "operator_id",
        "bound_at",
        "binding_hash",
    }
    if set(payload) != expected:
        raise ConnectivityOperatorReviewConflict("execution review binding payload is non-canonical")
    try:
        return ConnectivityExecutionReviewBinding(
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            attempt_id=_required_str(payload, "attempt_id"),
            receipt_hash=_required_hash(payload, "receipt_hash"),
            receipt_artifact_sha256=_required_hash(payload, "receipt_artifact_sha256"),
            execution_intent_context_hash=_required_hash(payload, "execution_intent_context_hash"),
            execution_intent_decision_hash=_required_hash(payload, "execution_intent_decision_hash"),
            execution_intent_event_hash=_required_hash(payload, "execution_intent_event_hash"),
            execution_intent_artifact_sha256=_required_hash(payload, "execution_intent_artifact_sha256"),
            operator_id=_required_str(payload, "operator_id"),
            bound_at=_datetime(payload.get("bound_at"), "bound_at"),
            binding_hash=_required_hash(payload, "binding_hash"),
        )
    except (TypeError, ValueError) as exc:
        raise ConnectivityOperatorReviewConflict("execution review binding payload is invalid") from exc


def _verify_receipt_matches_execution_context(
    receipt: ConnectivityOperatorReviewReceipt,
    context: ConnectivityExecutionIntentContext,
) -> None:
    if (
        receipt.order_id != context.order_id
        or receipt.client_order_id != context.client_order_id
        or receipt.attempt_id != context.attempt_id
        or receipt.body.get("operator_context_hash") != context.operator_context_hash
        or receipt.body.get("operator_decision_hash") != context.operator_decision_hash
        or receipt.body.get("operator_event_hash") != context.operator_event_hash
        or receipt.body.get("preparation_hash") != context.preparation_hash
        or receipt.body.get("standard_package_hash") != context.standard_package_hash
        or receipt.body.get("bracket_payload_hash") != context.bracket_payload_hash
        or receipt.body.get("account_attestation_fingerprint") != context.initial_account_fingerprint
        or _decimal(receipt.body.get("notional"), "notional") != context.notional
    ):
        raise ConnectivityOperatorReviewConflict("operator review receipt does not bind execution-intent context")


def _validate_receipt_body(body: Mapping[str, object]) -> None:
    expected_keys = {
        "schema_version", "environment", "purpose", "review_type", "order_id",
        "client_order_id", "attempt_id", "operator_context_hash", "operator_decision_hash",
        "operator_event_hash", "preparation_hash", "standard_package_hash", "bracket_payload_hash",
        "account_attestation_fingerprint", "asset_attestation_fingerprint",
        "flat_account_attestation_fingerprint", "market_attestation_fingerprint",
        "account_artifact_sha256", "asset_artifact_sha256", "flat_account_artifact_sha256",
        "market_artifact_sha256", "symbol", "side", "quantity", "order_type", "time_in_force",
        "order_class", "extended_hours", "limit_price", "take_profit_price", "stop_loss_price",
        "notional", "effective_notional_cap", "risk_decision_safety_state_version", "market_bid",
        "market_ask", "market_last", "market_observed_at", "flat_position_count",
        "flat_open_order_count", "flat_clean_for_first_canary", "reviewed_snapshot_at",
        "initial_evidence_may_expire", "final_freshness_reacquisition_required",
        "max_external_post_attempts", "human_execution_intent_recorded", "oms_staging_authorized",
        "external_post_authorized", "external_order_submitted", "strategy_trading_authorized",
        "capital_authority", "profitability_claim", "live_trading", "next_action",
    }
    if set(body) != expected_keys:
        raise ValueError("operator review receipt body is non-canonical")
    for key, expected in (
        ("schema_version", 1),
        ("environment", "PAPER"),
        ("purpose", "CONNECTIVITY_CANARY"),
        ("review_type", "PRE_FINAL_FRESHNESS_HUMAN_ORDER_REVIEW"),
        ("side", "buy"),
        ("quantity", "1"),
        ("order_type", "limit"),
        ("time_in_force", "day"),
        ("order_class", "bracket"),
        ("extended_hours", False),
        ("flat_position_count", 0),
        ("flat_open_order_count", 0),
        ("flat_clean_for_first_canary", True),
        ("initial_evidence_may_expire", True),
        ("final_freshness_reacquisition_required", True),
        ("max_external_post_attempts", 1),
        ("human_execution_intent_recorded", False),
        ("oms_staging_authorized", False),
        ("external_post_authorized", False),
        ("external_order_submitted", False),
        ("strategy_trading_authorized", False),
        ("capital_authority", "NONE"),
        ("profitability_claim", False),
        ("live_trading", "BLOCKED"),
        ("next_action", "SECOND_HUMAN_EXECUTION_INTENT_REQUIRED"),
    ):
        if body.get(key) != expected:
            raise ValueError(f"unsafe/non-canonical operator review field: {key}")
    for key in (
        "order_id", "client_order_id", "attempt_id", "symbol", "limit_price",
        "take_profit_price", "stop_loss_price", "market_observed_at", "reviewed_snapshot_at",
    ):
        _required_str(body, key)
    for key in (
        "operator_context_hash", "operator_decision_hash", "operator_event_hash", "preparation_hash",
        "standard_package_hash", "bracket_payload_hash", "account_attestation_fingerprint",
        "asset_attestation_fingerprint", "flat_account_attestation_fingerprint",
        "market_attestation_fingerprint", "account_artifact_sha256", "asset_artifact_sha256",
        "flat_account_artifact_sha256", "market_artifact_sha256",
    ):
        _required_hash(body, key)
    notional = _decimal(body.get("notional"), "notional")
    cap = _decimal(body.get("effective_notional_cap"), "effective_notional_cap")
    entry = _decimal(body.get("limit_price"), "limit_price")
    tp = _decimal(body.get("take_profit_price"), "take_profit_price")
    sl = _decimal(body.get("stop_loss_price"), "stop_loss_price")
    if notional <= 0 or cap <= 0 or notional > cap or not tp > entry > sl > 0:
        raise ValueError("operator review notional/protection geometry is invalid")
    _required_int(body, "risk_decision_safety_state_version")
    _decimal(body.get("market_bid"), "market_bid")
    _decimal(body.get("market_ask"), "market_ask")
    _decimal(body.get("market_last"), "market_last")
    _datetime(body.get("market_observed_at"), "market_observed_at")
    _datetime(body.get("reviewed_snapshot_at"), "reviewed_snapshot_at")


def _mapping(payload: Mapping[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConnectivityOperatorReviewConflict(f"{key} must be object")
    return value


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectivityOperatorReviewConflict(f"{key} must be non-empty string")
    return value


def _required_hash(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    _validate_hash(value, key)
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConnectivityOperatorReviewConflict(f"{key} must be non-negative integer")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ConnectivityOperatorReviewConflict(f"{label} must be decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ConnectivityOperatorReviewConflict(f"{label} is invalid decimal") from exc
    if not parsed.is_finite():
        raise ConnectivityOperatorReviewConflict(f"{label} must be finite")
    return parsed


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical identifier")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be datetime string")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: Mapping[str, object]) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _json_object(raw: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectivityOperatorReviewConflict("review binding JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ConnectivityOperatorReviewConflict("review binding JSON root must be object")
    return payload


__all__ = [
    "ConnectivityExecutionReviewBinding",
    "ConnectivityOperatorReviewConflict",
    "ConnectivityOperatorReviewError",
    "ConnectivityOperatorReviewReceipt",
    "ConnectivityOperatorReviewReceiptBuilder",
    "ConnectivityOperatorReviewRejected",
    "ConnectivityReviewedBoundFinalFreshnessGuard",
    "ConnectivityReviewedExecutionIntentBridge",
    "SQLiteConnectivityExecutionReviewBindingStore",
    "load_operator_review_receipt",
    "reviewed_execution_intent_challenge",
    "verify_execution_review_binding",
    "verify_reviewed_final_freshness_binding",
]
