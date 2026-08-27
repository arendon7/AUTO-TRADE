from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from enum import StrEnum
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping

from autotrade.paper_runtime_readiness_seal import (
    PaperRuntimeReadinessSealedResult,
    PaperRuntimeReadinessSealStatus,
)
from autotrade.persistence import SQLiteRuntime, _ledger_hash


PAPER_EXECUTION_ADMISSION_VERSION = "W87_PAPER_EXECUTION_ADMISSION_V1"
W87_MIN_CANARY_NOTIONAL_USD = Decimal("1")
W87_MAX_CANARY_NOTIONAL_USD = Decimal("5")
W87_PROBATION_ORDER_CAP = 1

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$")
_ACCOUNT_RE = re.compile(r"^[0-9A-Za-z-]{8,128}$")


class PaperExecutionAdmissionError(RuntimeError):
    pass


class PaperExecutionAdmissionIntegrityError(PaperExecutionAdmissionError):
    pass


class PaperExecutionAdmissionBlocked(PaperExecutionAdmissionError):
    pass


class PaperExecutionAdmissionConflict(PaperExecutionAdmissionError):
    pass


class PaperExecutionAdmissionStatus(StrEnum):
    ADMITTED = "ADMITTED"


@dataclass(frozen=True, slots=True)
class PaperExecutionAdmissionReceipt:
    admission_id: str
    contract_version: str
    readiness_seal_hash: str
    pipeline_receipt_hash: str
    final_readiness_hash: str
    funding_capacity_hash: str
    source_snapshot_hash: str
    candidate_identity_hash: str
    authority_key: str
    w85_admission_hash: str
    strategy_id: str
    product_id: str
    symbol: str
    broker_pair: str
    account_id: str
    side: str
    broker_minimum_executable_quantity: Decimal
    broker_trade_increment: Decimal
    conservative_limit_price: Decimal
    canary_quantity: Decimal
    canary_notional_usd: Decimal
    probation_notional_cap_usd: Decimal
    probation_order_cap: int
    status: PaperExecutionAdmissionStatus
    captured_at: datetime
    source_seal_observed_at: datetime
    source_seal_valid_until: datetime
    captured_from_ready_seal: bool
    exact_canary_envelope: bool
    order_intent_creation_permitted: bool
    separate_risk_decision_required: bool
    separate_human_execution_approval_required: bool
    oms_handoff_permitted: bool
    capital_reserved: bool
    broker_write_performed: bool
    paper_execution_authorized: bool
    external_execution_authorized: bool
    runtime_execution_authorized: bool
    capital_authority: str
    live_trading: str
    receipt_hash: str

    def __post_init__(self) -> None:
        _id(self.admission_id, "admission_id")
        _id(self.strategy_id, "strategy_id")
        _id(self.product_id, "product_id")
        if self.contract_version != PAPER_EXECUTION_ADMISSION_VERSION:
            raise PaperExecutionAdmissionIntegrityError(
                "W87 execution admission version is not canonical"
            )
        for name in (
            "readiness_seal_hash",
            "pipeline_receipt_hash",
            "final_readiness_hash",
            "funding_capacity_hash",
            "source_snapshot_hash",
            "candidate_identity_hash",
            "authority_key",
            "w85_admission_hash",
            "receipt_hash",
        ):
            _sha(getattr(self, name), name)

        _candidate_symbol(self.symbol)
        _broker_pair(self.broker_pair)
        if self.symbol.replace("-", "/") != self.broker_pair:
            raise PaperExecutionAdmissionIntegrityError(
                "candidate symbol and broker pair are not the same instrument identity"
            )
        if not isinstance(self.account_id, str) or not _ACCOUNT_RE.fullmatch(self.account_id):
            raise PaperExecutionAdmissionIntegrityError("account_id is invalid")
        if self.side != "BUY":
            raise PaperExecutionAdmissionIntegrityError(
                "W87 first admitted PAPER canary is BUY-only"
            )

        for name in (
            "broker_minimum_executable_quantity",
            "broker_trade_increment",
            "conservative_limit_price",
            "canary_quantity",
            "canary_notional_usd",
            "probation_notional_cap_usd",
        ):
            _positive(getattr(self, name), name)
        if self.probation_notional_cap_usd > W87_MAX_CANARY_NOTIONAL_USD:
            raise PaperExecutionAdmissionIntegrityError(
                "W87 probation cap may not exceed USD 5"
            )
        if self.probation_order_cap != W87_PROBATION_ORDER_CAP:
            raise PaperExecutionAdmissionIntegrityError(
                "W87 probation order cap must remain 1"
            )
        if self.status is not PaperExecutionAdmissionStatus.ADMITTED:
            raise PaperExecutionAdmissionIntegrityError("W87 receipt must be ADMITTED")

        for name in (
            "captured_at",
            "source_seal_observed_at",
            "source_seal_valid_until",
        ):
            _aware(getattr(self, name), name)
        captured = _utc(self.captured_at)
        seal_observed = _utc(self.source_seal_observed_at)
        seal_valid_until = _utc(self.source_seal_valid_until)
        if captured < seal_observed or captured > seal_valid_until:
            raise PaperExecutionAdmissionIntegrityError(
                "W87 admission must be captured while exact W86 seal is valid"
            )

        expected_quantity = _canonical_canary_quantity(
            minimum_quantity=self.broker_minimum_executable_quantity,
            trade_increment=self.broker_trade_increment,
            conservative_price=self.conservative_limit_price,
        )
        if self.canary_quantity != expected_quantity:
            raise PaperExecutionAdmissionIntegrityError(
                "canary quantity is not exact canonical W87 quantity"
            )
        if not _multiple(self.canary_quantity, self.broker_trade_increment):
            raise PaperExecutionAdmissionIntegrityError(
                "canary quantity violates broker trade increment"
            )
        expected_notional = self.canary_quantity * self.conservative_limit_price
        if self.canary_notional_usd != expected_notional:
            raise PaperExecutionAdmissionIntegrityError(
                "canary notional is inconsistent"
            )
        if not (
            W87_MIN_CANARY_NOTIONAL_USD
            <= self.canary_notional_usd
            <= self.probation_notional_cap_usd
        ):
            raise PaperExecutionAdmissionIntegrityError(
                "canary notional must remain inside USD 1..probation cap"
            )

        if (
            self.captured_from_ready_seal is not True
            or self.exact_canary_envelope is not True
            or self.order_intent_creation_permitted is not True
            or self.separate_risk_decision_required is not True
            or self.separate_human_execution_approval_required is not True
            or self.oms_handoff_permitted is not False
            or self.capital_reserved is not False
            or self.broker_write_performed is not False
            or self.paper_execution_authorized is not False
            or self.external_execution_authorized is not False
            or self.runtime_execution_authorized is not False
            or self.capital_authority != "NONE"
            or self.live_trading != "BLOCKED"
        ):
            raise PaperExecutionAdmissionIntegrityError(
                "W87 admission may only permit exact OrderIntent construction; "
                "it may not grant OMS, execution, capital, broker-write or LIVE authority"
            )
        if self.receipt_hash != _hash(_payload(self, include_hash=False)):
            raise PaperExecutionAdmissionIntegrityError(
                "W87 admission receipt hash mismatch"
            )

    def to_dict(self) -> dict[str, object]:
        return _payload(self, include_hash=True)


def capture_paper_execution_admission(
    *,
    admission_id: str,
    sealed_result: PaperRuntimeReadinessSealedResult,
) -> PaperExecutionAdmissionReceipt:
    """Capture one exact, non-executing PAPER canary envelope from W86.

    This transition only permits construction of a single exact OrderIntent
    candidate. It grants no RiskDecision, OMS handoff, capital reservation,
    broker POST, runtime execution or LIVE authority.
    """

    _id(admission_id, "admission_id")
    _validate_w86(sealed_result)
    now = _utc(_now_utc())
    seal = sealed_result.seal

    if (
        seal.status is not PaperRuntimeReadinessSealStatus.READY
        or seal.paper_runtime_ready is not True
    ):
        raise PaperExecutionAdmissionBlocked("W86 readiness seal is not READY")
    if not (_utc(seal.observed_at) <= now <= _utc(seal.valid_until)):
        raise PaperExecutionAdmissionBlocked(
            "W86 readiness seal expired before W87 capture"
        )

    final = sealed_result.pipeline.final_readiness
    asset = sealed_result.pipeline.asset_truth
    market = sealed_result.pipeline.market_truth
    funding = sealed_result.pipeline.funding_capacity

    _candidate_symbol(seal.symbol)
    _broker_pair(asset.canonical_broker_pair)
    if asset.canonical_broker_pair != market.canonical_broker_pair:
        raise PaperExecutionAdmissionIntegrityError(
            "W86 asset and market broker-pair identities disagree"
        )
    if seal.symbol.replace("-", "/") != asset.canonical_broker_pair:
        raise PaperExecutionAdmissionIntegrityError(
            "W86 candidate symbol does not bind to canonical broker pair"
        )

    if final.probation_order_cap != W87_PROBATION_ORDER_CAP:
        raise PaperExecutionAdmissionBlocked(
            "W85/W86 probation order cap is not exactly one"
        )
    cap = min(
        final.probation_notional_cap_usd,
        W87_MAX_CANARY_NOTIONAL_USD,
    )
    if cap < W87_MIN_CANARY_NOTIONAL_USD:
        raise PaperExecutionAdmissionBlocked(
            "upstream probation cap is below canonical W87 USD 1 minimum canary"
        )

    quantity = _canonical_canary_quantity(
        minimum_quantity=final.minimum_executable_quantity,
        trade_increment=asset.min_trade_increment,
        conservative_price=final.conservative_unit_price,
    )
    notional = quantity * final.conservative_unit_price
    if not W87_MIN_CANARY_NOTIONAL_USD <= notional <= cap:
        raise PaperExecutionAdmissionBlocked(
            "broker increment/price cannot produce canonical USD 1..5 PAPER canary"
        )
    if funding.buying_power_usd < notional:
        raise PaperExecutionAdmissionBlocked(
            "current W86 funding proof cannot fund canonical W87 canary"
        )

    values = {
        "admission_id": admission_id,
        "contract_version": PAPER_EXECUTION_ADMISSION_VERSION,
        "readiness_seal_hash": seal.receipt_hash,
        "pipeline_receipt_hash": sealed_result.pipeline.receipt.receipt_hash,
        "final_readiness_hash": final.receipt_hash,
        "funding_capacity_hash": funding.proof_hash,
        "source_snapshot_hash": seal.source_snapshot_hash,
        "candidate_identity_hash": seal.candidate_identity_hash,
        "authority_key": seal.authority_key,
        "w85_admission_hash": seal.admission_hash,
        "strategy_id": seal.strategy_id,
        "product_id": seal.product_id,
        "symbol": seal.symbol,
        "broker_pair": asset.canonical_broker_pair,
        "account_id": seal.account_id,
        "side": "BUY",
        "broker_minimum_executable_quantity": final.minimum_executable_quantity,
        "broker_trade_increment": asset.min_trade_increment,
        "conservative_limit_price": final.conservative_unit_price,
        "canary_quantity": quantity,
        "canary_notional_usd": notional,
        "probation_notional_cap_usd": cap,
        "probation_order_cap": W87_PROBATION_ORDER_CAP,
        "status": PaperExecutionAdmissionStatus.ADMITTED,
        "captured_at": now,
        "source_seal_observed_at": seal.observed_at,
        "source_seal_valid_until": seal.valid_until,
        "captured_from_ready_seal": True,
        "exact_canary_envelope": True,
        "order_intent_creation_permitted": True,
        "separate_risk_decision_required": True,
        "separate_human_execution_approval_required": True,
        "oms_handoff_permitted": False,
        "capital_reserved": False,
        "broker_write_performed": False,
        "paper_execution_authorized": False,
        "external_execution_authorized": False,
        "runtime_execution_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }
    return PaperExecutionAdmissionReceipt(
        **values,
        receipt_hash=_hash(_payload_values(values)),
    )


class SQLitePaperExecutionAdmissionRegistry:
    """Durable local registry for exact W87 admission receipts.

    The registry has no credentials, network, OMS, Safety writer, capital
    reservation or execution API. A readiness seal can bind to at most one
    admission. Exact replay is idempotent; conflicting reuse fails closed.
    """

    def __init__(self, runtime: SQLiteRuntime) -> None:
        if not isinstance(runtime, SQLiteRuntime):
            raise TypeError("SQLiteRuntime is required")
        self._runtime = runtime
        self._initialize()

    def _initialize(self) -> None:
        conn = self._runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS w87_paper_execution_admission (
                    admission_id TEXT PRIMARY KEY,
                    readiness_seal_hash TEXT NOT NULL UNIQUE,
                    candidate_identity_hash TEXT NOT NULL,
                    authority_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    captured_at TEXT NOT NULL
                );
                """
            )
        finally:
            conn.close()

    def capture(
        self,
        receipt: PaperExecutionAdmissionReceipt,
    ) -> PaperExecutionAdmissionReceipt:
        if not isinstance(receipt, PaperExecutionAdmissionReceipt):
            raise TypeError("PaperExecutionAdmissionReceipt is required")
        receipt.__post_init__()

        payload_json = _canonical(receipt.to_dict())
        event_payload = {
            "admission_id": receipt.admission_id,
            "receipt_hash": receipt.receipt_hash,
            "readiness_seal_hash": receipt.readiness_seal_hash,
            "candidate_identity_hash": receipt.candidate_identity_hash,
            "authority_key": receipt.authority_key,
            "strategy_id": receipt.strategy_id,
            "candidate_symbol": receipt.symbol,
            "broker_pair": receipt.broker_pair,
            "canary_notional_usd": str(receipt.canary_notional_usd),
            "order_intent_creation_permitted": True,
            "paper_execution_authorized": False,
            "external_execution_authorized": False,
            "runtime_execution_authorized": False,
            "capital_authority": "NONE",
            "live_trading": "BLOCKED",
        }
        event_id = f"w87-admission:{receipt.receipt_hash}"
        event_json = _canonical(event_payload)

        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            by_id = conn.execute(
                "SELECT payload_json,receipt_hash,readiness_seal_hash "
                "FROM w87_paper_execution_admission WHERE admission_id=?",
                (receipt.admission_id,),
            ).fetchone()
            by_seal = conn.execute(
                "SELECT admission_id,payload_json,receipt_hash "
                "FROM w87_paper_execution_admission WHERE readiness_seal_hash=?",
                (receipt.readiness_seal_hash,),
            ).fetchone()

            if by_id is not None or by_seal is not None:
                if not _same_existing_receipt(
                    by_id=by_id,
                    by_seal=by_seal,
                    receipt=receipt,
                    payload_json=payload_json,
                ):
                    raise PaperExecutionAdmissionConflict(
                        "W87 admission id/readiness seal already bound to different receipt"
                    )
                self._verify_event_tx(
                    conn,
                    event_id=event_id,
                    event_json=event_json,
                )
                conn.execute("COMMIT")
                return receipt

            conn.execute(
                """
                INSERT INTO w87_paper_execution_admission(
                    admission_id, readiness_seal_hash, candidate_identity_hash,
                    authority_key, payload_json, receipt_hash, captured_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    receipt.admission_id,
                    receipt.readiness_seal_hash,
                    receipt.candidate_identity_hash,
                    receipt.authority_key,
                    payload_json,
                    receipt.receipt_hash,
                    _utc(receipt.captured_at).isoformat(),
                ),
            )

            previous = conn.execute(
                "SELECT event_hash FROM ledger_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = previous["event_hash"] if previous is not None else "GENESIS"
            occurred_at = _utc(receipt.captured_at).isoformat()
            event_hash = _ledger_hash(
                prev_hash=prev_hash,
                event_id=event_id,
                event_type="W87_PAPER_EXECUTION_ADMISSION_CAPTURED",
                occurred_at=occurred_at,
                payload_json=event_json,
            )
            conn.execute(
                """
                INSERT INTO ledger_events(
                    event_id,event_type,occurred_at,payload_json,prev_hash,event_hash
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    event_id,
                    "W87_PAPER_EXECUTION_ADMISSION_CAPTURED",
                    occurred_at,
                    event_json,
                    prev_hash,
                    event_hash,
                ),
            )
            conn.execute("COMMIT")
            return receipt
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, admission_id: str) -> PaperExecutionAdmissionReceipt:
        _id(admission_id, "admission_id")
        conn = self._runtime.connect()
        try:
            row = conn.execute(
                "SELECT payload_json FROM w87_paper_execution_admission "
                "WHERE admission_id=?",
                (admission_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise KeyError(admission_id)
        return _from_payload(json.loads(row["payload_json"]))

    @staticmethod
    def _verify_event_tx(
        conn: sqlite3.Connection,
        *,
        event_id: str,
        event_json: str,
    ) -> None:
        row = conn.execute(
            "SELECT event_type,payload_json FROM ledger_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if (
            row is None
            or row["event_type"] != "W87_PAPER_EXECUTION_ADMISSION_CAPTURED"
            or row["payload_json"] != event_json
        ):
            raise PaperExecutionAdmissionConflict(
                "W87 admission row exists without exact durable ledger event"
            )


def _same_existing_receipt(
    *,
    by_id: sqlite3.Row | None,
    by_seal: sqlite3.Row | None,
    receipt: PaperExecutionAdmissionReceipt,
    payload_json: str,
) -> bool:
    if by_id is not None:
        if (
            by_id["payload_json"] != payload_json
            or by_id["receipt_hash"] != receipt.receipt_hash
            or by_id["readiness_seal_hash"] != receipt.readiness_seal_hash
        ):
            return False
    if by_seal is not None:
        if (
            by_seal["admission_id"] != receipt.admission_id
            or by_seal["payload_json"] != payload_json
            or by_seal["receipt_hash"] != receipt.receipt_hash
        ):
            return False
    return True


def _validate_w86(value: PaperRuntimeReadinessSealedResult) -> None:
    if not isinstance(value, PaperRuntimeReadinessSealedResult):
        raise TypeError(
            "sealed_result must be PaperRuntimeReadinessSealedResult"
        )

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

    seal = value.seal
    if (
        seal.paper_execution_authorized is not False
        or seal.external_execution_authorized is not False
        or seal.runtime_execution_authorized is not False
        or seal.capital_authority != "NONE"
        or seal.live_trading != "BLOCKED"
    ):
        raise PaperExecutionAdmissionIntegrityError(
            "W86 source contains forbidden authority escalation"
        )
    if (
        value.pipeline.final_readiness.probation_notional_cap_usd
        > W87_MAX_CANARY_NOTIONAL_USD
    ):
        raise PaperExecutionAdmissionIntegrityError(
            "upstream probation cap exceeds USD 5"
        )


def _canonical_canary_quantity(
    *,
    minimum_quantity: Decimal,
    trade_increment: Decimal,
    conservative_price: Decimal,
) -> Decimal:
    _positive(minimum_quantity, "minimum_quantity")
    _positive(trade_increment, "trade_increment")
    _positive(conservative_price, "conservative_price")
    floor_quantity = W87_MIN_CANARY_NOTIONAL_USD / conservative_price
    target = max(minimum_quantity, floor_quantity)
    return _ceil(target, trade_increment)


def _ceil(value: Decimal, increment: Decimal) -> Decimal:
    return (
        (value / increment).to_integral_value(rounding=ROUND_CEILING)
        * increment
    )


def _multiple(value: Decimal, increment: Decimal) -> bool:
    return value % increment == 0


def _candidate_symbol(value: str) -> None:
    if (
        not isinstance(value, str)
        or value.count("-") != 1
        or value != value.upper()
        or any(not part for part in value.split("-"))
    ):
        raise PaperExecutionAdmissionIntegrityError(
            "symbol must be canonical BASE-QUOTE"
        )


def _broker_pair(value: str) -> None:
    if (
        not isinstance(value, str)
        or value.count("/") != 1
        or value != value.upper()
        or any(not part for part in value.split("/"))
    ):
        raise PaperExecutionAdmissionIntegrityError(
            "broker_pair must be canonical BASE/QUOTE"
        )


def _payload(
    receipt: PaperExecutionAdmissionReceipt,
    *,
    include_hash: bool,
) -> dict[str, object]:
    values = {
        name: getattr(receipt, name)
        for name in PaperExecutionAdmissionReceipt.__dataclass_fields__
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


def _from_payload(
    payload: Mapping[str, object],
) -> PaperExecutionAdmissionReceipt:
    expected = set(PaperExecutionAdmissionReceipt.__dataclass_fields__)
    if set(payload) != expected:
        raise PaperExecutionAdmissionIntegrityError(
            "stored W87 admission payload is non-canonical"
        )
    return PaperExecutionAdmissionReceipt(
        admission_id=_text(payload, "admission_id"),
        contract_version=_text(payload, "contract_version"),
        readiness_seal_hash=_text(payload, "readiness_seal_hash"),
        pipeline_receipt_hash=_text(payload, "pipeline_receipt_hash"),
        final_readiness_hash=_text(payload, "final_readiness_hash"),
        funding_capacity_hash=_text(payload, "funding_capacity_hash"),
        source_snapshot_hash=_text(payload, "source_snapshot_hash"),
        candidate_identity_hash=_text(payload, "candidate_identity_hash"),
        authority_key=_text(payload, "authority_key"),
        w85_admission_hash=_text(payload, "w85_admission_hash"),
        strategy_id=_text(payload, "strategy_id"),
        product_id=_text(payload, "product_id"),
        symbol=_text(payload, "symbol"),
        broker_pair=_text(payload, "broker_pair"),
        account_id=_text(payload, "account_id"),
        side=_text(payload, "side"),
        broker_minimum_executable_quantity=_decimal(
            payload,
            "broker_minimum_executable_quantity",
        ),
        broker_trade_increment=_decimal(payload, "broker_trade_increment"),
        conservative_limit_price=_decimal(payload, "conservative_limit_price"),
        canary_quantity=_decimal(payload, "canary_quantity"),
        canary_notional_usd=_decimal(payload, "canary_notional_usd"),
        probation_notional_cap_usd=_decimal(
            payload,
            "probation_notional_cap_usd",
        ),
        probation_order_cap=_integer(payload, "probation_order_cap"),
        status=PaperExecutionAdmissionStatus(_text(payload, "status")),
        captured_at=_datetime(payload, "captured_at"),
        source_seal_observed_at=_datetime(payload, "source_seal_observed_at"),
        source_seal_valid_until=_datetime(payload, "source_seal_valid_until"),
        captured_from_ready_seal=_boolean(payload, "captured_from_ready_seal"),
        exact_canary_envelope=_boolean(payload, "exact_canary_envelope"),
        order_intent_creation_permitted=_boolean(
            payload,
            "order_intent_creation_permitted",
        ),
        separate_risk_decision_required=_boolean(
            payload,
            "separate_risk_decision_required",
        ),
        separate_human_execution_approval_required=_boolean(
            payload,
            "separate_human_execution_approval_required",
        ),
        oms_handoff_permitted=_boolean(payload, "oms_handoff_permitted"),
        capital_reserved=_boolean(payload, "capital_reserved"),
        broker_write_performed=_boolean(payload, "broker_write_performed"),
        paper_execution_authorized=_boolean(
            payload,
            "paper_execution_authorized",
        ),
        external_execution_authorized=_boolean(
            payload,
            "external_execution_authorized",
        ),
        runtime_execution_authorized=_boolean(
            payload,
            "runtime_execution_authorized",
        ),
        capital_authority=_text(payload, "capital_authority"),
        live_trading=_text(payload, "live_trading"),
        receipt_hash=_text(payload, "receipt_hash"),
    )


def _id(value: str, name: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise PaperExecutionAdmissionIntegrityError(
            f"{name} must be canonical identifier"
        )


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise PaperExecutionAdmissionIntegrityError(
            f"{name} must be lowercase sha256"
        )


def _positive(value: Decimal, name: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
    ):
        raise PaperExecutionAdmissionIntegrityError(
            f"{name} must be finite positive Decimal"
        )


def _aware(value: datetime, name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise PaperExecutionAdmissionIntegrityError(
            f"{name} must be timezone-aware"
        )


def _utc(value: datetime) -> datetime:
    _aware(value, "datetime")
    return value.astimezone(timezone.utc)


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise PaperExecutionAdmissionIntegrityError(f"{key} must be text")
    return value


def _decimal(payload: Mapping[str, object], key: str) -> Decimal:
    value = payload.get(key)
    if not isinstance(value, str):
        raise PaperExecutionAdmissionIntegrityError(
            f"{key} must be canonical decimal text"
        )
    try:
        result = Decimal(value)
    except Exception as exc:
        raise PaperExecutionAdmissionIntegrityError(
            f"{key} is invalid Decimal"
        ) from exc
    if not result.is_finite():
        raise PaperExecutionAdmissionIntegrityError(
            f"{key} must be finite"
        )
    return result


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaperExecutionAdmissionIntegrityError(f"{key} must be integer")
    return value


def _boolean(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise PaperExecutionAdmissionIntegrityError(f"{key} must be bool")
    return value


def _datetime(payload: Mapping[str, object], key: str) -> datetime:
    value = _text(payload, key)
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PaperExecutionAdmissionIntegrityError(
            f"{key} is invalid datetime"
        ) from exc
    _aware(result, key)
    return result


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "PAPER_EXECUTION_ADMISSION_VERSION",
    "W87_MIN_CANARY_NOTIONAL_USD",
    "W87_MAX_CANARY_NOTIONAL_USD",
    "W87_PROBATION_ORDER_CAP",
    "PaperExecutionAdmissionError",
    "PaperExecutionAdmissionIntegrityError",
    "PaperExecutionAdmissionBlocked",
    "PaperExecutionAdmissionConflict",
    "PaperExecutionAdmissionStatus",
    "PaperExecutionAdmissionReceipt",
    "SQLitePaperExecutionAdmissionRegistry",
    "capture_paper_execution_admission",
]
