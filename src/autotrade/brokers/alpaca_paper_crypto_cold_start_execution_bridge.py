from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256

from autotrade.cold_start_oms import (
    COLD_START_OMS_SCOPE,
    ColdStartExternalSubmissionHandoff,
    ColdStartOmsStageAuthorization,
    ColdStartOmsStageAuthority,
    ColdStartOrderManagementSystem,
)
from autotrade.domain import (
    MarketSnapshot,
    OrderRecord,
    RiskDecision,
    intent_fingerprint,
    market_fingerprint,
    risk_decision_fingerprint,
)

from .alpaca_paper_crypto_canary_coordinator import PreparedCryptoPaperCanaryPackage
from .alpaca_paper_crypto_cold_start_execution_attempt import (
    CryptoColdStartExecutionAttemptCheckpoint,
)
from .alpaca_paper_crypto_cold_start_final_guard import (
    COLD_START_MAX_NOTIONAL,
    COLD_START_MIN_NOTIONAL,
    COLD_START_SCOPE,
    SQLiteCryptoColdStartAuthorityProvider,
)
from .alpaca_paper_crypto_operator_decision import (
    CryptoOperatorDecision,
    CryptoOperatorDecisionContext,
    CryptoOperatorDecisionStatus,
    SQLiteCryptoOperatorDecisionRegistry,
)


class CryptoColdStartExecutionBridgeError(RuntimeError):
    pass


class CryptoColdStartExecutionBridgeBlocked(CryptoColdStartExecutionBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoColdStartOmsStageContext:
    package: PreparedCryptoPaperCanaryPackage
    operator_decision: CryptoOperatorDecision
    checkpoint: CryptoColdStartExecutionAttemptCheckpoint
    consumed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.package, PreparedCryptoPaperCanaryPackage):
            raise ValueError("cold-start stage context requires prepared package")
        if not isinstance(self.operator_decision, CryptoOperatorDecision):
            raise ValueError("cold-start stage context requires operator decision")
        if not isinstance(self.checkpoint, CryptoColdStartExecutionAttemptCheckpoint):
            raise ValueError("cold-start stage context requires durable checkpoint")
        _require_aware(self.consumed_at, "consumed_at")


@dataclass(frozen=True, slots=True)
class CryptoColdStartExecutionStageResult:
    package_hash: str
    operator_decision_hash: str
    attempt_id: str
    checkpoint_hash: str
    order: OrderRecord
    handoff: ColdStartExternalSubmissionHandoff


class CryptoColdStartExecutionBridge(ColdStartOmsStageAuthority):
    """No-network crypto authority feeding an OMS-owned cold-start handoff.

    The bridge validates the dedicated PRE_CONSUME checkpoint, consumes the
    exact one-shot human decision, and asks `ColdStartOrderManagementSystem` to
    own the ledger handoff plus VALIDATED->SUBMITTING transition. This module
    never updates OrderStore directly and never constructs SUBMITTING state.
    """

    def __init__(
        self,
        *,
        oms: ColdStartOrderManagementSystem,
        authority_provider: SQLiteCryptoColdStartAuthorityProvider,
    ) -> None:
        if not isinstance(oms, ColdStartOrderManagementSystem):
            raise TypeError("cold-start bridge requires ColdStartOrderManagementSystem")
        if not isinstance(authority_provider, SQLiteCryptoColdStartAuthorityProvider):
            raise TypeError("cold-start bridge requires authoritative core provider")
        self._oms = oms
        self._authority = authority_provider

    def stage_after_checkpoint(
        self,
        *,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        operator_registry: SQLiteCryptoOperatorDecisionRegistry,
        checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
        risk_decision: RiskDecision,
        market: MarketSnapshot,
        consume_at: datetime,
        stage_at: datetime,
    ) -> CryptoColdStartExecutionStageResult:
        if not isinstance(package, PreparedCryptoPaperCanaryPackage):
            raise CryptoColdStartExecutionBridgeBlocked("prepared crypto PAPER package is required")
        if not isinstance(operator_decision, CryptoOperatorDecision):
            raise CryptoColdStartExecutionBridgeBlocked("cold-start operator decision is required")
        if not isinstance(operator_registry, SQLiteCryptoOperatorDecisionRegistry):
            raise CryptoColdStartExecutionBridgeBlocked("authoritative operator registry is required")
        if not isinstance(checkpoint, CryptoColdStartExecutionAttemptCheckpoint):
            raise CryptoColdStartExecutionBridgeBlocked("cold-start PRE_CONSUME checkpoint is required")
        if not isinstance(risk_decision, RiskDecision) or not isinstance(market, MarketSnapshot):
            raise CryptoColdStartExecutionBridgeBlocked("exact RiskDecision and MarketSnapshot are required")
        _require_aware(consume_at, "consume_at")
        _require_aware(stage_at, "stage_at")
        consume_instant = consume_at.astimezone(timezone.utc)
        stage_instant = stage_at.astimezone(timezone.utc)
        if consume_instant > stage_instant:
            raise CryptoColdStartExecutionBridgeBlocked("consumption cannot occur after staging")
        if stage_instant >= package.execution_deadline.astimezone(timezone.utc):
            raise CryptoColdStartExecutionBridgeBlocked("prepared package expired before cold-start staging")
        if package.network_write_authorized is not False or package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise CryptoColdStartExecutionBridgeBlocked("prepared package must remain non-executable")
        if not COLD_START_MIN_NOTIONAL <= package.notional <= COLD_START_MAX_NOTIONAL:
            raise CryptoColdStartExecutionBridgeBlocked("cold-start staging is limited to USD 1-5")

        self._validate_checkpoint(
            package=package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
        )
        before = self._authority.snapshot()
        if before.state_fingerprint != checkpoint.authority_state_fingerprint:
            raise CryptoColdStartExecutionBridgeBlocked("authoritative cold-start core changed after PRE_CONSUME")
        expected_snapshot = (
            "r6-crypto-paper-cold-start:"
            f"{checkpoint.pre_consume.account_reference[:20]}"
        )
        if checkpoint.pre_consume.portfolio_snapshot_id != expected_snapshot:
            raise CryptoColdStartExecutionBridgeBlocked(
                "cold-start Portfolio snapshot is not bound to exact PAPER account"
            )
        _validate_decision_package(package=package, risk_decision=risk_decision, market=market)

        expected_context = CryptoOperatorDecisionContext.from_prepared_package(
            package,
            attempt_id=checkpoint.attempt_id,
        )
        if operator_decision.context != expected_context:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision does not bind exact package")
        try:
            durable = operator_registry.get(expected_context.preparation_hash)
        except Exception as exc:
            raise CryptoColdStartExecutionBridgeBlocked("durable operator decision unavailable") from exc
        if durable.decision != operator_decision:
            raise CryptoColdStartExecutionBridgeBlocked("supplied operator decision differs from durable evidence")
        if durable.status is CryptoOperatorDecisionStatus.CONSUMED:
            if durable.consumed_attempt_id != checkpoint.attempt_id:
                raise CryptoColdStartExecutionBridgeBlocked("operator decision consumed by another attempt")
        elif durable.status is CryptoOperatorDecisionStatus.ISSUED:
            if not operator_decision.is_valid_at(consume_instant):
                raise CryptoColdStartExecutionBridgeBlocked("operator decision expired before consumption")
        else:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision state is not resumable")

        try:
            consumed = operator_registry.consume(
                decision=operator_decision,
                attempt_id=checkpoint.attempt_id,
                now=consume_instant,
            )
        except Exception as exc:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision consumption failed") from exc
        if (
            consumed.status is not CryptoOperatorDecisionStatus.CONSUMED
            or consumed.consumed_attempt_id != checkpoint.attempt_id
            or consumed.consumed_at is None
        ):
            raise CryptoColdStartExecutionBridgeBlocked("operator decision was not durably consumed")

        context = CryptoColdStartOmsStageContext(
            package=package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
            consumed_at=consumed.consumed_at,
        )
        try:
            order, handoff = self._oms.stage_cold_start_external_submission(
                order_id=package.order_id,
                decision=risk_decision,
                market=market,
                now=stage_instant,
                authority=self,
                authority_context=context,
            )
        except Exception as exc:
            raise CryptoColdStartExecutionBridgeBlocked("OMS-owned cold-start staging failed") from exc

        after = self._authority.snapshot()
        if after.state_fingerprint != checkpoint.authority_state_fingerprint:
            raise CryptoColdStartExecutionBridgeBlocked(
                "authoritative cold-start core changed during OMS staging"
            )
        if handoff.authorization_id != crypto_cold_start_handoff_id(
            package=package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
        ):
            raise CryptoColdStartExecutionBridgeBlocked("OMS cold-start handoff id mismatch")
        if handoff.checkpoint_hash != checkpoint.record_hash:
            raise CryptoColdStartExecutionBridgeBlocked("OMS cold-start handoff checkpoint mismatch")
        return CryptoColdStartExecutionStageResult(
            package_hash=package.package_hash,
            operator_decision_hash=operator_decision.decision_hash,
            attempt_id=checkpoint.attempt_id,
            checkpoint_hash=checkpoint.record_hash,
            order=order,
            handoff=handoff,
        )

    def authorize_oms_stage(
        self,
        *,
        order: OrderRecord,
        decision: RiskDecision,
        market: MarketSnapshot,
        now: datetime,
        context: object,
    ) -> ColdStartOmsStageAuthorization:
        if not isinstance(context, CryptoColdStartOmsStageContext):
            raise CryptoColdStartExecutionBridgeBlocked("cold-start OMS stage context is invalid")
        package = context.package
        operator_decision = context.operator_decision
        checkpoint = context.checkpoint
        self._validate_checkpoint(
            package=package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
        )
        authority = self._authority.snapshot()
        if authority.state_fingerprint != checkpoint.authority_state_fingerprint:
            raise CryptoColdStartExecutionBridgeBlocked("cold-start core changed before OMS authorization")
        expected_snapshot = f"r6-crypto-paper-cold-start:{checkpoint.pre_consume.account_reference[:20]}"
        if authority.portfolio_snapshot_id != expected_snapshot:
            raise CryptoColdStartExecutionBridgeBlocked("authoritative Portfolio snapshot/account mismatch")
        if order.order_id != package.order_id or order.risk_decision_id != package.risk_decision_id:
            raise CryptoColdStartExecutionBridgeBlocked("OMS order differs from prepared package")
        if intent_fingerprint(order.intent) != package.intent_fingerprint:
            raise CryptoColdStartExecutionBridgeBlocked("OMS intent differs from prepared package")
        _validate_decision_package(package=package, risk_decision=decision, market=market)
        if context.consumed_at > now.astimezone(timezone.utc):
            raise CryptoColdStartExecutionBridgeBlocked("operator consumption is future-dated")
        authorization_id = crypto_cold_start_handoff_id(
            package=package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
        )
        return self._issue_authorization(
            authorization_id=authorization_id,
            package_hash=package.package_hash,
            operator_decision_hash=operator_decision.decision_hash,
            checkpoint_hash=checkpoint.record_hash,
            authority_state_fingerprint=checkpoint.authority_state_fingerprint,
            attempt_id=checkpoint.attempt_id,
            order_id=package.order_id,
            client_order_id=package.client_order_id,
            intent_fingerprint_value=package.intent_fingerprint,
            risk_decision_id=package.risk_decision_id,
            market_fingerprint_value=package.market_fingerprint,
            safety_state_version=authority.safety_state_version,
            authorized_at=context.consumed_at,
        )

    @staticmethod
    def _validate_checkpoint(
        *,
        package: PreparedCryptoPaperCanaryPackage,
        operator_decision: CryptoOperatorDecision,
        checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
    ) -> None:
        if checkpoint.package_hash != package.package_hash:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint package mismatch")
        if checkpoint.preparation_hash != operator_decision.context.preparation_hash:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint preparation mismatch")
        if checkpoint.operator_decision_hash != operator_decision.decision_hash:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint decision hash mismatch")
        if checkpoint.attempt_id != operator_decision.context.attempt_id:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint attempt mismatch")
        if checkpoint.order_id != package.order_id or checkpoint.client_order_id != package.client_order_id:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint order identity mismatch")
        if checkpoint.pre_consume.bootstrap_scope != COLD_START_SCOPE:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint is outside cold-start scope")
        if COLD_START_OMS_SCOPE != COLD_START_SCOPE:
            raise CryptoColdStartExecutionBridgeBlocked("core/broker cold-start scope drift")


def crypto_cold_start_handoff_id(
    *,
    package: PreparedCryptoPaperCanaryPackage,
    operator_decision: CryptoOperatorDecision,
    checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
) -> str:
    if checkpoint.package_hash != package.package_hash:
        raise CryptoColdStartExecutionBridgeBlocked("cannot derive handoff from mismatched package")
    if checkpoint.operator_decision_hash != operator_decision.decision_hash:
        raise CryptoColdStartExecutionBridgeBlocked("cannot derive handoff from mismatched decision")
    material = "|".join(
        (
            "R6_CRYPTO_COLD_START_EXECUTION_HANDOFF",
            COLD_START_SCOPE,
            package.package_hash,
            operator_decision.decision_hash,
            checkpoint.record_hash,
            checkpoint.authority_state_fingerprint,
            checkpoint.attempt_id,
            package.order_id,
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _validate_decision_package(
    *,
    package: PreparedCryptoPaperCanaryPackage,
    risk_decision: RiskDecision,
    market: MarketSnapshot,
) -> None:
    if risk_decision.decision_id != package.risk_decision_id:
        raise CryptoColdStartExecutionBridgeBlocked("RiskDecision id mismatch")
    if risk_decision.valid_until != package.risk_decision_valid_until:
        raise CryptoColdStartExecutionBridgeBlocked("RiskDecision expiry mismatch")
    if risk_decision.safety_state_version != package.risk_decision_safety_state_version:
        raise CryptoColdStartExecutionBridgeBlocked("temporary RiskDecision Safety version mismatch")
    if risk_decision.market_fingerprint != package.market_fingerprint:
        raise CryptoColdStartExecutionBridgeBlocked("RiskDecision market mismatch")
    if risk_decision.intent_fingerprint != package.intent_fingerprint:
        raise CryptoColdStartExecutionBridgeBlocked("RiskDecision intent mismatch")
    if risk_decision_fingerprint(risk_decision) != package.risk_decision_fingerprint:
        raise CryptoColdStartExecutionBridgeBlocked("RiskDecision fingerprint mismatch")
    if market_fingerprint(market) != package.market_fingerprint:
        raise CryptoColdStartExecutionBridgeBlocked("MarketSnapshot mismatch")


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"cold-start execution bridge {label} must be timezone-aware")


__all__ = [
    "CryptoColdStartExecutionBridge",
    "CryptoColdStartExecutionBridgeBlocked",
    "CryptoColdStartExecutionBridgeError",
    "CryptoColdStartExecutionStageResult",
    "CryptoColdStartOmsStageContext",
    "crypto_cold_start_handoff_id",
]
