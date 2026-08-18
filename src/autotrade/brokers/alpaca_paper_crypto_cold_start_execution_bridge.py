from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from autotrade.domain import (
    MarketSnapshot,
    OrderRecord,
    OrderStatus,
    RiskDecision,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.ledger import DuplicateLedgerEvent, EventLedger, LedgerEvent
from autotrade.state import OrderStore

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


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class CryptoColdStartExecutionBridgeError(RuntimeError):
    pass


class CryptoColdStartExecutionBridgeBlocked(CryptoColdStartExecutionBridgeError):
    pass


@dataclass(frozen=True, slots=True)
class CryptoColdStartExternalHandoff:
    handoff_id: str
    package_hash: str
    operator_decision_hash: str
    checkpoint_hash: str
    authority_state_fingerprint: str
    attempt_id: str
    order_id: str
    client_order_id: str
    risk_decision_id: str
    market_fingerprint: str
    authorized_at: datetime
    event_id: str
    handoff_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("handoff_id", self.handoff_id),
            ("package_hash", self.package_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("checkpoint_hash", self.checkpoint_hash),
            ("authority_state_fingerprint", self.authority_state_fingerprint),
            ("market_fingerprint", self.market_fingerprint),
            ("handoff_hash", self.handoff_hash),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        _require_aware(self.authorized_at, "authorized_at")
        if self.event_id != f"cold-start-external-handoff:{self.order_id}:{self.handoff_id}":
            raise ValueError("cold-start handoff event_id mismatch")
        if self.handoff_hash != _handoff_hash_values(_handoff_values(self, include_hash=False)):
            raise ValueError("cold-start handoff hash mismatch")

    def event_payload(self) -> dict[str, str]:
        return {
            "scope": COLD_START_SCOPE,
            "handoff_id": self.handoff_id,
            "package_hash": self.package_hash,
            "operator_decision_hash": self.operator_decision_hash,
            "checkpoint_hash": self.checkpoint_hash,
            "authority_state_fingerprint": self.authority_state_fingerprint,
            "attempt_id": self.attempt_id,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "risk_decision_id": self.risk_decision_id,
            "market_fingerprint": self.market_fingerprint,
            "authorized_at": _iso(self.authorized_at),
            "handoff_hash": self.handoff_hash,
        }


@dataclass(frozen=True, slots=True)
class CryptoColdStartExecutionStageResult:
    package_hash: str
    operator_decision_hash: str
    attempt_id: str
    checkpoint_hash: str
    order: OrderRecord
    handoff: CryptoColdStartExternalHandoff

    def __post_init__(self) -> None:
        if self.order.status is not OrderStatus.SUBMITTING:
            raise ValueError("cold-start bridge result requires SUBMITTING")
        if self.order.order_id != self.handoff.order_id:
            raise ValueError("cold-start bridge order/handoff mismatch")


class CryptoColdStartExecutionBridge:
    """No-network bridge for exactly one isolated cold-start PAPER canary.

    The normal OMS handoff remains untouched and continues to require Health
    NORMAL plus an inactive kill switch. This bridge can only consume the exact
    human decision already bound to a dedicated cold-start PRE_CONSUME checkpoint,
    persist a tamper-evident local handoff, and move the same durable order from
    VALIDATED to SUBMITTING. It has no credentials, transport, broker or POST API.
    """

    def __init__(
        self,
        *,
        order_store: OrderStore,
        ledger: EventLedger,
        authority_provider: SQLiteCryptoColdStartAuthorityProvider,
    ) -> None:
        self._orders = order_store
        self._ledger = ledger
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
        requested_stage = stage_at.astimezone(timezone.utc)
        if consume_instant > requested_stage:
            raise CryptoColdStartExecutionBridgeBlocked("consumption cannot occur after staging")
        if requested_stage >= package.execution_deadline.astimezone(timezone.utc):
            raise CryptoColdStartExecutionBridgeBlocked("prepared package expired before cold-start staging")
        if package.network_write_authorized is not False:
            raise CryptoColdStartExecutionBridgeBlocked("prepared package must remain non-executable")
        if package.next_action != "OPERATOR_DECISION_REQUIRED":
            raise CryptoColdStartExecutionBridgeBlocked("prepared package must still require operator decision")
        if not COLD_START_MIN_NOTIONAL <= package.notional <= COLD_START_MAX_NOTIONAL:
            raise CryptoColdStartExecutionBridgeBlocked("cold-start staging is limited to USD 1-5")

        attempt_id = operator_decision.context.attempt_id
        if checkpoint.attempt_id != attempt_id:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint attempt mismatch")
        if checkpoint.package_hash != package.package_hash:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint package mismatch")
        if checkpoint.preparation_hash != operator_decision.context.preparation_hash:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint preparation mismatch")
        if checkpoint.operator_decision_hash != operator_decision.decision_hash:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint decision hash mismatch")
        if checkpoint.order_id != package.order_id or checkpoint.client_order_id != package.client_order_id:
            raise CryptoColdStartExecutionBridgeBlocked("checkpoint order identity mismatch")

        before = self._authority.snapshot()
        if before.state_fingerprint != checkpoint.authority_state_fingerprint:
            raise CryptoColdStartExecutionBridgeBlocked("authoritative cold-start core changed after PRE_CONSUME")

        _validate_decision_package(
            package=package,
            risk_decision=risk_decision,
            market=market,
        )
        expected_context = CryptoOperatorDecisionContext.from_prepared_package(
            package,
            attempt_id=attempt_id,
        )
        if operator_decision.context != expected_context:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision does not bind exact package")
        if operator_decision.issued_at < package.prepared_at or operator_decision.issued_at >= package.execution_deadline:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision timing is outside prepared package")

        try:
            durable = operator_registry.get(expected_context.preparation_hash)
        except Exception as exc:
            raise CryptoColdStartExecutionBridgeBlocked("durable operator decision unavailable") from exc
        if durable.decision != operator_decision:
            raise CryptoColdStartExecutionBridgeBlocked("supplied operator decision differs from durable evidence")
        if durable.status is CryptoOperatorDecisionStatus.CONSUMED:
            if durable.consumed_attempt_id != attempt_id:
                raise CryptoColdStartExecutionBridgeBlocked("operator decision consumed by another attempt")
        elif durable.status is CryptoOperatorDecisionStatus.ISSUED:
            if not operator_decision.is_valid_at(consume_instant):
                raise CryptoColdStartExecutionBridgeBlocked("operator decision expired before consumption")
        else:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision state is not resumable")

        current = self._orders.get_by_order_id(package.order_id)
        if current is None:
            raise CryptoColdStartExecutionBridgeBlocked("durable cold-start OMS order is missing")
        if current.risk_decision_id != package.risk_decision_id:
            raise CryptoColdStartExecutionBridgeBlocked("durable order RiskDecision mismatch")
        if current.status not in {OrderStatus.VALIDATED, OrderStatus.SUBMITTING}:
            raise CryptoColdStartExecutionBridgeBlocked(
                f"cold-start staging cannot resume from {current.status.value}"
            )

        try:
            consumed = operator_registry.consume(
                decision=operator_decision,
                attempt_id=attempt_id,
                now=consume_instant,
            )
        except Exception as exc:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision consumption failed") from exc
        if consumed.status is not CryptoOperatorDecisionStatus.CONSUMED or consumed.consumed_attempt_id != attempt_id:
            raise CryptoColdStartExecutionBridgeBlocked("operator decision was not durably consumed")

        handoff_id = crypto_cold_start_handoff_id(
            package=package,
            operator_decision=operator_decision,
            checkpoint=checkpoint,
        )
        event_id = f"cold-start-external-handoff:{package.order_id}:{handoff_id}"
        existing = _find_handoff(self._ledger, event_id)
        if existing is None:
            if current.status is not OrderStatus.VALIDATED:
                raise CryptoColdStartExecutionBridgeBlocked("SUBMITTING cannot exist without durable cold-start handoff")
            handoff = _build_handoff(
                handoff_id=handoff_id,
                package=package,
                operator_decision=operator_decision,
                checkpoint=checkpoint,
                market=market,
                authorized_at=requested_stage,
            )
            _append_handoff_idempotent(self._ledger, handoff)
        else:
            handoff = existing
            _validate_handoff_binding(
                handoff=handoff,
                handoff_id=handoff_id,
                package=package,
                operator_decision=operator_decision,
                checkpoint=checkpoint,
                market=market,
            )

        if current.status is OrderStatus.VALIDATED:
            staged = replace(current, status=OrderStatus.SUBMITTING, submitted_at=handoff.authorized_at)
            self._orders.update(staged)
        else:
            if current.submitted_at != handoff.authorized_at:
                raise CryptoColdStartExecutionBridgeBlocked("SUBMITTING timestamp differs from durable cold-start handoff")
            staged = current

        after = self._authority.snapshot()
        if after.state_fingerprint != checkpoint.authority_state_fingerprint:
            raise CryptoColdStartExecutionBridgeBlocked(
                "authoritative cold-start core changed during no-network staging"
            )
        return CryptoColdStartExecutionStageResult(
            package_hash=package.package_hash,
            operator_decision_hash=operator_decision.decision_hash,
            attempt_id=attempt_id,
            checkpoint_hash=checkpoint.record_hash,
            order=staged,
            handoff=handoff,
        )


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


def _build_handoff(
    *,
    handoff_id: str,
    package: PreparedCryptoPaperCanaryPackage,
    operator_decision: CryptoOperatorDecision,
    checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
    market: MarketSnapshot,
    authorized_at: datetime,
) -> CryptoColdStartExternalHandoff:
    values: dict[str, object] = {
        "handoff_id": handoff_id,
        "package_hash": package.package_hash,
        "operator_decision_hash": operator_decision.decision_hash,
        "checkpoint_hash": checkpoint.record_hash,
        "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
        "attempt_id": checkpoint.attempt_id,
        "order_id": package.order_id,
        "client_order_id": package.client_order_id,
        "risk_decision_id": package.risk_decision_id,
        "market_fingerprint": market_fingerprint(market),
        "authorized_at": authorized_at.astimezone(timezone.utc),
        "event_id": f"cold-start-external-handoff:{package.order_id}:{handoff_id}",
    }
    return CryptoColdStartExternalHandoff(
        **values,  # type: ignore[arg-type]
        handoff_hash=_handoff_hash_values(values),
    )


def _validate_handoff_binding(
    *,
    handoff: CryptoColdStartExternalHandoff,
    handoff_id: str,
    package: PreparedCryptoPaperCanaryPackage,
    operator_decision: CryptoOperatorDecision,
    checkpoint: CryptoColdStartExecutionAttemptCheckpoint,
    market: MarketSnapshot,
) -> None:
    expected = {
        "handoff_id": handoff_id,
        "package_hash": package.package_hash,
        "operator_decision_hash": operator_decision.decision_hash,
        "checkpoint_hash": checkpoint.record_hash,
        "authority_state_fingerprint": checkpoint.authority_state_fingerprint,
        "attempt_id": checkpoint.attempt_id,
        "order_id": package.order_id,
        "client_order_id": package.client_order_id,
        "risk_decision_id": package.risk_decision_id,
        "market_fingerprint": market_fingerprint(market),
    }
    actual = {key: getattr(handoff, key) for key in expected}
    if actual != expected:
        raise CryptoColdStartExecutionBridgeBlocked("durable cold-start handoff binding mismatch")


def _append_handoff_idempotent(ledger: EventLedger, handoff: CryptoColdStartExternalHandoff) -> None:
    event = LedgerEvent(
        event_id=handoff.event_id,
        event_type="COLD_START_EXTERNAL_HANDOFF_AUTHORIZED",
        occurred_at=handoff.authorized_at,
        payload=handoff.event_payload(),
    )
    try:
        ledger.append(event)
    except DuplicateLedgerEvent:
        existing = _find_handoff(ledger, handoff.event_id)
        if existing != handoff:
            raise CryptoColdStartExecutionBridgeBlocked("cold-start handoff ledger identity conflict")


def _find_handoff(ledger: EventLedger, event_id: str) -> CryptoColdStartExternalHandoff | None:
    matches = tuple(event for event in ledger.all_events() if event.event_id == event_id)
    if not matches:
        return None
    if len(matches) != 1:
        raise CryptoColdStartExecutionBridgeBlocked("duplicate cold-start handoff ledger identity")
    event = matches[0]
    if event.event_type != "COLD_START_EXTERNAL_HANDOFF_AUTHORIZED":
        raise CryptoColdStartExecutionBridgeBlocked("cold-start handoff event type mismatch")
    payload = dict(event.payload)
    expected_keys = {
        "scope",
        "handoff_id",
        "package_hash",
        "operator_decision_hash",
        "checkpoint_hash",
        "authority_state_fingerprint",
        "attempt_id",
        "order_id",
        "client_order_id",
        "risk_decision_id",
        "market_fingerprint",
        "authorized_at",
        "handoff_hash",
    }
    if set(payload) != expected_keys or payload.get("scope") != COLD_START_SCOPE:
        raise CryptoColdStartExecutionBridgeBlocked("cold-start handoff payload is non-canonical")
    handoff = CryptoColdStartExternalHandoff(
        handoff_id=str(payload["handoff_id"]),
        package_hash=str(payload["package_hash"]),
        operator_decision_hash=str(payload["operator_decision_hash"]),
        checkpoint_hash=str(payload["checkpoint_hash"]),
        authority_state_fingerprint=str(payload["authority_state_fingerprint"]),
        attempt_id=str(payload["attempt_id"]),
        order_id=str(payload["order_id"]),
        client_order_id=str(payload["client_order_id"]),
        risk_decision_id=str(payload["risk_decision_id"]),
        market_fingerprint=str(payload["market_fingerprint"]),
        authorized_at=datetime.fromisoformat(str(payload["authorized_at"])),
        event_id=event.event_id,
        handoff_hash=str(payload["handoff_hash"]),
    )
    if event.occurred_at != handoff.authorized_at:
        raise CryptoColdStartExecutionBridgeBlocked("cold-start handoff event timestamp mismatch")
    return handoff


def _handoff_values(
    handoff: CryptoColdStartExternalHandoff,
    *,
    include_hash: bool,
) -> dict[str, object]:
    values: dict[str, object] = {
        "handoff_id": handoff.handoff_id,
        "package_hash": handoff.package_hash,
        "operator_decision_hash": handoff.operator_decision_hash,
        "checkpoint_hash": handoff.checkpoint_hash,
        "authority_state_fingerprint": handoff.authority_state_fingerprint,
        "attempt_id": handoff.attempt_id,
        "order_id": handoff.order_id,
        "client_order_id": handoff.client_order_id,
        "risk_decision_id": handoff.risk_decision_id,
        "market_fingerprint": handoff.market_fingerprint,
        "authorized_at": handoff.authorized_at.astimezone(timezone.utc),
        "event_id": handoff.event_id,
    }
    if include_hash:
        values["handoff_hash"] = handoff.handoff_hash
    return values


def _handoff_hash_values(values: dict[str, object]) -> str:
    payload = dict(values)
    authorized_at = payload.get("authorized_at")
    if isinstance(authorized_at, datetime):
        payload["authorized_at"] = _iso(authorized_at)
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _iso(value: datetime) -> str:
    _require_aware(value, "timestamp")
    return value.astimezone(timezone.utc).isoformat()


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"cold-start execution bridge {label} must be timezone-aware")


__all__ = [
    "CryptoColdStartExecutionBridge",
    "CryptoColdStartExecutionBridgeBlocked",
    "CryptoColdStartExecutionBridgeError",
    "CryptoColdStartExecutionStageResult",
    "CryptoColdStartExternalHandoff",
    "crypto_cold_start_handoff_id",
]
