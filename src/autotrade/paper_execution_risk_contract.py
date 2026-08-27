from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Mapping, Protocol

from autotrade.domain import (
    MarketSnapshot,
    OrderIntent,
    OrderType,
    RiskDecision,
    RiskDecisionStatus,
    Side,
    intent_fingerprint,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.paper_execution_admission import PaperExecutionAdmissionReceipt
from autotrade.paper_runtime_readiness_seal import (
    PaperRuntimeReadinessSealedResult,
    PaperRuntimeReadinessSealStatus,
)
from autotrade.portfolio_integrity import portfolio_snapshot_error
from autotrade.safety import CapitalSafetyKernel
from autotrade.state import VersionedPortfolioSnapshot


PAPER_EXECUTION_RISK_CONTRACT_VERSION = "W87_PAPER_EXECUTION_RISK_CONTRACT_V1"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")


class PaperExecutionRiskContractError(RuntimeError):
    pass


class PaperExecutionRiskContractIntegrityError(PaperExecutionRiskContractError):
    pass


class PaperExecutionRiskContractBlocked(PaperExecutionRiskContractError):
    pass


class PaperExecutionRiskContractStatus(StrEnum):
    RISK_APPROVED = "RISK_APPROVED"


class PortfolioSnapshotReader(Protocol):
    def get(self) -> VersionedPortfolioSnapshot: ...


@dataclass(frozen=True, slots=True)
class PaperExecutionRiskContractReceipt:
    contract_id: str
    contract_version: str
    admission_hash: str
    readiness_seal_hash: str
    pipeline_receipt_hash: str
    market_truth_hash: str
    market_snapshot_fingerprint: str
    intent_id: str
    idempotency_key: str
    intent_fingerprint: str
    risk_decision_id: str
    risk_decision_fingerprint: str
    limits_version: str
    strategy_id: str
    product_id: str
    candidate_symbol: str
    broker_pair: str
    account_id: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal
    approved_notional_usd: Decimal
    portfolio_snapshot_id: str
    portfolio_version: int
    safety_state_version: int
    status: PaperExecutionRiskContractStatus
    evaluated_at: datetime
    risk_decision_valid_until: datetime
    readiness_seal_valid_until: datetime
    valid_until: datetime
    exact_admission_binding_verified: bool
    exact_market_binding_verified: bool
    authoritative_safety_approval_verified: bool
    portfolio_flatness_verified: bool
    portfolio_unchanged_during_evaluation: bool
    safety_state_unchanged_during_evaluation: bool
    separate_human_execution_approval_required: bool
    oms_handoff_permitted: bool
    capital_reserved: bool
    broker_write_performed: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    next_action: str
    receipt_hash: str

    def __post_init__(self) -> None:
        for name in (
            "contract_id",
            "intent_id",
            "idempotency_key",
            "risk_decision_id",
            "limits_version",
            "strategy_id",
            "product_id",
        ):
            _id(getattr(self, name), name)
        if self.contract_version != PAPER_EXECUTION_RISK_CONTRACT_VERSION:
            raise PaperExecutionRiskContractIntegrityError(
                "W87 risk contract version is not canonical"
            )
        for name in (
            "admission_hash",
            "readiness_seal_hash",
            "pipeline_receipt_hash",
            "market_truth_hash",
            "market_snapshot_fingerprint",
            "intent_fingerprint",
            "risk_decision_fingerprint",
            "receipt_hash",
        ):
            _sha(getattr(self, name), name)

        _candidate_symbol(self.candidate_symbol)
        _broker_pair(self.broker_pair)
        if self.candidate_symbol.replace("-", "/") != self.broker_pair:
            raise PaperExecutionRiskContractIntegrityError(
                "candidate symbol and broker pair differ"
            )
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise PaperExecutionRiskContractIntegrityError("account_id is required")
        if self.side != Side.BUY.value or self.order_type != OrderType.LIMIT.value:
            raise PaperExecutionRiskContractIntegrityError(
                "W87 first risk contract must remain BUY LIMIT"
            )
        for name in ("quantity", "limit_price", "approved_notional_usd"):
            _positive(getattr(self, name), name)
        if self.approved_notional_usd != self.quantity * self.limit_price:
            raise PaperExecutionRiskContractIntegrityError(
                "approved notional does not equal exact quantity * limit price"
            )
        if (
            isinstance(self.portfolio_version, bool)
            or not isinstance(self.portfolio_version, int)
            or self.portfolio_version <= 0
        ):
            raise PaperExecutionRiskContractIntegrityError(
                "portfolio_version must be positive integer"
            )
        if (
            isinstance(self.safety_state_version, bool)
            or not isinstance(self.safety_state_version, int)
            or self.safety_state_version < 0
        ):
            raise PaperExecutionRiskContractIntegrityError(
                "safety_state_version must be non-negative integer"
            )
        if not isinstance(self.portfolio_snapshot_id, str) or not self.portfolio_snapshot_id.strip():
            raise PaperExecutionRiskContractIntegrityError(
                "portfolio_snapshot_id is required"
            )
        if self.status is not PaperExecutionRiskContractStatus.RISK_APPROVED:
            raise PaperExecutionRiskContractIntegrityError(
                "W87 risk contract must be RISK_APPROVED"
            )

        for name in (
            "evaluated_at",
            "risk_decision_valid_until",
            "readiness_seal_valid_until",
            "valid_until",
        ):
            _aware(getattr(self, name), name)
        evaluated = _utc(self.evaluated_at)
        risk_until = _utc(self.risk_decision_valid_until)
        seal_until = _utc(self.readiness_seal_valid_until)
        expected_valid_until = min(risk_until, seal_until)
        if _utc(self.valid_until) != expected_valid_until:
            raise PaperExecutionRiskContractIntegrityError(
                "risk contract valid_until must be exact minimum of RiskDecision and W86 seal"
            )
        if not evaluated < expected_valid_until:
            raise PaperExecutionRiskContractIntegrityError(
                "risk contract must have positive remaining authority window"
            )

        if (
            self.exact_admission_binding_verified is not True
            or self.exact_market_binding_verified is not True
            or self.authoritative_safety_approval_verified is not True
            or self.portfolio_flatness_verified is not True
            or self.portfolio_unchanged_during_evaluation is not True
            or self.safety_state_unchanged_during_evaluation is not True
            or self.separate_human_execution_approval_required is not True
            or self.oms_handoff_permitted is not False
            or self.capital_reserved is not False
            or self.broker_write_performed is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
            or self.next_action != "CANARY_PREPARATION_REQUIRED"
        ):
            raise PaperExecutionRiskContractIntegrityError(
                "W87 risk contract may prove Safety approval only; human approval, "
                "OMS/execution, capital, broker-write and LIVE remain unavailable"
            )
        if self.receipt_hash != _hash(_payload(self, include_hash=False)):
            raise PaperExecutionRiskContractIntegrityError(
                "W87 risk contract receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class PaperExecutionRiskContractResult:
    receipt: PaperExecutionRiskContractReceipt
    intent: OrderIntent
    decision: RiskDecision
    market: MarketSnapshot

    def __post_init__(self) -> None:
        self.receipt.__post_init__()
        if intent_fingerprint(self.intent) != self.receipt.intent_fingerprint:
            raise PaperExecutionRiskContractIntegrityError(
                "result intent does not match W87 risk contract"
            )
        if risk_decision_fingerprint(self.decision) != self.receipt.risk_decision_fingerprint:
            raise PaperExecutionRiskContractIntegrityError(
                "result RiskDecision does not match W87 risk contract"
            )
        if market_fingerprint(self.market) != self.receipt.market_snapshot_fingerprint:
            raise PaperExecutionRiskContractIntegrityError(
                "result market does not match W87 risk contract"
            )


def evaluate_paper_execution_risk_contract(
    *,
    contract_id: str,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
    safety: CapitalSafetyKernel,
    portfolio_store: PortfolioSnapshotReader,
) -> PaperExecutionRiskContractResult:
    """Evaluate one exact W87 PAPER canary through authoritative Capital Safety.

    This bridge is pre-OMS and pre-execution. It reconstructs the exact admitted
    OrderIntent and exact W86 market snapshot, evaluates both through the
    authoritative CapitalSafetyKernel, and rejects portfolio/Safety-control drift.
    """

    _id(contract_id, "contract_id")
    if not isinstance(admission, PaperExecutionAdmissionReceipt):
        raise TypeError("admission must be PaperExecutionAdmissionReceipt")
    if not isinstance(sealed_result, PaperRuntimeReadinessSealedResult):
        raise TypeError("sealed_result must be PaperRuntimeReadinessSealedResult")
    if not isinstance(safety, CapitalSafetyKernel):
        raise TypeError("safety must be authoritative CapitalSafetyKernel")
    if not hasattr(portfolio_store, "get") or not callable(portfolio_store.get):
        raise TypeError("portfolio_store must expose get()")

    admission.__post_init__()
    _validate_w86_source(sealed_result)
    _validate_admission_binding(admission=admission, sealed_result=sealed_result)

    now = _utc(_now_utc())
    seal = sealed_result.seal
    if (
        seal.status is not PaperRuntimeReadinessSealStatus.READY
        or seal.paper_runtime_ready is not True
    ):
        raise PaperExecutionRiskContractBlocked("W86 readiness seal is not READY")
    if not (_utc(seal.observed_at) <= now < _utc(seal.valid_until)):
        raise PaperExecutionRiskContractBlocked(
            "W86 readiness seal is not fresh for Safety evaluation"
        )

    market = _exact_market(sealed_result)
    intent = _exact_intent(admission)

    before_portfolio = portfolio_store.get()
    _validate_versioned_portfolio(before_portfolio)
    _require_first_canary_flat_portfolio(before_portfolio)
    before_safety = safety.state_store.get()
    sealed_safety = sealed_result.pipeline.safety_health_truth
    if (
        before_safety.version != sealed_safety.safety_version
        or before_safety.kill_switch_active is not False
        or before_safety.circuit_active is not False
    ):
        raise PaperExecutionRiskContractBlocked(
            "authoritative Safety state no longer matches fresh W86 seal"
        )

    decision = safety.evaluate(
        intent=intent,
        market=market,
        portfolio=before_portfolio.snapshot,
        now=now,
    )

    after_safety = safety.state_store.get()
    after_portfolio = portfolio_store.get()
    _validate_versioned_portfolio(after_portfolio)

    if after_safety != before_safety:
        raise PaperExecutionRiskContractBlocked(
            "Safety control state changed during W87 risk evaluation"
        )
    if after_portfolio != before_portfolio:
        raise PaperExecutionRiskContractBlocked(
            "portfolio state changed during W87 risk evaluation"
        )
    if decision.safety_state_version != before_safety.version:
        raise PaperExecutionRiskContractBlocked(
            "RiskDecision Safety state version does not match stable control state"
        )
    if decision.status is not RiskDecisionStatus.APPROVED:
        raise PaperExecutionRiskContractBlocked(
            f"Capital Safety rejected W87 canary: {decision.reason_code}"
        )
    _validate_decision(
        decision=decision,
        intent=intent,
        market=market,
        admission=admission,
        now=now,
    )
    if not now < _utc(seal.valid_until):
        raise PaperExecutionRiskContractBlocked(
            "W86 readiness seal expired during Safety evaluation"
        )

    valid_until = min(_utc(decision.valid_until), _utc(seal.valid_until))
    if not now < valid_until:
        raise PaperExecutionRiskContractBlocked(
            "W87 risk contract has no positive remaining authority window"
        )
    if decision.approved_notional is None or intent.limit_price is None:
        raise PaperExecutionRiskContractIntegrityError(
            "approved W87 LIMIT decision lacks exact price/notional"
        )

    values = {
        "contract_id": contract_id,
        "contract_version": PAPER_EXECUTION_RISK_CONTRACT_VERSION,
        "admission_hash": admission.receipt_hash,
        "readiness_seal_hash": seal.receipt_hash,
        "pipeline_receipt_hash": sealed_result.pipeline.receipt.receipt_hash,
        "market_truth_hash": sealed_result.pipeline.market_truth.proof_hash,
        "market_snapshot_fingerprint": market_fingerprint(market),
        "intent_id": intent.intent_id,
        "idempotency_key": intent.idempotency_key,
        "intent_fingerprint": intent_fingerprint(intent),
        "risk_decision_id": decision.decision_id,
        "risk_decision_fingerprint": risk_decision_fingerprint(decision),
        "limits_version": decision.limits_version,
        "strategy_id": admission.strategy_id,
        "product_id": admission.product_id,
        "candidate_symbol": admission.symbol,
        "broker_pair": admission.broker_pair,
        "account_id": admission.account_id,
        "side": intent.side.value,
        "order_type": intent.order_type.value,
        "quantity": intent.quantity,
        "limit_price": intent.limit_price,
        "approved_notional_usd": decision.approved_notional,
        "portfolio_snapshot_id": before_portfolio.snapshot.snapshot_id,
        "portfolio_version": before_portfolio.version,
        "safety_state_version": before_safety.version,
        "status": PaperExecutionRiskContractStatus.RISK_APPROVED,
        "evaluated_at": now,
        "risk_decision_valid_until": decision.valid_until,
        "readiness_seal_valid_until": seal.valid_until,
        "valid_until": valid_until,
        "exact_admission_binding_verified": True,
        "exact_market_binding_verified": True,
        "authoritative_safety_approval_verified": True,
        "portfolio_flatness_verified": True,
        "portfolio_unchanged_during_evaluation": True,
        "safety_state_unchanged_during_evaluation": True,
        "separate_human_execution_approval_required": True,
        "oms_handoff_permitted": False,
        "capital_reserved": False,
        "broker_write_performed": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
        "next_action": "CANARY_PREPARATION_REQUIRED",
    }
    receipt = PaperExecutionRiskContractReceipt(
        **values,
        receipt_hash=_hash(_payload_values(values)),
    )
    return PaperExecutionRiskContractResult(
        receipt=receipt,
        intent=intent,
        decision=decision,
        market=market,
    )


def _validate_w86_source(value: PaperRuntimeReadinessSealedResult) -> None:
    value.pipeline.broker_truth.__post_init__()
    value.pipeline.asset_truth.__post_init__()
    value.pipeline.market_truth.__post_init__()
    value.pipeline.safety_health_truth.__post_init__()
    value.pipeline.final_readiness.__post_init__()
    value.pipeline.funding_capacity.__post_init__()
    value.pipeline.receipt.__post_init__()
    value.pipeline.__post_init__()
    value.post_collection_source.__post_init__()
    value.seal.__post_init__()
    value.__post_init__()


def _validate_admission_binding(
    *,
    admission: PaperExecutionAdmissionReceipt,
    sealed_result: PaperRuntimeReadinessSealedResult,
) -> None:
    seal = sealed_result.seal
    final = sealed_result.pipeline.final_readiness
    market_truth = sealed_result.pipeline.market_truth
    if (
        admission.readiness_seal_hash != seal.receipt_hash
        or admission.pipeline_receipt_hash != sealed_result.pipeline.receipt.receipt_hash
        or admission.final_readiness_hash != final.receipt_hash
        or admission.funding_capacity_hash != sealed_result.pipeline.funding_capacity.proof_hash
        or admission.source_snapshot_hash != seal.source_snapshot_hash
        or admission.candidate_identity_hash != seal.candidate_identity_hash
        or admission.authority_key != seal.authority_key
        or admission.w85_admission_hash != seal.admission_hash
        or admission.strategy_id != seal.strategy_id
        or admission.product_id != seal.product_id
        or admission.symbol != seal.symbol
        or admission.account_id != seal.account_id
        or admission.broker_pair != market_truth.canonical_broker_pair
    ):
        raise PaperExecutionRiskContractIntegrityError(
            "W87 admission is not bound to exact W86 sealed result"
        )
    if (
        admission.canary_quantity < admission.broker_minimum_executable_quantity
        or admission.canary_quantity % admission.broker_trade_increment != 0
        or admission.conservative_limit_price != final.conservative_unit_price
        or admission.canary_notional_usd
        != admission.canary_quantity * admission.conservative_limit_price
    ):
        raise PaperExecutionRiskContractIntegrityError(
            "W87 admission exact canary envelope is inconsistent with W86"
        )


def _exact_market(sealed_result: PaperRuntimeReadinessSealedResult) -> MarketSnapshot:
    proof = sealed_result.pipeline.market_truth
    market = MarketSnapshot(
        symbol=proof.canonical_broker_pair,
        bid=proof.bid_price,
        ask=proof.ask_price,
        last=proof.trade_price,
        observed_at=proof.market_received_at,
    )
    if market_fingerprint(market) != proof.market_snapshot_fingerprint:
        raise PaperExecutionRiskContractIntegrityError(
            "W86 market truth cannot reconstruct exact attested MarketSnapshot"
        )
    return market


def _exact_intent(admission: PaperExecutionAdmissionReceipt) -> OrderIntent:
    token = admission.receipt_hash[:32]
    return OrderIntent(
        intent_id=f"w87-risk:{token}",
        idempotency_key=f"w87-risk-idem:{token}",
        strategy_id=admission.strategy_id,
        symbol=admission.broker_pair,
        side=Side.BUY,
        quantity=admission.canary_quantity,
        order_type=OrderType.LIMIT,
        created_at=admission.captured_at,
        limit_price=admission.conservative_limit_price,
    )


def _validate_versioned_portfolio(value: VersionedPortfolioSnapshot) -> None:
    if not isinstance(value, VersionedPortfolioSnapshot):
        raise PaperExecutionRiskContractIntegrityError(
            "portfolio reader did not return VersionedPortfolioSnapshot"
        )
    if isinstance(value.version, bool) or not isinstance(value.version, int) or value.version <= 0:
        raise PaperExecutionRiskContractIntegrityError(
            "portfolio version must be positive integer"
        )
    error = portfolio_snapshot_error(value.snapshot)
    if error is not None:
        raise PaperExecutionRiskContractIntegrityError(
            f"durable portfolio snapshot is invalid: {error}"
        )


def _require_first_canary_flat_portfolio(value: VersionedPortfolioSnapshot) -> None:
    portfolio = value.snapshot
    if (
        portfolio.gross_exposure != 0
        or portfolio.net_exposure != 0
        or portfolio.open_orders != 0
        or bool(portfolio.signed_position_notional_by_symbol)
        or any(amount != 0 for amount in portfolio.strategy_gross_exposure.values())
        or any(
            bool(positions)
            for positions in portfolio.strategy_signed_position_notional_by_symbol.values()
        )
        or portfolio.reconciliation_ok is not True
        or portfolio.broker_state_known is not True
    ):
        raise PaperExecutionRiskContractBlocked(
            "first W87 PAPER canary requires flat, reconciled, broker-known durable portfolio"
        )


def _validate_decision(
    *,
    decision: RiskDecision,
    intent: OrderIntent,
    market: MarketSnapshot,
    admission: PaperExecutionAdmissionReceipt,
    now: datetime,
) -> None:
    if (
        decision.intent_id != intent.intent_id
        or decision.intent_fingerprint != intent_fingerprint(intent)
        or decision.market_fingerprint != market_fingerprint(market)
        or decision.status is not RiskDecisionStatus.APPROVED
        or decision.approved_notional != admission.canary_notional_usd
        or decision.approved_notional
        != intent.quantity * admission.conservative_limit_price
        or decision.risk_reducing is not False
    ):
        raise PaperExecutionRiskContractIntegrityError(
            "Capital Safety approval is not bound to exact W87 intent/market/notional"
        )
    if not (_utc(decision.evaluated_at) == now < _utc(decision.valid_until)):
        raise PaperExecutionRiskContractBlocked(
            "Capital Safety approval is not currently valid"
        )


def _payload(
    receipt: PaperExecutionRiskContractReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    values = {
        name: getattr(receipt, name)
        for name in PaperExecutionRiskContractReceipt.__dataclass_fields__
        if name != "receipt_hash"
    }
    payload = _payload_values(values)
    if include_hash:
        payload["receipt_hash"] = receipt.receipt_hash
    return payload


def _payload_values(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            result[key] = _utc(value).isoformat()
        elif isinstance(value, Decimal):
            result[key] = str(value)
        elif isinstance(value, StrEnum):
            result[key] = value.value
        else:
            result[key] = value
    return result


def _hash(payload: Mapping[str, object]) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(raw).hexdigest()


def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperExecutionRiskContractIntegrityError(
            f"{name} must be canonical identifier"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperExecutionRiskContractIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _candidate_symbol(value: str) -> None:
    if (
        not isinstance(value, str)
        or value.count("-") != 1
        or value != value.upper()
        or any(not part for part in value.split("-"))
    ):
        raise PaperExecutionRiskContractIntegrityError(
            "candidate_symbol must be canonical BASE-QUOTE"
        )


def _broker_pair(value: str) -> None:
    if (
        not isinstance(value, str)
        or value.count("/") != 1
        or value != value.upper()
        or any(not part for part in value.split("/"))
    ):
        raise PaperExecutionRiskContractIntegrityError(
            "broker_pair must be canonical BASE/QUOTE"
        )


def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise PaperExecutionRiskContractIntegrityError(
            f"{name} must be finite positive Decimal"
        )


def _aware(value: datetime, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PaperExecutionRiskContractIntegrityError(
            f"{name} must be timezone-aware datetime"
        )


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)
