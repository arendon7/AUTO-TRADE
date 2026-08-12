from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

from .alpaca_paper_bracket import (
    AlpacaEquityBracketRequest,
    AlpacaNestedBracketAttestation,
)
from .alpaca_paper_submission import (
    PaperSubmissionEventType,
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from .alpaca_paper_trade_updates import (
    PaperTradeUpdateEvent,
    PaperTradeUpdateEventType,
    SQLitePaperTradeUpdateLedger,
)


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PaperQualificationError(RuntimeError):
    pass


class PaperQualificationRejected(PaperQualificationError):
    def __init__(self, reasons: list[str] | tuple[str, ...]) -> None:
        self.reasons = tuple(reasons)
        super().__init__("; ".join(self.reasons))


class PaperQualificationIntegrityError(PaperQualificationError):
    pass


@dataclass(frozen=True, slots=True)
class PaperQualificationPolicy:
    max_adverse_slippage_bps: Decimal = Decimal("50")
    max_submit_to_fill_seconds: Decimal = Decimal("120")

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_adverse_slippage_bps, Decimal)
            or not self.max_adverse_slippage_bps.is_finite()
            or not Decimal("0") <= self.max_adverse_slippage_bps <= Decimal("500")
        ):
            raise ValueError("max_adverse_slippage_bps must be finite between 0 and 500")
        if (
            not isinstance(self.max_submit_to_fill_seconds, Decimal)
            or not self.max_submit_to_fill_seconds.is_finite()
            or not Decimal("0") < self.max_submit_to_fill_seconds <= Decimal("3600")
        ):
            raise ValueError("max_submit_to_fill_seconds must be finite > 0 and <= 3600")


@dataclass(frozen=True, slots=True)
class PaperQualificationReport:
    order_id: str
    client_order_id: str
    parent_broker_order_id: str
    take_profit_broker_order_id: str
    stop_loss_broker_order_id: str
    order_payload_hash: str
    submission_binding_hash: str
    submission_control_hash: str
    submission_event_head_hash: str
    trade_update_scope_hash: str
    trade_update_head_hash: str
    trade_update_control_hash: str
    expected_quantity: Decimal
    filled_quantity: Decimal
    fill_count: int
    average_fill_price: Decimal
    benchmark_limit_price: Decimal
    signed_slippage_bps: Decimal
    adverse_slippage_bps: Decimal
    submit_to_fill_seconds: Decimal
    submit_attempt_at: datetime
    reconciled_at: datetime
    terminal_fill_at: datetime
    evaluated_at: datetime
    policy_max_adverse_slippage_bps: Decimal
    policy_max_submit_to_fill_seconds: Decimal
    report_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("order_payload_hash", self.order_payload_hash),
            ("submission_binding_hash", self.submission_binding_hash),
            ("submission_control_hash", self.submission_control_hash),
            ("submission_event_head_hash", self.submission_event_head_hash),
            ("trade_update_scope_hash", self.trade_update_scope_hash),
            ("trade_update_head_hash", self.trade_update_head_hash),
            ("trade_update_control_hash", self.trade_update_control_hash),
            ("report_hash", self.report_hash),
        ):
            if not _HASH_RE.fullmatch(value):
                raise ValueError(f"{label} must be lowercase SHA-256")
        if self.fill_count <= 0:
            raise ValueError("fill_count must be > 0")
        for label, value in (
            ("expected_quantity", self.expected_quantity),
            ("filled_quantity", self.filled_quantity),
            ("average_fill_price", self.average_fill_price),
            ("benchmark_limit_price", self.benchmark_limit_price),
        ):
            if not _finite_positive(value):
                raise ValueError(f"{label} must be finite and > 0")
        if self.filled_quantity != self.expected_quantity:
            raise ValueError("filled_quantity must equal expected_quantity")
        for label, value in (
            ("signed_slippage_bps", self.signed_slippage_bps),
            ("adverse_slippage_bps", self.adverse_slippage_bps),
            ("submit_to_fill_seconds", self.submit_to_fill_seconds),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{label} must be finite")
        if self.adverse_slippage_bps < 0 or self.submit_to_fill_seconds < 0:
            raise ValueError("adverse slippage and latency cannot be negative")
        for label, value in (
            ("submit_attempt_at", self.submit_attempt_at),
            ("reconciled_at", self.reconciled_at),
            ("terminal_fill_at", self.terminal_fill_at),
            ("evaluated_at", self.evaluated_at),
        ):
            _require_aware(value, label)
        if self.terminal_fill_at < self.submit_attempt_at:
            raise ValueError("terminal fill cannot precede submit attempt")
        if self.reconciled_at < self.submit_attempt_at:
            raise ValueError("reconciliation cannot precede submit attempt")
        if self.evaluated_at < max(self.reconciled_at, self.terminal_fill_at):
            raise ValueError("evaluation cannot precede evidence")

    @property
    def fingerprint(self) -> str:
        return _report_hash(self._payload_without_hash())

    def _payload_without_hash(self) -> dict[str, object]:
        return {
            "adverse_slippage_bps": _decimal_text(self.adverse_slippage_bps),
            "average_fill_price": _decimal_text(self.average_fill_price),
            "benchmark_limit_price": _decimal_text(self.benchmark_limit_price),
            "client_order_id": self.client_order_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "expected_quantity": _decimal_text(self.expected_quantity),
            "fill_count": self.fill_count,
            "filled_quantity": _decimal_text(self.filled_quantity),
            "order_id": self.order_id,
            "order_payload_hash": self.order_payload_hash,
            "parent_broker_order_id": self.parent_broker_order_id,
            "policy_max_adverse_slippage_bps": _decimal_text(
                self.policy_max_adverse_slippage_bps
            ),
            "policy_max_submit_to_fill_seconds": _decimal_text(
                self.policy_max_submit_to_fill_seconds
            ),
            "reconciled_at": self.reconciled_at.isoformat(),
            "signed_slippage_bps": _decimal_text(self.signed_slippage_bps),
            "stop_loss_broker_order_id": self.stop_loss_broker_order_id,
            "submission_binding_hash": self.submission_binding_hash,
            "submission_control_hash": self.submission_control_hash,
            "submission_event_head_hash": self.submission_event_head_hash,
            "submit_attempt_at": self.submit_attempt_at.isoformat(),
            "submit_to_fill_seconds": _decimal_text(self.submit_to_fill_seconds),
            "take_profit_broker_order_id": self.take_profit_broker_order_id,
            "terminal_fill_at": self.terminal_fill_at.isoformat(),
            "trade_update_control_hash": self.trade_update_control_hash,
            "trade_update_head_hash": self.trade_update_head_hash,
            "trade_update_scope_hash": self.trade_update_scope_hash,
        }

    def to_dict(self) -> dict[str, object]:
        payload = self._payload_without_hash()
        payload.update(
            {
                "artifact_version": 1,
                "capital_authority": "NONE",
                "external_paper_qualified": True,
                "live_trading": "BLOCKED",
                "profitability_claim": False,
                "report_hash": self.report_hash,
            }
        )
        return payload

    def write(self, path: str | Path) -> None:
        if self.report_hash != self.fingerprint:
            raise PaperQualificationIntegrityError("qualification report hash mismatch")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = _canonical_json(self.to_dict()) + "\n"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(raw, encoding="utf-8")
        temporary.replace(target)

    @classmethod
    def read(cls, path: str | Path) -> "PaperQualificationReport":
        try:
            document = json.loads(
                Path(path).read_text(encoding="utf-8"),
                parse_constant=lambda token: _reject_constant(token),
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PaperQualificationIntegrityError("qualification artifact is unreadable") from exc
        if not isinstance(document, dict) or document.get("artifact_version") != 1:
            raise PaperQualificationIntegrityError("unsupported qualification artifact")
        if document.get("capital_authority") != "NONE":
            raise PaperQualificationIntegrityError("qualification artifact capital authority changed")
        if document.get("external_paper_qualified") is not True:
            raise PaperQualificationIntegrityError("qualification artifact status changed")
        if document.get("live_trading") != "BLOCKED" or document.get("profitability_claim") is not False:
            raise PaperQualificationIntegrityError("qualification artifact non-claims changed")
        try:
            report = cls(
                order_id=str(document["order_id"]),
                client_order_id=str(document["client_order_id"]),
                parent_broker_order_id=str(document["parent_broker_order_id"]),
                take_profit_broker_order_id=str(document["take_profit_broker_order_id"]),
                stop_loss_broker_order_id=str(document["stop_loss_broker_order_id"]),
                order_payload_hash=str(document["order_payload_hash"]),
                submission_binding_hash=str(document["submission_binding_hash"]),
                submission_control_hash=str(document["submission_control_hash"]),
                submission_event_head_hash=str(document["submission_event_head_hash"]),
                trade_update_scope_hash=str(document["trade_update_scope_hash"]),
                trade_update_head_hash=str(document["trade_update_head_hash"]),
                trade_update_control_hash=str(document["trade_update_control_hash"]),
                expected_quantity=Decimal(str(document["expected_quantity"])),
                filled_quantity=Decimal(str(document["filled_quantity"])),
                fill_count=int(document["fill_count"]),
                average_fill_price=Decimal(str(document["average_fill_price"])),
                benchmark_limit_price=Decimal(str(document["benchmark_limit_price"])),
                signed_slippage_bps=Decimal(str(document["signed_slippage_bps"])),
                adverse_slippage_bps=Decimal(str(document["adverse_slippage_bps"])),
                submit_to_fill_seconds=Decimal(str(document["submit_to_fill_seconds"])),
                submit_attempt_at=_timestamp(document["submit_attempt_at"]),
                reconciled_at=_timestamp(document["reconciled_at"]),
                terminal_fill_at=_timestamp(document["terminal_fill_at"]),
                evaluated_at=_timestamp(document["evaluated_at"]),
                policy_max_adverse_slippage_bps=Decimal(
                    str(document["policy_max_adverse_slippage_bps"])
                ),
                policy_max_submit_to_fill_seconds=Decimal(
                    str(document["policy_max_submit_to_fill_seconds"])
                ),
                report_hash=str(document["report_hash"]),
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise PaperQualificationIntegrityError("qualification artifact is malformed") from exc
        if report.report_hash != report.fingerprint:
            raise PaperQualificationIntegrityError("qualification artifact hash mismatch")
        return report


class AlpacaPaperQualificationEvaluator:
    """Offline deterministic qualification from already-durable PAPER evidence."""

    def __init__(self, policy: PaperQualificationPolicy | None = None) -> None:
        self.policy = policy or PaperQualificationPolicy()

    def qualify(
        self,
        *,
        expected_bracket: AlpacaEquityBracketRequest,
        bracket_attestation: AlpacaNestedBracketAttestation,
        submission_registry: SQLitePaperSubmissionRegistry,
        trade_update_ledger: SQLitePaperTradeUpdateLedger,
        evaluated_at: datetime,
    ) -> PaperQualificationReport:
        _require_aware(evaluated_at, "evaluated_at")
        evaluated_at = evaluated_at.astimezone(timezone.utc)
        reasons: list[str] = []

        submission = submission_registry.verified_global_snapshot(expected_bracket.order_id)
        binding = submission.binding
        state = submission.state
        if state.status is not PaperSubmissionStatus.ACKNOWLEDGED:
            reasons.append("submission is not durably ACKNOWLEDGED")
        if state.attempt_count != 1:
            reasons.append("qualification requires exactly one external submit attempt")
        if binding.client_order_id != expected_bracket.client_order_id:
            reasons.append("binding/bracket client_order_id mismatch")
        if binding.order_payload_hash != expected_bracket.payload_hash:
            reasons.append("binding/bracket payload hash mismatch")
        if state.broker_order_id != bracket_attestation.parent_order_id:
            reasons.append("submission/bracket parent broker order mismatch")
        if state.broker_client_order_id != bracket_attestation.client_order_id:
            reasons.append("submission/bracket broker client_order_id mismatch")
        if bracket_attestation.client_order_id != expected_bracket.client_order_id:
            reasons.append("attested bracket client_order_id mismatch")
        if any(
            other.order_id != expected_bracket.order_id and other.attempt_count > 0
            for other in submission.all_states
        ):
            reasons.append("first-canary qualification found another attempted PAPER submission")

        submit_events = [
            event
            for event in submission.events
            if event.event_type is PaperSubmissionEventType.SUBMIT_ATTEMPT_UNKNOWN
        ]
        ack_events = [
            event
            for event in submission.events
            if event.event_type is PaperSubmissionEventType.RECONCILED_ACKNOWLEDGED
        ]
        if len(submit_events) != 1:
            reasons.append("submission evidence must contain exactly one submit attempt")
        if len(ack_events) != 1:
            reasons.append("submission evidence must contain exactly one reconciled acknowledgement")

        ledger_state = trade_update_ledger.verify()
        trade_events = trade_update_ledger.events()
        scope = trade_update_ledger.scope
        expected_symbol = _payload_str(expected_bracket.canonical_payload, "symbol")
        if scope.symbol != expected_symbol:
            reasons.append("trade_updates scope symbol mismatch")
        if scope.parent_order_id != bracket_attestation.parent_order_id:
            reasons.append("trade_updates parent broker order mismatch")
        if scope.parent_client_order_id != bracket_attestation.client_order_id:
            reasons.append("trade_updates parent client_order_id mismatch")
        if scope.take_profit_order_id != bracket_attestation.take_profit_order_id:
            reasons.append("trade_updates take-profit broker order mismatch")
        if scope.stop_loss_order_id != bracket_attestation.stop_loss_order_id:
            reasons.append("trade_updates stop-loss broker order mismatch")

        expected_quantity = _payload_decimal(expected_bracket.canonical_payload, "qty")
        benchmark_limit = _payload_decimal(expected_bracket.canonical_payload, "limit_price")
        parent_events = [
            event for event in trade_events if event.broker_order_id == scope.parent_order_id
        ]
        if not parent_events:
            reasons.append("trade_updates has no parent order evidence")
        negative_terminal = [
            event
            for event in parent_events
            if event.event_type
            in {
                PaperTradeUpdateEventType.CANCELED,
                PaperTradeUpdateEventType.EXPIRED,
                PaperTradeUpdateEventType.REJECTED,
            }
        ]
        if negative_terminal:
            reasons.append("parent PAPER order terminated without qualification fill")
        fills = [event for event in parent_events if event.event_type in {PaperTradeUpdateEventType.PARTIAL_FILL, PaperTradeUpdateEventType.FILL}]
        terminal_fills = [
            event for event in parent_events if event.event_type is PaperTradeUpdateEventType.FILL
        ]
        if len(terminal_fills) != 1:
            reasons.append("parent order requires exactly one terminal fill event")
        if not fills:
            reasons.append("parent order has no fill evidence")

        filled_quantity = sum(
            (event.fill_qty or Decimal("0") for event in fills),
            Decimal("0"),
        )
        if filled_quantity != expected_quantity:
            reasons.append("aggregated execution fill quantity does not equal expected quantity")
        if ledger_state.parent_filled_qty != expected_quantity:
            reasons.append("cumulative broker parent filled quantity is incomplete")
        if terminal_fills and terminal_fills[0].filled_qty != expected_quantity:
            reasons.append("terminal fill cumulative quantity is incomplete")

        submit_at = submit_events[0].occurred_at.astimezone(timezone.utc) if submit_events else evaluated_at
        reconciled_at = ack_events[0].occurred_at.astimezone(timezone.utc) if ack_events else evaluated_at
        terminal_at = terminal_fills[0].occurred_at.astimezone(timezone.utc) if terminal_fills else evaluated_at
        if reconciled_at < submit_at:
            reasons.append("reconciliation acknowledgement precedes submit attempt")
        if terminal_at < submit_at:
            reasons.append("terminal fill precedes submit attempt")
        if evaluated_at < max(reconciled_at, terminal_at, ledger_state.last_event_at):
            reasons.append("qualification evaluated_at precedes durable evidence")

        weighted_notional = sum(
            (
                (event.fill_price or Decimal("0")) * (event.fill_qty or Decimal("0"))
                for event in fills
            ),
            Decimal("0"),
        )
        average_fill = (
            weighted_notional / filled_quantity
            if filled_quantity > 0
            else Decimal("0")
        )
        signed_slippage = (
            ((average_fill - benchmark_limit) / benchmark_limit) * Decimal("10000")
            if average_fill > 0 and benchmark_limit > 0
            else Decimal("0")
        )
        adverse_slippage = max(Decimal("0"), signed_slippage)
        latency = Decimal(str((terminal_at - submit_at).total_seconds()))
        if adverse_slippage > self.policy.max_adverse_slippage_bps:
            reasons.append("adverse slippage exceeds qualification policy")
        if latency > self.policy.max_submit_to_fill_seconds:
            reasons.append("submit-to-fill latency exceeds qualification policy")
        if reasons:
            raise PaperQualificationRejected(reasons)

        payload = {
            "adverse_slippage_bps": _decimal_text(adverse_slippage),
            "average_fill_price": _decimal_text(average_fill),
            "benchmark_limit_price": _decimal_text(benchmark_limit),
            "client_order_id": binding.client_order_id,
            "evaluated_at": evaluated_at.isoformat(),
            "expected_quantity": _decimal_text(expected_quantity),
            "fill_count": len(fills),
            "filled_quantity": _decimal_text(filled_quantity),
            "order_id": expected_bracket.order_id,
            "order_payload_hash": binding.order_payload_hash,
            "parent_broker_order_id": bracket_attestation.parent_order_id,
            "policy_max_adverse_slippage_bps": _decimal_text(
                self.policy.max_adverse_slippage_bps
            ),
            "policy_max_submit_to_fill_seconds": _decimal_text(
                self.policy.max_submit_to_fill_seconds
            ),
            "reconciled_at": reconciled_at.isoformat(),
            "signed_slippage_bps": _decimal_text(signed_slippage),
            "stop_loss_broker_order_id": bracket_attestation.stop_loss_order_id,
            "submission_binding_hash": binding.fingerprint,
            "submission_control_hash": state.control_hash,
            "submission_event_head_hash": state.event_head_hash,
            "submit_attempt_at": submit_at.isoformat(),
            "submit_to_fill_seconds": _decimal_text(latency),
            "take_profit_broker_order_id": bracket_attestation.take_profit_order_id,
            "terminal_fill_at": terminal_at.isoformat(),
            "trade_update_control_hash": ledger_state.control_hash,
            "trade_update_head_hash": ledger_state.head_hash,
            "trade_update_scope_hash": ledger_state.scope_hash,
        }
        report_hash = _report_hash(payload)
        return PaperQualificationReport(
            order_id=expected_bracket.order_id,
            client_order_id=binding.client_order_id,
            parent_broker_order_id=bracket_attestation.parent_order_id,
            take_profit_broker_order_id=bracket_attestation.take_profit_order_id,
            stop_loss_broker_order_id=bracket_attestation.stop_loss_order_id,
            order_payload_hash=binding.order_payload_hash,
            submission_binding_hash=binding.fingerprint,
            submission_control_hash=state.control_hash,
            submission_event_head_hash=state.event_head_hash,
            trade_update_scope_hash=ledger_state.scope_hash,
            trade_update_head_hash=ledger_state.head_hash,
            trade_update_control_hash=ledger_state.control_hash,
            expected_quantity=expected_quantity,
            filled_quantity=filled_quantity,
            fill_count=len(fills),
            average_fill_price=average_fill,
            benchmark_limit_price=benchmark_limit,
            signed_slippage_bps=signed_slippage,
            adverse_slippage_bps=adverse_slippage,
            submit_to_fill_seconds=latency,
            submit_attempt_at=submit_at,
            reconciled_at=reconciled_at,
            terminal_fill_at=terminal_at,
            evaluated_at=evaluated_at,
            policy_max_adverse_slippage_bps=self.policy.max_adverse_slippage_bps,
            policy_max_submit_to_fill_seconds=self.policy.max_submit_to_fill_seconds,
            report_hash=report_hash,
        )


def _payload_str(payload: object, key: str) -> str:
    if not isinstance(payload, dict):
        raise PaperQualificationIntegrityError("bracket canonical payload is not a mapping")
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise PaperQualificationIntegrityError(f"bracket payload {key} is missing")
    return value


def _payload_decimal(payload: object, key: str) -> Decimal:
    text = _payload_str(payload, key)
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise PaperQualificationIntegrityError(f"bracket payload {key} is invalid") from exc
    if not _finite_positive(value):
        raise PaperQualificationIntegrityError(f"bracket payload {key} must be positive")
    return value


def _report_hash(payload: dict[str, object]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decimal_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("decimal must be finite")
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is missing")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    _require_aware(parsed, "timestamp")
    return parsed.astimezone(timezone.utc)


def _reject_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON constant forbidden: {token}")


def _finite_positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _require_aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
