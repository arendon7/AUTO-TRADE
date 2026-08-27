from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetAttestation
from autotrade.brokers.alpaca_paper_crypto_canary_coordinator import (
    PREPARATION_EVIDENCE_TTL,
    CryptoCanaryPreparationResult,
    CryptoPaperCanaryCoordinator,
    PreparedCryptoPaperCanaryPackage,
)
from autotrade.brokers.alpaca_paper_crypto_lifecycle import (
    CryptoLifecycleStatus,
    SQLiteCryptoPaperLifecycle,
)
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketAttestation,
)
from autotrade.domain import MarketSnapshot, OrderStatus, intent_fingerprint, market_fingerprint
from autotrade.paper_execution_admission import PaperExecutionAdmissionReceipt
from autotrade.paper_execution_risk_contract import (
    PaperExecutionRiskContractResult,
    PaperExecutionRiskContractStatus,
)
from autotrade.paper_runtime_readiness_seal import (
    PaperRuntimeReadinessSealedResult,
    PaperRuntimeReadinessSealStatus,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.product_profile import ProductCapabilities


PAPER_EXECUTION_CANARY_PREPARATION_VERSION = "W87_PAPER_EXECUTION_CANARY_PREPARATION_V1"
_CERTIFIED_R6_TRACKS = ("R0", "R1", "R2", "R3", "R4", "R5")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
_UNRESOLVED_LOCAL_STATUSES = {
    CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN.value,
    CryptoLifecycleStatus.PROTECTION_SUBMISSION_UNKNOWN.value,
    CryptoLifecycleStatus.HALTED_RECONCILIATION_REQUIRED.value,
}


class PaperExecutionCanaryPreparationError(RuntimeError):
    pass


class PaperExecutionCanaryPreparationIntegrityError(PaperExecutionCanaryPreparationError):
    pass


class PaperExecutionCanaryPreparationBlocked(PaperExecutionCanaryPreparationError):
    pass


class PaperExecutionCanaryPreparationStatus(StrEnum):
    PREPARED = "PREPARED"


@dataclass(frozen=True, slots=True)
class PaperExecutionCanaryPreparationReceipt:
    bridge_id: str
    contract_version: str
    admission_hash: str
    risk_contract_hash: str
    readiness_seal_hash: str
    pipeline_receipt_hash: str
    account_attestation_fingerprint: str
    asset_attestation_fingerprint: str
    market_attestation_fingerprint: str
    market_snapshot_fingerprint: str
    product_profile_fingerprint: str
    intent_fingerprint: str
    risk_decision_fingerprint: str
    package_hash: str
    order_id: str
    lifecycle_id: str
    client_order_id: str
    account_id: str
    symbol: str
    quantity: Decimal
    limit_price: Decimal
    notional_usd: Decimal
    prepared_at: datetime
    risk_contract_valid_until: datetime
    package_execution_deadline: datetime
    unresolved_local_unknown_orders: int
    status: PaperExecutionCanaryPreparationStatus
    exact_w86_binding_verified: bool
    exact_admission_binding_verified: bool
    exact_risk_binding_verified: bool
    exact_broker_evidence_reconstructed: bool
    local_unknown_state_verified: bool
    oms_order_status: str
    lifecycle_status: str
    lifecycle_entry_attempt_count: int
    separate_human_execution_approval_required: bool
    operator_decision_required: bool
    capital_reserved: bool
    broker_write_performed: bool
    network_write_authorized: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    next_action: str
    receipt_hash: str

    def __post_init__(self) -> None:
        for name in ("bridge_id", "order_id", "lifecycle_id", "client_order_id"):
            _id(getattr(self, name), name)
        if self.contract_version != PAPER_EXECUTION_CANARY_PREPARATION_VERSION:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 canary preparation version is not canonical"
            )
        for name in (
            "admission_hash",
            "risk_contract_hash",
            "readiness_seal_hash",
            "pipeline_receipt_hash",
            "account_attestation_fingerprint",
            "asset_attestation_fingerprint",
            "market_attestation_fingerprint",
            "market_snapshot_fingerprint",
            "product_profile_fingerprint",
            "intent_fingerprint",
            "risk_decision_fingerprint",
            "package_hash",
            "receipt_hash",
        ):
            _sha(getattr(self, name), name)
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise PaperExecutionCanaryPreparationIntegrityError("account_id is required")
        if not isinstance(self.symbol, str) or self.symbol.count("/") != 1 or self.symbol != self.symbol.upper():
            raise PaperExecutionCanaryPreparationIntegrityError(
                "prepared symbol must be canonical BASE/QUOTE"
            )
        for name in ("quantity", "limit_price", "notional_usd"):
            _positive(getattr(self, name), name)
        if self.notional_usd != self.quantity * self.limit_price:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "prepared notional must equal exact quantity * limit_price"
            )
        for name in (
            "prepared_at",
            "risk_contract_valid_until",
            "package_execution_deadline",
        ):
            _aware(getattr(self, name), name)
        if _utc(self.prepared_at) >= _utc(self.package_execution_deadline):
            raise PaperExecutionCanaryPreparationIntegrityError(
                "prepared package must retain a positive execution window"
            )
        if _utc(self.package_execution_deadline) > _utc(self.risk_contract_valid_until):
            raise PaperExecutionCanaryPreparationIntegrityError(
                "prepared package may not outlive W87 risk contract"
            )
        if (
            isinstance(self.unresolved_local_unknown_orders, bool)
            or not isinstance(self.unresolved_local_unknown_orders, int)
            or self.unresolved_local_unknown_orders != 0
        ):
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 preparation requires zero unresolved local UNKNOWN orders"
            )
        if self.status is not PaperExecutionCanaryPreparationStatus.PREPARED:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 preparation receipt must be PREPARED"
            )
        if self.oms_order_status != OrderStatus.VALIDATED.value:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 preparation must stop at OMS VALIDATED"
            )
        if self.lifecycle_status != CryptoLifecycleStatus.ENTRY_PREPARED.value:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 preparation must stop at lifecycle ENTRY_PREPARED"
            )
        if self.lifecycle_entry_attempt_count != 0:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 preparation may not consume an entry attempt"
            )
        if (
            self.exact_w86_binding_verified is not True
            or self.exact_admission_binding_verified is not True
            or self.exact_risk_binding_verified is not True
            or self.exact_broker_evidence_reconstructed is not True
            or self.local_unknown_state_verified is not True
            or self.separate_human_execution_approval_required is not True
            or self.operator_decision_required is not True
            or self.capital_reserved is not False
            or self.broker_write_performed is not False
            or self.network_write_authorized is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
            or self.next_action != "OPERATOR_DECISION_REQUIRED"
        ):
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 preparation may create local OMS/lifecycle evidence only; human approval, "
                "capital, broker write, external execution and LIVE remain unavailable"
            )
        if self.receipt_hash != _hash(_receipt_payload(self, include_hash=False)):
            raise PaperExecutionCanaryPreparationIntegrityError(
                "W87 canary preparation receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _receipt_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperExecutionCanaryPreparationResult:
    receipt: PaperExecutionCanaryPreparationReceipt
    package: PreparedCryptoPaperCanaryPackage
    coordinator_result: CryptoCanaryPreparationResult

    def __post_init__(self) -> None:
        self.receipt.__post_init__()
        self.package.__post_init__()
        if self.coordinator_result.package != self.package:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "coordinator result differs from exact prepared package"
            )
        if self.package.package_hash != self.receipt.package_hash:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "prepared package differs from W87 bridge receipt"
            )


def prepare_paper_execution_canary(
    *,
    bridge_id: str,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
    risk_result: PaperExecutionRiskContractResult,
    coordinator: CryptoPaperCanaryCoordinator,
    runtime: SQLiteRuntime,
) -> PaperExecutionCanaryPreparationResult:
    """Prepare one W87 PAPER canary through the already-certified R6 coordinator.

    The bridge performs no network I/O and exposes no writer, credentials, POST,
    operator approval or LIVE control. It reconstructs exact broker evidence from
    W86, proves the W87 admission/risk bindings, stops at OMS VALIDATED and durable
    ENTRY_PREPARED, and returns only an OPERATOR_DECISION_REQUIRED package.
    """

    _id(bridge_id, "bridge_id")
    if not isinstance(admission, PaperExecutionAdmissionReceipt):
        raise TypeError("admission must be PaperExecutionAdmissionReceipt")
    if not isinstance(sealed_result, PaperRuntimeReadinessSealedResult):
        raise TypeError("sealed_result must be PaperRuntimeReadinessSealedResult")
    if not isinstance(risk_result, PaperExecutionRiskContractResult):
        raise TypeError("risk_result must be PaperExecutionRiskContractResult")
    if not isinstance(coordinator, CryptoPaperCanaryCoordinator):
        raise TypeError("coordinator must be certified CryptoPaperCanaryCoordinator")
    if not isinstance(runtime, SQLiteRuntime):
        raise TypeError("runtime must be SQLiteRuntime")

    admission.__post_init__()
    sealed_result.pipeline.__post_init__()
    sealed_result.seal.__post_init__()
    sealed_result.__post_init__()
    risk_result.__post_init__()
    _validate_exact_bindings(
        admission=admission,
        sealed_result=sealed_result,
        risk_result=risk_result,
    )

    now = _utc(_now_utc())
    seal = sealed_result.seal
    risk_receipt = risk_result.receipt
    if seal.status is not PaperRuntimeReadinessSealStatus.READY or seal.paper_runtime_ready is not True:
        raise PaperExecutionCanaryPreparationBlocked("W86 readiness seal is not READY")
    if not (_utc(seal.observed_at) <= now < _utc(seal.valid_until)):
        raise PaperExecutionCanaryPreparationBlocked(
            "W86 readiness seal is stale before canary preparation"
        )
    if risk_receipt.status is not PaperExecutionRiskContractStatus.RISK_APPROVED:
        raise PaperExecutionCanaryPreparationBlocked("W87 risk contract is not RISK_APPROVED")
    if not (_utc(risk_receipt.evaluated_at) <= now < _utc(risk_receipt.valid_until)):
        raise PaperExecutionCanaryPreparationBlocked(
            "W87 risk contract is stale before canary preparation"
        )

    account = sealed_result.pipeline.account_attestation
    asset = _rebuild_asset_attestation(sealed_result)
    market_attestation = _rebuild_market_attestation(sealed_result)
    product_profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )
    _validate_reconstructed_evidence(
        admission=admission,
        sealed_result=sealed_result,
        risk_result=risk_result,
        asset=asset,
        market_attestation=market_attestation,
        product_profile=product_profile,
    )

    predicted_deadline = min(
        _utc(risk_result.decision.valid_until),
        _utc(account.attested_at) + PREPARATION_EVIDENCE_TTL,
        _utc(asset.observed_at) + PREPARATION_EVIDENCE_TTL,
        _utc(market_attestation.received_at) + PREPARATION_EVIDENCE_TTL,
    )
    if predicted_deadline > _utc(risk_receipt.valid_until):
        raise PaperExecutionCanaryPreparationBlocked(
            "R6 coordinator deadline would outlive W87 risk/seal authority window"
        )
    if now >= predicted_deadline:
        raise PaperExecutionCanaryPreparationBlocked(
            "R6 coordinator evidence window is stale before preparation"
        )

    broker_truth = sealed_result.pipeline.broker_truth
    if (
        broker_truth.clean_for_candidate_start is not True
        or broker_truth.position_count != 0
        or broker_truth.open_order_count != 0
    ):
        raise PaperExecutionCanaryPreparationBlocked(
            "W87 canary preparation requires exact broker flatness from W86"
        )

    lifecycle = SQLiteCryptoPaperLifecycle(runtime)
    unresolved_unknown = _count_unresolved_local_unknown(runtime)
    if unresolved_unknown != 0:
        raise PaperExecutionCanaryPreparationBlocked(
            "local crypto lifecycle contains unresolved UNKNOWN/reconciliation state"
        )

    prepared = coordinator.prepare_entry(
        intent=risk_result.intent,
        decision=risk_result.decision,
        market_attestation=market_attestation,
        account_attestation=account,
        asset_attestation=asset,
        product_profile=product_profile,
        lifecycle=lifecycle,
        now=now,
        certified_tracks=_CERTIFIED_R6_TRACKS,
        reconciliation_clean=True,
        unresolved_unknown_orders=unresolved_unknown,
        relevant_open_orders=broker_truth.open_order_count,
        confirmed_pair_position_quantity=Decimal("0"),
    )
    _validate_prepared_result(
        prepared=prepared,
        admission=admission,
        sealed_result=sealed_result,
        risk_result=risk_result,
        product_profile=product_profile,
    )

    package = prepared.package
    values = {
        "bridge_id": bridge_id,
        "contract_version": PAPER_EXECUTION_CANARY_PREPARATION_VERSION,
        "admission_hash": admission.receipt_hash,
        "risk_contract_hash": risk_receipt.receipt_hash,
        "readiness_seal_hash": seal.receipt_hash,
        "pipeline_receipt_hash": sealed_result.pipeline.receipt.receipt_hash,
        "account_attestation_fingerprint": account.fingerprint,
        "asset_attestation_fingerprint": asset.fingerprint,
        "market_attestation_fingerprint": market_attestation.fingerprint,
        "market_snapshot_fingerprint": market_fingerprint(market_attestation.market),
        "product_profile_fingerprint": product_profile.fingerprint,
        "intent_fingerprint": intent_fingerprint(risk_result.intent),
        "risk_decision_fingerprint": risk_receipt.risk_decision_fingerprint,
        "package_hash": package.package_hash,
        "order_id": package.order_id,
        "lifecycle_id": package.lifecycle_id,
        "client_order_id": package.client_order_id,
        "account_id": admission.account_id,
        "symbol": package.symbol,
        "quantity": package.quantity,
        "limit_price": package.limit_price,
        "notional_usd": package.notional,
        "prepared_at": package.prepared_at,
        "risk_contract_valid_until": risk_receipt.valid_until,
        "package_execution_deadline": package.execution_deadline,
        "unresolved_local_unknown_orders": unresolved_unknown,
        "status": PaperExecutionCanaryPreparationStatus.PREPARED,
        "exact_w86_binding_verified": True,
        "exact_admission_binding_verified": True,
        "exact_risk_binding_verified": True,
        "exact_broker_evidence_reconstructed": True,
        "local_unknown_state_verified": True,
        "oms_order_status": prepared.order.status.value,
        "lifecycle_status": prepared.lifecycle_state.status.value,
        "lifecycle_entry_attempt_count": prepared.lifecycle_state.entry_attempt_count,
        "separate_human_execution_approval_required": True,
        "operator_decision_required": True,
        "capital_reserved": False,
        "broker_write_performed": False,
        "network_write_authorized": package.network_write_authorized,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "next_action": package.next_action,
    }
    receipt = PaperExecutionCanaryPreparationReceipt(
        **values,
        receipt_hash=_hash(_receipt_payload_from_values(values)),
    )
    return PaperExecutionCanaryPreparationResult(
        receipt=receipt,
        package=package,
        coordinator_result=prepared,
    )


def _validate_exact_bindings(
    *,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
    risk_result: PaperExecutionRiskContractResult,
) -> None:
    receipt = risk_result.receipt
    seal = sealed_result.seal
    pipeline = sealed_result.pipeline
    if admission.readiness_seal_hash != seal.receipt_hash:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W87 admission is not bound to exact W86 seal"
        )
    if receipt.admission_hash != admission.receipt_hash:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W87 risk contract is not bound to exact admission"
        )
    if receipt.readiness_seal_hash != seal.receipt_hash:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W87 risk contract is not bound to exact W86 seal"
        )
    if receipt.pipeline_receipt_hash != pipeline.receipt.receipt_hash:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W87 risk contract is not bound to exact W86 pipeline"
        )
    if (
        admission.account_id != seal.account_id
        or admission.account_id != receipt.account_id
        or admission.symbol != seal.symbol
        or admission.broker_pair != risk_result.intent.symbol
        or receipt.candidate_symbol != admission.symbol
        or receipt.broker_pair != admission.broker_pair
    ):
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W86/W87 account or instrument identity differs"
        )
    if receipt.intent_fingerprint != intent_fingerprint(risk_result.intent):
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W87 risk receipt differs from exact OrderIntent"
        )
    if receipt.market_snapshot_fingerprint != market_fingerprint(risk_result.market):
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W87 risk receipt differs from exact market snapshot"
        )
    if (
        risk_result.intent.quantity != admission.canary_quantity
        or risk_result.intent.limit_price != admission.conservative_limit_price
        or receipt.approved_notional_usd != admission.canary_notional_usd
    ):
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W87 risk contract altered admitted quantity, price or notional"
        )


def _rebuild_asset_attestation(
    sealed_result: PaperRuntimeReadinessSealedResult,
) -> AlpacaPaperCryptoAssetAttestation:
    proof = sealed_result.pipeline.asset_truth
    asset = AlpacaPaperCryptoAssetAttestation(
        symbol=proof.canonical_broker_pair,
        asset_id=proof.asset_id,
        asset_class=proof.asset_class,
        exchange=proof.exchange,
        status=proof.status,
        tradable=proof.tradable,
        fractionable=proof.fractionable,
        marginable=proof.marginable,
        shortable=proof.shortable,
        min_order_size=proof.min_order_size,
        min_trade_increment=proof.min_trade_increment,
        price_increment=proof.price_increment,
        account_attestation_fingerprint=proof.account_attestation_fingerprint,
        credential_reference=proof.credential_reference,
        observed_at=proof.asset_observed_at,
        request_id=proof.asset_request_id,
        response_sha256=proof.asset_response_sha256,
        source_path=proof.source_path,
        source_host=proof.source_host,
    )
    if asset.fingerprint != proof.asset_attestation_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "reconstructed asset attestation fingerprint differs from W86"
        )
    if asset.contract_fingerprint != proof.asset_contract_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "reconstructed asset contract fingerprint differs from W86"
        )
    return asset


def _rebuild_market_attestation(
    sealed_result: PaperRuntimeReadinessSealedResult,
) -> AlpacaPaperCryptoMarketAttestation:
    proof = sealed_result.pipeline.market_truth
    market = MarketSnapshot(
        symbol=proof.canonical_broker_pair,
        bid=proof.bid_price,
        ask=proof.ask_price,
        last=proof.trade_price,
        observed_at=proof.market_received_at,
    )
    attestation = AlpacaPaperCryptoMarketAttestation(
        market=market,
        location=proof.location,
        quote_observed_at=proof.quote_observed_at,
        trade_observed_at=proof.trade_observed_at,
        received_at=proof.market_received_at,
        quote_response_sha256=proof.quote_response_sha256,
        trade_response_sha256=proof.trade_response_sha256,
        source_host=proof.source_host,
    )
    if market_fingerprint(market) != proof.market_snapshot_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "reconstructed market snapshot fingerprint differs from W86"
        )
    if attestation.fingerprint != proof.market_attestation_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "reconstructed market attestation fingerprint differs from W86"
        )
    return attestation


def _validate_reconstructed_evidence(
    *,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
    risk_result: PaperExecutionRiskContractResult,
    asset: AlpacaPaperCryptoAssetAttestation,
    market_attestation: AlpacaPaperCryptoMarketAttestation,
    product_profile: ProductCapabilities,
) -> None:
    pipeline = sealed_result.pipeline
    account = pipeline.account_attestation
    if account.fingerprint != pipeline.receipt.account_attestation_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "account attestation differs from W86 collection receipt"
        )
    if account.fingerprint != pipeline.asset_truth.account_attestation_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W86 asset proof differs from exact account attestation"
        )
    if account.account_id != admission.account_id:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W86 account attestation differs from W87 admission account"
        )
    if asset.fingerprint != pipeline.market_truth.asset_attestation_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "W86 market proof differs from exact asset attestation"
        )
    if market_attestation.market != risk_result.market:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "reconstructed W86 market differs from W87 Safety market"
        )
    if product_profile.source_fingerprint != asset.fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "ProductCapabilities is not bound to exact W86 asset evidence"
        )


def _validate_prepared_result(
    *,
    prepared: CryptoCanaryPreparationResult,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
    risk_result: PaperExecutionRiskContractResult,
    product_profile: ProductCapabilities,
) -> None:
    package = prepared.package
    package.__post_init__()
    if prepared.order.status is not OrderStatus.VALIDATED:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 coordinator did not stop at OMS VALIDATED"
        )
    if prepared.lifecycle_state.status is not CryptoLifecycleStatus.ENTRY_PREPARED:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 coordinator did not stop at ENTRY_PREPARED"
        )
    if prepared.lifecycle_state.entry_attempt_count != 0:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 coordinator consumed an entry attempt during preparation"
        )
    if package.next_action != "OPERATOR_DECISION_REQUIRED" or package.network_write_authorized is not False:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package escaped operator-decision/no-write boundary"
        )
    if package.intent_fingerprint != intent_fingerprint(risk_result.intent):
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package intent differs from W87 risk contract"
        )
    if package.risk_decision_fingerprint != risk_result.receipt.risk_decision_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package RiskDecision differs from W87 risk contract"
        )
    if package.market_fingerprint != risk_result.receipt.market_snapshot_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package market differs from W87 risk contract"
        )
    if package.account_attestation_fingerprint != sealed_result.pipeline.account_attestation.fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package account evidence differs from W86"
        )
    if package.asset_attestation_fingerprint != sealed_result.pipeline.asset_truth.asset_attestation_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package asset evidence differs from W86"
        )
    if package.market_attestation_fingerprint != sealed_result.pipeline.market_truth.market_attestation_fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package market attestation differs from W86"
        )
    if package.product_profile_fingerprint != product_profile.fingerprint:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package product profile differs from reconstructed W86 evidence"
        )
    if (
        package.symbol != admission.broker_pair
        or package.quantity != admission.canary_quantity
        or package.limit_price != admission.conservative_limit_price
        or package.notional != admission.canary_notional_usd
    ):
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package altered W87 admitted symbol, quantity, price or notional"
        )
    if _utc(package.execution_deadline) > _utc(risk_result.receipt.valid_until):
        raise PaperExecutionCanaryPreparationIntegrityError(
            "R6 package execution deadline outlives W87 risk contract"
        )


def _count_unresolved_local_unknown(runtime: SQLiteRuntime) -> int:
    conn = runtime.connect()
    try:
        rows = conn.execute(
            "SELECT state_json FROM alpaca_crypto_lifecycle_control ORDER BY lifecycle_id"
        ).fetchall()
    except Exception as exc:
        raise PaperExecutionCanaryPreparationIntegrityError(
            "cannot read durable crypto lifecycle control state"
        ) from exc
    finally:
        conn.close()

    count = 0
    for row in rows:
        raw = row["state_json"] if hasattr(row, "keys") else row[0]
        try:
            payload = json.loads(str(raw))
        except Exception as exc:
            raise PaperExecutionCanaryPreparationIntegrityError(
                "durable crypto lifecycle state is not canonical JSON"
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
            raise PaperExecutionCanaryPreparationIntegrityError(
                "durable crypto lifecycle state lacks canonical status"
            )
        if payload["status"] in _UNRESOLVED_LOCAL_STATUSES:
            count += 1
    return count


def _receipt_payload(
    value: PaperExecutionCanaryPreparationReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    payload = _receipt_payload_from_values(
        {
            name: getattr(value, name)
            for name in value.__dataclass_fields__
            if name != "receipt_hash"
        }
    )
    if include_hash:
        payload["receipt_hash"] = value.receipt_hash
    return payload


def _receipt_payload_from_values(values: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            payload[key] = _utc(value).isoformat()
        elif isinstance(value, Decimal):
            payload[key] = format(value, "f")
        elif isinstance(value, StrEnum):
            payload[key] = value.value
        else:
            payload[key] = value
    return payload


def _id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperExecutionCanaryPreparationIntegrityError(f"{label} is invalid")


def _sha(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperExecutionCanaryPreparationIntegrityError(
            f"{label} must be lowercase SHA-256"
        )


def _positive(value: Decimal, label: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PaperExecutionCanaryPreparationIntegrityError(
            f"{label} must be finite positive Decimal"
        )


def _aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperExecutionCanaryPreparationIntegrityError(
            f"{label} must be timezone-aware"
        )


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "PAPER_EXECUTION_CANARY_PREPARATION_VERSION",
    "PaperExecutionCanaryPreparationBlocked",
    "PaperExecutionCanaryPreparationError",
    "PaperExecutionCanaryPreparationIntegrityError",
    "PaperExecutionCanaryPreparationReceipt",
    "PaperExecutionCanaryPreparationResult",
    "PaperExecutionCanaryPreparationStatus",
    "prepare_paper_execution_canary",
]
