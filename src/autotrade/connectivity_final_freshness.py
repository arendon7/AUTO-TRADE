from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable, Mapping

from autotrade.brokers.alpaca_paper_asset import (
    AlpacaPaperEquityAssetAttestation,
    AlpacaPaperEquityAssetGateway,
)
from autotrade.brokers.alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from autotrade.brokers.alpaca_paper_canary_permit import (
    PaperCanaryPermitStatus,
    SQLitePaperCanaryPermitRegistry,
)
from autotrade.brokers.alpaca_paper_connectivity_candidate import (
    DECISION_TTL_MS,
    MAX_ACCOUNT_FRACTION,
    MAX_CONNECTIVITY_NOTIONAL,
    MAX_MARKET_AGE_SECONDS,
)
from autotrade.brokers.alpaca_paper_flat_account import (
    AlpacaPaperFlatAccountGateway,
    PaperFlatAccountAttestation,
)
from autotrade.brokers.alpaca_paper_gateway import (
    ALPACA_PAPER_ACCOUNT_PATH,
    ALPACA_PAPER_TRADING_HOST,
    AlpacaPaperAccountAttestation,
    AlpacaPaperAccountGateway,
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.alpaca_paper_market_data import (
    ALPACA_BASIC_EQUITY_FEED,
    ALPACA_MARKET_DATA_CURRENCY,
    AlpacaPaperEquityMarketAttestation,
    AlpacaPaperEquityMarketDataGateway,
    AlpacaPaperMarketDataConfig,
)
from autotrade.brokers.alpaca_paper_market_evidence import market_evidence_payload
from autotrade.brokers.alpaca_paper_operational import (
    PaperOperationalWorkspace,
    _file_sha256,
    _read_json_object,
    account_attestation_payload,
)
from autotrade.brokers.alpaca_paper_submission import (
    PaperSubmissionStatus,
    SQLitePaperSubmissionRegistry,
)
from autotrade.connectivity_canary_authority import (
    CONNECTIVITY_CANARY_STRATEGY_ID,
    SQLiteConnectivityCanaryAuthorityStore,
)
from autotrade.connectivity_operator_decision import (
    ConnectivityOperatorDecision,
    ConnectivityOperatorDecisionStatus,
    SQLiteConnectivityOperatorDecisionRegistry,
)
from autotrade.connectivity_preparation_binding import SQLiteConnectivityPreparationBindingStore
from autotrade.domain import (
    OrderStatus,
    PortfolioSnapshot,
    RiskDecision,
    RiskDecisionStatus,
    intent_fingerprint,
    market_fingerprint,
    risk_decision_fingerprint,
)
from autotrade.instrument_master import InstrumentTradingStatus, SQLiteInstrumentMaster
from autotrade.persistence import (
    SQLiteEventLedger,
    SQLiteOrderStore,
    SQLiteRuntime,
    SQLiteSafetyStateStore,
)
from autotrade.safety import CapitalSafetyKernel, SafetyLimits

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_GENESIS_HASH = "0" * 64
_ARTIFACT = "connectivity_final_freshness.json"
_REGISTRY_DB = "connectivity_final_freshness.sqlite3"
_OPERATOR_DB = "connectivity_operator.sqlite3"
_OPERATOR_ARTIFACT = "connectivity_operator_decision.json"
_PREPARATION_ARTIFACT = "connectivity_preparation.json"
_FINAL_TTL_SECONDS = 5
_FINAL_LIMITS_VERSION = "r6-connectivity-final-freshness-v1"


class ConnectivityFinalFreshnessError(RuntimeError):
    pass


class ConnectivityFinalFreshnessRejected(ConnectivityFinalFreshnessError):
    pass


class ConnectivityFinalFreshnessConflict(ConnectivityFinalFreshnessError):
    pass


class ConnectivityFinalFreshnessIntegrityError(ConnectivityFinalFreshnessError):
    pass


class ConnectivityFinalFreshnessStatus(StrEnum):
    ISSUED = "ISSUED"


@dataclass(frozen=True, slots=True)
class ConnectivityFinalFreshnessPermit:
    order_id: str
    client_order_id: str
    attempt_id: str
    operator_context_hash: str
    operator_decision_hash: str
    operator_event_hash: str
    preparation_hash: str
    connectivity_binding_hash: str
    standard_package_hash: str
    canary_approval_hash: str
    submission_binding_hash: str
    bracket_payload_hash: str
    instrument_rules_fingerprint: str
    initial_account_fingerprint: str
    fresh_account_fingerprint: str
    fresh_asset_fingerprint: str
    fresh_flat_fingerprint: str
    fresh_market_attestation_fingerprint: str
    fresh_market_fingerprint: str
    fresh_risk_decision_id: str
    fresh_risk_decision_fingerprint: str
    safety_state_version: int
    fresh_portfolio_snapshot_id: str
    effective_notional_cap: Decimal
    core_db_sha256_after_fresh_safety: str
    issued_at: datetime
    expires_at: datetime
    permit_hash: str

    def __post_init__(self) -> None:
        for label, value in (
            ("order_id", self.order_id),
            ("client_order_id", self.client_order_id),
            ("attempt_id", self.attempt_id),
            ("fresh_risk_decision_id", self.fresh_risk_decision_id),
            ("fresh_portfolio_snapshot_id", self.fresh_portfolio_snapshot_id),
        ):
            _validate_id(value, label)
        for label, value in (
            ("operator_context_hash", self.operator_context_hash),
            ("operator_decision_hash", self.operator_decision_hash),
            ("operator_event_hash", self.operator_event_hash),
            ("preparation_hash", self.preparation_hash),
            ("connectivity_binding_hash", self.connectivity_binding_hash),
            ("standard_package_hash", self.standard_package_hash),
            ("canary_approval_hash", self.canary_approval_hash),
            ("submission_binding_hash", self.submission_binding_hash),
            ("bracket_payload_hash", self.bracket_payload_hash),
            ("instrument_rules_fingerprint", self.instrument_rules_fingerprint),
            ("initial_account_fingerprint", self.initial_account_fingerprint),
            ("fresh_account_fingerprint", self.fresh_account_fingerprint),
            ("fresh_asset_fingerprint", self.fresh_asset_fingerprint),
            ("fresh_flat_fingerprint", self.fresh_flat_fingerprint),
            ("fresh_market_attestation_fingerprint", self.fresh_market_attestation_fingerprint),
            ("fresh_market_fingerprint", self.fresh_market_fingerprint),
            ("fresh_risk_decision_fingerprint", self.fresh_risk_decision_fingerprint),
            ("core_db_sha256_after_fresh_safety", self.core_db_sha256_after_fresh_safety),
            ("permit_hash", self.permit_hash),
        ):
            _validate_hash(value, label)
        if isinstance(self.safety_state_version, bool) or not isinstance(self.safety_state_version, int) or self.safety_state_version < 0:
            raise ValueError("safety_state_version must be non-negative integer")
        if not isinstance(self.effective_notional_cap, Decimal) or not self.effective_notional_cap.is_finite() or self.effective_notional_cap <= 0:
            raise ValueError("effective_notional_cap must be finite and positive")
        _require_aware(self.issued_at, "issued_at")
        _require_aware(self.expires_at, "expires_at")
        issued = self.issued_at.astimezone(timezone.utc)
        expires = self.expires_at.astimezone(timezone.utc)
        if expires <= issued or expires - issued > timedelta(seconds=_FINAL_TTL_SECONDS):
            raise ValueError("final freshness permit must be >0 and <=5 seconds")
        if self.permit_hash != _hash(_permit_payload(self, include_hash=False)):
            raise ValueError("final freshness permit hash mismatch")

    def is_valid_at(self, now: datetime) -> bool:
        _require_aware(now, "now")
        instant = now.astimezone(timezone.utc)
        return self.issued_at.astimezone(timezone.utc) <= instant < self.expires_at.astimezone(timezone.utc)

    def payload(self) -> dict[str, object]:
        return _permit_payload(self, include_hash=True)


@dataclass(frozen=True, slots=True)
class ConnectivityFinalFreshnessState:
    permit: ConnectivityFinalFreshnessPermit
    status: ConnectivityFinalFreshnessStatus
    event_hash: str


class SQLiteConnectivityFinalFreshnessRegistry:
    """Single-issuance tamper-evident registry. It has no consume/write/broker API."""

    def __init__(self, runtime: SQLiteRuntime) -> None:
        self._runtime = runtime
        conn = runtime.connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS connectivity_final_freshness_events (
                    sequence INTEGER PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    permit_hash TEXT NOT NULL UNIQUE,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS connectivity_final_freshness_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    event_sequence INTEGER NOT NULL CHECK(event_sequence>=0),
                    event_head_hash TEXT NOT NULL,
                    control_hash TEXT NOT NULL
                );
                """
            )
            row = conn.execute(
                "SELECT event_sequence,event_head_hash,control_hash FROM connectivity_final_freshness_control WHERE singleton=1"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO connectivity_final_freshness_control(singleton,event_sequence,event_head_hash,control_hash) VALUES(1,0,?,?)",
                    (_GENESIS_HASH, _control_hash(0, _GENESIS_HASH)),
                )
        finally:
            conn.close()

    def issue(self, permit: ConnectivityFinalFreshnessPermit) -> ConnectivityFinalFreshnessState:
        if not isinstance(permit, ConnectivityFinalFreshnessPermit):
            raise TypeError("ConnectivityFinalFreshnessPermit is required")
        conn = self._runtime.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            states, sequence, head = self._verify_locked(conn)
            if states:
                existing = next(iter(states.values()))
                if existing.permit == permit:
                    conn.execute("COMMIT")
                    return existing
                raise ConnectivityFinalFreshnessConflict(
                    "final freshness was already issued for this workspace"
                )
            payload_json = _canonical(permit.payload())
            event_hash = _event_hash(
                sequence=sequence + 1,
                permit_hash=permit.permit_hash,
                occurred_at=permit.issued_at,
                payload_json=payload_json,
                previous_event_hash=head,
            )
            conn.execute(
                "INSERT INTO connectivity_final_freshness_events(sequence,event_type,permit_hash,occurred_at,payload_json,previous_event_hash,event_hash) VALUES(?,?,?,?,?,?,?)",
                (
                    sequence + 1,
                    ConnectivityFinalFreshnessStatus.ISSUED.value,
                    permit.permit_hash,
                    _iso(permit.issued_at),
                    payload_json,
                    head,
                    event_hash,
                ),
            )
            conn.execute(
                "UPDATE connectivity_final_freshness_control SET event_sequence=?,event_head_hash=?,control_hash=? WHERE singleton=1",
                (sequence + 1, event_hash, _control_hash(sequence + 1, event_hash)),
            )
            states, _, _ = self._verify_locked(conn)
            state = states[permit.permit_hash]
            conn.execute("COMMIT")
            return state
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get(self, permit_hash: str) -> ConnectivityFinalFreshnessState:
        _validate_hash(permit_hash, "permit_hash")
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            if permit_hash not in states:
                raise KeyError(permit_hash)
            return states[permit_hash]
        finally:
            conn.close()

    def list_states(self) -> tuple[ConnectivityFinalFreshnessState, ...]:
        conn = self._runtime.connect()
        try:
            states, _, _ = self._verify_locked(conn)
            return tuple(states[key] for key in sorted(states))
        finally:
            conn.close()

    def _verify_locked(
        self, conn: sqlite3.Connection
    ) -> tuple[dict[str, ConnectivityFinalFreshnessState], int, str]:
        control = conn.execute(
            "SELECT event_sequence,event_head_hash,control_hash FROM connectivity_final_freshness_control WHERE singleton=1"
        ).fetchone()
        if control is None:
            raise ConnectivityFinalFreshnessIntegrityError("final freshness control anchor is missing")
        sequence = _strict_int(control["event_sequence"], "event_sequence")
        head = str(control["event_head_hash"])
        _validate_hash(head, "event_head_hash")
        if str(control["control_hash"]) != _control_hash(sequence, head):
            raise ConnectivityFinalFreshnessIntegrityError("final freshness control hash mismatch")
        rows = conn.execute(
            "SELECT sequence,event_type,permit_hash,occurred_at,payload_json,previous_event_hash,event_hash FROM connectivity_final_freshness_events ORDER BY sequence"
        ).fetchall()
        if len(rows) != sequence:
            raise ConnectivityFinalFreshnessIntegrityError("final freshness event count mismatch")
        states: dict[str, ConnectivityFinalFreshnessState] = {}
        previous = _GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            current_sequence = _strict_int(row["sequence"], "sequence")
            if current_sequence != expected_sequence:
                raise ConnectivityFinalFreshnessIntegrityError("final freshness event sequence gap")
            if str(row["event_type"]) != ConnectivityFinalFreshnessStatus.ISSUED.value:
                raise ConnectivityFinalFreshnessIntegrityError("final freshness event type is invalid")
            permit_hash = str(row["permit_hash"])
            _validate_hash(permit_hash, "permit_hash")
            occurred_at = _datetime(row["occurred_at"], "occurred_at")
            payload_json = str(row["payload_json"])
            parsed = _json_object(payload_json, "final freshness event")
            if payload_json != _canonical(parsed):
                raise ConnectivityFinalFreshnessIntegrityError("final freshness payload is non-canonical")
            if str(row["previous_event_hash"]) != previous:
                raise ConnectivityFinalFreshnessIntegrityError("final freshness previous-hash mismatch")
            calculated = _event_hash(
                sequence=current_sequence,
                permit_hash=permit_hash,
                occurred_at=occurred_at,
                payload_json=payload_json,
                previous_event_hash=previous,
            )
            if str(row["event_hash"]) != calculated:
                raise ConnectivityFinalFreshnessIntegrityError("final freshness event hash mismatch")
            permit = _permit_from_payload(parsed)
            if permit.permit_hash != permit_hash or permit.issued_at != occurred_at:
                raise ConnectivityFinalFreshnessIntegrityError("final freshness event identity mismatch")
            if permit_hash in states:
                raise ConnectivityFinalFreshnessIntegrityError("final freshness permit issued more than once")
            states[permit_hash] = ConnectivityFinalFreshnessState(
                permit=permit,
                status=ConnectivityFinalFreshnessStatus.ISSUED,
                event_hash=calculated,
            )
            previous = calculated
        if sequence == 0:
            if head != _GENESIS_HASH:
                raise ConnectivityFinalFreshnessIntegrityError("empty final freshness registry has non-genesis head")
        elif previous != head:
            raise ConnectivityFinalFreshnessIntegrityError("final freshness anchored head mismatch")
        return states, sequence, head


@dataclass(frozen=True, slots=True)
class ConnectivityFinalFreshnessResult:
    permit: ConnectivityFinalFreshnessPermit
    state: ConnectivityFinalFreshnessState
    fresh_account: AlpacaPaperAccountAttestation
    fresh_asset: AlpacaPaperEquityAssetAttestation
    fresh_flat: PaperFlatAccountAttestation
    fresh_market: AlpacaPaperEquityMarketAttestation
    fresh_risk_decision: RiskDecision
    artifact_path: Path


class ConnectivityFinalFreshnessGuard:
    """Reacquire fresh GET-only broker evidence and fresh deterministic Safety authority.

    This component deliberately ends before OMS staging. It has no writer and no
    POST API. The five broker reads are account, asset, positions, open orders and
    IEX snapshot. Initial preflight artifacts remain immutable and untouched.
    """

    def __init__(
        self,
        workspace: PaperOperationalWorkspace,
        *,
        account_gateway: AlpacaPaperAccountGateway | None = None,
        asset_gateway: AlpacaPaperEquityAssetGateway | None = None,
        flat_gateway: AlpacaPaperFlatAccountGateway | None = None,
        market_gateway: AlpacaPaperEquityMarketDataGateway | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace
        self._account_gateway = account_gateway or AlpacaPaperAccountGateway(
            config=AlpacaPaperGatewayConfig(enabled=True)
        )
        self._asset_gateway = asset_gateway or AlpacaPaperEquityAssetGateway(
            config=AlpacaPaperGatewayConfig(enabled=True)
        )
        self._flat_gateway = flat_gateway or AlpacaPaperFlatAccountGateway(
            config=AlpacaPaperGatewayConfig(enabled=True)
        )
        self._market_gateway = market_gateway or AlpacaPaperEquityMarketDataGateway(
            AlpacaPaperMarketDataConfig(enabled=True)
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def artifact_path(self) -> Path:
        return self._workspace.root / _ARTIFACT

    @property
    def registry_path(self) -> Path:
        return self._workspace.root / _REGISTRY_DB

    def acquire(self, *, credentials: AlpacaPaperCredentials) -> ConnectivityFinalFreshnessResult:
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise TypeError("AlpacaPaperCredentials are required")
        if self.artifact_path.exists() or self.registry_path.exists():
            raise ConnectivityFinalFreshnessRejected(
                "final freshness already exists; never refresh in-place"
            )
        started_at = self._now()
        operator_state = self._load_operator_state(now=started_at)
        operator = operator_state.decision
        context = operator.context
        preparation = self._load_preparation(context.connectivity_preparation_hash)
        runtime = SQLiteRuntime(self._workspace.core_db_path)
        self._verify_static_local_state(
            runtime=runtime,
            operator=operator,
            preparation=preparation,
        )
        initial_account = self._read_initial_account()
        if initial_account.fingerprint != context.account_attestation_fingerprint:
            raise ConnectivityFinalFreshnessRejected("initial account/operator binding mismatch")
        if credentials.credential_reference != initial_account.credential_reference:
            raise ConnectivityFinalFreshnessRejected(
                "current credentials do not match the originally attested PAPER credential reference"
            )
        initial_asset = PaperAssetEvidenceStore(self._workspace).read()
        if initial_asset.account_attestation_fingerprint != initial_account.fingerprint:
            raise ConnectivityFinalFreshnessRejected("initial asset/account binding mismatch")

        order = SQLiteOrderStore(runtime).get_by_order_id(context.order_id)
        if order is None:
            raise ConnectivityFinalFreshnessRejected("connectivity OMS order is missing")
        if order.status is not OrderStatus.VALIDATED:
            raise ConnectivityFinalFreshnessRejected("connectivity OMS order must remain VALIDATED")
        if order.intent.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID:
            raise ConnectivityFinalFreshnessRejected("connectivity strategy purpose drifted")
        if order.intent.quantity != Decimal("1") or order.intent.side.value != "BUY" or order.intent.order_type.value != "LIMIT":
            raise ConnectivityFinalFreshnessRejected("connectivity order shape drifted")
        if intent_fingerprint(order.intent) != _required_hash(_mapping(preparation, "standard_prepared_package"), "intent_fingerprint"):
            raise ConnectivityFinalFreshnessRejected("connectivity intent fingerprint drifted")

        fresh_account = self._account_gateway.attest_account(
            credentials=credentials,
            expected_account_id=initial_account.account_id,
            now=self._now(),
        )
        self._validate_fresh_account(initial=initial_account, fresh=fresh_account)
        fresh_asset = self._asset_gateway.attest_asset(
            credentials=credentials,
            symbol=order.intent.symbol,
            account_attestation_fingerprint=fresh_account.fingerprint,
            expected_credential_reference=initial_account.credential_reference,
            now=self._now(),
        )
        self._validate_fresh_asset(initial=initial_asset, fresh=fresh_asset)
        fresh_flat = self._flat_gateway.attest_flatness(
            credentials=credentials,
            account_attestation_fingerprint=fresh_account.fingerprint,
            expected_credential_reference=initial_account.credential_reference,
            now=self._now(),
        )
        self._validate_fresh_flat(fresh=fresh_flat, account=fresh_account)
        fresh_market = self._market_gateway.attest_snapshot(
            credentials=credentials,
            symbol=order.intent.symbol,
            now=self._now(),
        )
        completed_at = self._now()
        if not operator.is_valid_at(completed_at):
            raise ConnectivityFinalFreshnessRejected(
                "human connectivity decision expired during final GET-only freshness acquisition"
            )
        self._validate_fresh_market(fresh=fresh_market, symbol=order.intent.symbol)

        # Recheck every mutable local control after network latency and before
        # creating the fresh Safety decision. No stale pre-network state is trusted.
        self._verify_static_local_state(
            runtime=runtime,
            operator=operator,
            preparation=preparation,
        )
        self._verify_submission_and_original_permit(
            operator=operator,
            preparation=preparation,
        )
        rules, binding = self._verify_instrument_and_binding(
            runtime=runtime,
            operator=operator,
            fresh_asset=fresh_asset,
        )

        package = _mapping(preparation, "standard_prepared_package")
        original_cap = _positive_decimal(package.get("effective_notional_cap"), "effective_notional_cap")
        fresh_cap = min(
            MAX_CONNECTIVITY_NOTIONAL,
            original_cap,
            fresh_account.portfolio_value * MAX_ACCOUNT_FRACTION,
            fresh_account.buying_power,
        )
        if not fresh_cap.is_finite() or fresh_cap <= 0:
            raise ConnectivityFinalFreshnessRejected("fresh connectivity cap is not positive")
        if order.intent.limit_price is None:
            raise ConnectivityFinalFreshnessRejected("connectivity LIMIT price disappeared")
        notional = order.intent.quantity * order.intent.limit_price
        if notional > fresh_cap:
            raise ConnectivityFinalFreshnessRejected(
                "prepared one-share notional exceeds freshly revalidated account cap"
            )
        if order.intent.limit_price % fresh_asset.price_increment != 0:
            raise ConnectivityFinalFreshnessRejected(
                "prepared LIMIT price no longer aligns to fresh asset price increment"
            )
        if order.intent.quantity < fresh_asset.min_order_size or order.intent.quantity % fresh_asset.min_trade_increment != 0:
            raise ConnectivityFinalFreshnessRejected(
                "prepared whole-share quantity no longer satisfies fresh asset constraints"
            )
        if rules.price_tick != fresh_asset.price_increment:
            raise ConnectivityFinalFreshnessRejected("fresh asset price increment drifted from prepared Instrument Master")
        if rules.quantity_step != Decimal("1") or rules.min_quantity != Decimal("1") or rules.max_quantity != Decimal("1"):
            raise ConnectivityFinalFreshnessRejected("prepared Instrument Master whole-share policy drifted")
        if rules.trading_status is not InstrumentTradingStatus.TRADING:
            raise ConnectivityFinalFreshnessRejected("prepared Instrument Master is not TRADING")
        if rules.fingerprint != binding.instrument_master_fingerprint:
            raise ConnectivityFinalFreshnessRejected("prepared Instrument Master fingerprint/binding mismatch")

        portfolio = PortfolioSnapshot(
            snapshot_id=f"r6-connectivity-final:{_hash({'account': fresh_account.fingerprint, 'flat': fresh_flat.fingerprint})[:24]}",
            equity=fresh_account.portfolio_value,
            gross_exposure=Decimal("0"),
            net_exposure=Decimal("0"),
            daily_pnl=Decimal("0"),
            drawdown=Decimal("0"),
            open_orders=0,
            signed_position_notional_by_symbol={},
            strategy_gross_exposure={},
            strategy_signed_position_notional_by_symbol={},
            reconciliation_ok=True,
            broker_state_known=True,
        )
        limits = SafetyLimits(
            limits_version=_FINAL_LIMITS_VERSION,
            allowed_symbols=frozenset({order.intent.symbol}),
            allowed_order_types=frozenset({order.intent.order_type}),
            max_order_notional=fresh_cap,
            max_position_notional=fresh_cap,
            max_strategy_gross_exposure=fresh_cap,
            max_portfolio_gross_exposure=fresh_cap,
            max_net_exposure=fresh_cap,
            max_leverage=MAX_ACCOUNT_FRACTION,
            max_daily_loss=Decimal("0.01"),
            max_drawdown=Decimal("0.0001"),
            max_open_orders=1,
            stale_market_data_ms=MAX_MARKET_AGE_SECONDS * 1000,
            price_deviation_bps=Decimal("100"),
            decision_ttl_ms=DECISION_TTL_MS,
        )
        ledger = SQLiteEventLedger(runtime)
        safety_store = SQLiteSafetyStateStore(runtime)
        decision = CapitalSafetyKernel(limits, ledger, state_store=safety_store).evaluate(
            intent=order.intent,
            market=fresh_market.market,
            portfolio=portfolio,
            now=completed_at,
        )
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise ConnectivityFinalFreshnessRejected(
                f"fresh Capital Safety rejected connectivity canary: {decision.reason_code}: {decision.reason_detail}"
            )
        if decision.approved_notional != notional:
            raise ConnectivityFinalFreshnessRejected("fresh Capital Safety approved notional mismatch")
        if decision.intent_fingerprint != intent_fingerprint(order.intent):
            raise ConnectivityFinalFreshnessRejected("fresh Capital Safety intent binding mismatch")
        if decision.market_fingerprint != market_fingerprint(fresh_market.market):
            raise ConnectivityFinalFreshnessRejected("fresh Capital Safety market binding mismatch")
        if not ledger.verify_integrity():
            raise ConnectivityFinalFreshnessRejected("core Event Ledger failed after fresh Safety decision")
        _checkpoint_core(runtime)
        core_hash = _file_sha256(self._workspace.core_db_path)

        expires_at = min(
            operator.expires_at.astimezone(timezone.utc),
            decision.valid_until.astimezone(timezone.utc),
            completed_at + timedelta(seconds=_FINAL_TTL_SECONDS),
        )
        if expires_at <= completed_at:
            raise ConnectivityFinalFreshnessRejected("no final freshness eligibility window remains")
        permit = _build_permit(
            order_id=order.order_id,
            client_order_id=context.client_order_id,
            attempt_id=context.attempt_id,
            operator=operator,
            operator_event_hash=operator_state.event_hash,
            preparation_hash=context.connectivity_preparation_hash,
            connectivity_binding_hash=binding.binding_hash,
            standard_package_hash=context.standard_package_hash,
            canary_approval_hash=context.canary_approval_hash,
            submission_binding_hash=context.submission_binding_hash,
            bracket_payload_hash=context.bracket_payload_hash,
            instrument_rules_fingerprint=binding.instrument_master_fingerprint,
            initial_account_fingerprint=initial_account.fingerprint,
            fresh_account=fresh_account,
            fresh_asset=fresh_asset,
            fresh_flat=fresh_flat,
            fresh_market=fresh_market,
            decision=decision,
            portfolio=portfolio,
            effective_notional_cap=fresh_cap,
            core_hash=core_hash,
            issued_at=completed_at,
            expires_at=expires_at,
        )
        registry = SQLiteConnectivityFinalFreshnessRegistry(SQLiteRuntime(self.registry_path))
        state = registry.issue(permit)
        artifact = self._artifact_payload(
            permit=permit,
            state=state,
            fresh_account=fresh_account,
            fresh_asset=fresh_asset,
            fresh_flat=fresh_flat,
            fresh_market=fresh_market,
            decision=decision,
        )
        _write_json_exclusive(self.artifact_path, artifact)

        # Prove this GET-only/freshness step did not cross into execution state.
        order_after = SQLiteOrderStore(runtime).get_by_order_id(order.order_id)
        if order_after is None or order_after.status is not OrderStatus.VALIDATED:
            raise ConnectivityFinalFreshnessRejected("final freshness unexpectedly changed OMS state")
        submission_after = SQLitePaperSubmissionRegistry(
            SQLiteRuntime(self._workspace.submission_db_path)
        ).get(order.order_id)
        if submission_after.status is not PaperSubmissionStatus.PREPARED or submission_after.attempt_count != 0:
            raise ConnectivityFinalFreshnessRejected("final freshness unexpectedly changed submission state")
        return ConnectivityFinalFreshnessResult(
            permit=permit,
            state=state,
            fresh_account=fresh_account,
            fresh_asset=fresh_asset,
            fresh_flat=fresh_flat,
            fresh_market=fresh_market,
            fresh_risk_decision=decision,
            artifact_path=self.artifact_path,
        )

    def _now(self) -> datetime:
        value = self._clock()
        _require_aware(value, "clock")
        return value.astimezone(timezone.utc)

    def _load_operator_state(self, *, now: datetime):
        registry_path = self._workspace.root / _OPERATOR_DB
        if not registry_path.is_file() or registry_path.is_symlink():
            raise ConnectivityFinalFreshnessRejected("connectivity human decision registry is missing")
        registry = SQLiteConnectivityOperatorDecisionRegistry(SQLiteRuntime(registry_path))
        states = registry.list_states()
        if len(states) != 1:
            raise ConnectivityFinalFreshnessRejected("exactly one connectivity human decision is required")
        state = states[0]
        if state.status is not ConnectivityOperatorDecisionStatus.ISSUED:
            raise ConnectivityFinalFreshnessRejected("connectivity human decision is not ISSUED")
        if not state.decision.is_valid_at(now):
            raise ConnectivityFinalFreshnessRejected("connectivity human decision is expired")
        artifact = _read_json(self._workspace.root / _OPERATOR_ARTIFACT, "connectivity operator decision")
        for key, expected in (
            ("schema_version", 1),
            ("environment", "PAPER"),
            ("purpose", "CONNECTIVITY_CANARY"),
            ("status", "ISSUED"),
            ("oms_staging_authorized", False),
            ("external_post_authorized", False),
            ("external_order_submitted", False),
            ("strategy_health_required", False),
            ("strategy_trading_authorized", False),
            ("capital_authority", "NONE"),
            ("live_trading", "BLOCKED"),
            ("next_action", "CONNECTIVITY_FINAL_FRESHNESS_REQUIRED"),
        ):
            if artifact.get(key) != expected:
                raise ConnectivityFinalFreshnessRejected(f"unsafe connectivity operator artifact field: {key}")
        decision_payload = artifact.get("decision")
        if not isinstance(decision_payload, dict) or decision_payload != state.decision.payload():
            raise ConnectivityFinalFreshnessRejected("operator artifact/registry decision mismatch")
        if artifact.get("event_hash") != state.event_hash:
            raise ConnectivityFinalFreshnessRejected("operator artifact/registry event mismatch")
        return state

    def _load_preparation(self, expected_hash: str) -> dict[str, object]:
        payload = _read_json(self._workspace.root / _PREPARATION_ARTIFACT, "connectivity preparation")
        observed = payload.get("preparation_hash")
        if observed != expected_hash or not isinstance(observed, str) or not _HASH_RE.fullmatch(observed):
            raise ConnectivityFinalFreshnessRejected("connectivity preparation/operator hash mismatch")
        body = dict(payload)
        body.pop("preparation_hash", None)
        if _hash(body) != observed:
            raise ConnectivityFinalFreshnessRejected("connectivity preparation hash mismatch")
        if payload.get("next_action") != "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED":
            raise ConnectivityFinalFreshnessRejected("connectivity preparation action drifted")
        if payload.get("external_post_authorized") is not False or payload.get("live_trading") != "BLOCKED":
            raise ConnectivityFinalFreshnessRejected("connectivity preparation authority drifted")
        return payload

    def _verify_static_local_state(
        self,
        *,
        runtime: SQLiteRuntime,
        operator: ConnectivityOperatorDecision,
        preparation: Mapping[str, object],
    ) -> None:
        if _file_sha256(self._workspace.core_db_path) != operator.context.core_db_sha256_after_preparation:
            raise ConnectivityFinalFreshnessRejected("core.sqlite3 changed after human connectivity authorization")
        binding = SQLiteConnectivityPreparationBindingStore(runtime).get_for_order(operator.context.order_id)
        if binding is None or binding.binding_hash != operator.context.connectivity_binding_hash:
            raise ConnectivityFinalFreshnessRejected("durable connectivity preparation binding drifted")
        package = _mapping(preparation, "standard_prepared_package")
        if _required_hash(package, "package_hash") != operator.context.standard_package_hash:
            raise ConnectivityFinalFreshnessRejected("prepared package/operator binding drifted")
        self._verify_submission_and_original_permit(operator=operator, preparation=preparation)

    def _verify_submission_and_original_permit(
        self,
        *,
        operator: ConnectivityOperatorDecision,
        preparation: Mapping[str, object],
    ) -> None:
        context = operator.context
        submission = SQLitePaperSubmissionRegistry(
            SQLiteRuntime(self._workspace.submission_db_path)
        ).get(context.order_id)
        if (
            submission.status is not PaperSubmissionStatus.PREPARED
            or submission.attempt_count != 0
            or submission.binding_hash != context.submission_binding_hash
            or submission.broker_order_id is not None
            or submission.broker_client_order_id is not None
        ):
            raise ConnectivityFinalFreshnessRejected("submission is no longer pristine PREPARED")
        permit = SQLitePaperCanaryPermitRegistry(
            SQLiteRuntime(self._workspace.permit_db_path)
        ).get(context.canary_approval_hash)
        if (
            permit.status is not PaperCanaryPermitStatus.ISSUED
            or permit.order_id != context.order_id
            or permit.client_order_id != context.client_order_id
            or permit.binding_hash != context.submission_binding_hash
            or permit.event_hash != context.permit_event_hash
            or permit.attempt_id is not None
        ):
            raise ConnectivityFinalFreshnessRejected("original preparation permit drifted")
        package = _mapping(preparation, "standard_prepared_package")
        if _required_hash(package, "canary_approval_hash") != permit.approval_hash:
            raise ConnectivityFinalFreshnessRejected("preparation canary approval/permit mismatch")

    def _verify_instrument_and_binding(
        self,
        *,
        runtime: SQLiteRuntime,
        operator: ConnectivityOperatorDecision,
        fresh_asset: AlpacaPaperEquityAssetAttestation,
    ):
        binding = SQLiteConnectivityPreparationBindingStore(runtime).get_for_order(operator.context.order_id)
        if binding is None:
            raise ConnectivityFinalFreshnessRejected("connectivity preparation binding is missing")
        authority = SQLiteConnectivityCanaryAuthorityStore(runtime).get_for_order(operator.context.order_id)
        if authority is None:
            raise ConnectivityFinalFreshnessRejected("connectivity candidate authority is missing")
        initial_asset = PaperAssetEvidenceStore(self._workspace).read()
        if authority.asset_attestation_fingerprint != initial_asset.fingerprint:
            raise ConnectivityFinalFreshnessRejected("candidate authority/initial asset mismatch")
        if authority.instrument_rules_fingerprint != binding.instrument_master_fingerprint:
            raise ConnectivityFinalFreshnessRejected("candidate authority/Instrument Master binding mismatch")
        rules = SQLiteInstrumentMaster(runtime).latest(
            venue="ALPACA_PAPER", symbol=fresh_asset.symbol
        )
        return rules, binding

    def _read_initial_account(self) -> AlpacaPaperAccountAttestation:
        raw = _read_json_object(self._workspace.account_attestation_path)
        try:
            return AlpacaPaperAccountAttestation(
                account_id=_required_str(raw, "account_id"),
                account_reference=_required_hash(raw, "account_reference"),
                credential_reference=_required_hash(raw, "credential_reference"),
                status=_required_str(raw, "status"),
                currency=_required_str(raw, "currency"),
                buying_power=_positive_or_zero_decimal(raw.get("buying_power"), "buying_power"),
                portfolio_value=_positive_or_zero_decimal(raw.get("portfolio_value"), "portfolio_value"),
                shorting_enabled=_required_bool(raw, "shorting_enabled"),
                attested_at=_datetime(raw.get("attested_at"), "attested_at"),
                request_id=_required_str(raw, "request_id"),
                source_host=_required_str(raw, "source_host"),
                source_path=_required_str(raw, "source_path"),
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise ConnectivityFinalFreshnessRejected("initial account evidence is invalid") from exc

    def _validate_fresh_account(
        self, *, initial: AlpacaPaperAccountAttestation, fresh: AlpacaPaperAccountAttestation
    ) -> None:
        if not isinstance(fresh, AlpacaPaperAccountAttestation):
            raise ConnectivityFinalFreshnessRejected("fresh account gateway returned invalid type")
        if fresh.source_host != ALPACA_PAPER_TRADING_HOST or fresh.source_path != ALPACA_PAPER_ACCOUNT_PATH:
            raise ConnectivityFinalFreshnessRejected("fresh account endpoint is not exact PAPER")
        if fresh.status != "ACTIVE" or fresh.currency != "USD":
            raise ConnectivityFinalFreshnessRejected("fresh account is not ACTIVE USD PAPER")
        if (
            fresh.account_id != initial.account_id
            or fresh.account_reference != initial.account_reference
            or fresh.credential_reference != initial.credential_reference
        ):
            raise ConnectivityFinalFreshnessRejected("fresh account identity/credential reference drifted")
        if fresh.portfolio_value <= 0 or fresh.buying_power <= 0:
            raise ConnectivityFinalFreshnessRejected("fresh PAPER capital fields must be positive")

    def _validate_fresh_asset(
        self, *, initial: AlpacaPaperEquityAssetAttestation, fresh: AlpacaPaperEquityAssetAttestation
    ) -> None:
        if not isinstance(fresh, AlpacaPaperEquityAssetAttestation):
            raise ConnectivityFinalFreshnessRejected("fresh asset gateway returned invalid type")
        stable_initial = (
            initial.symbol,
            initial.asset_id,
            initial.asset_class,
            initial.exchange,
            initial.fractionable,
            initial.min_order_size,
            initial.min_trade_increment,
            initial.price_increment,
            initial.attributes,
        )
        stable_fresh = (
            fresh.symbol,
            fresh.asset_id,
            fresh.asset_class,
            fresh.exchange,
            fresh.fractionable,
            fresh.min_order_size,
            fresh.min_trade_increment,
            fresh.price_increment,
            fresh.attributes,
        )
        if stable_fresh != stable_initial:
            raise ConnectivityFinalFreshnessRejected("fresh asset venue metadata drifted from prepared asset")
        if fresh.status != "active" or fresh.tradable is not True:
            raise ConnectivityFinalFreshnessRejected("fresh asset is no longer active/tradable")

    def _validate_fresh_flat(
        self, *, fresh: PaperFlatAccountAttestation, account: AlpacaPaperAccountAttestation
    ) -> None:
        if not isinstance(fresh, PaperFlatAccountAttestation):
            raise ConnectivityFinalFreshnessRejected("fresh flat-account gateway returned invalid type")
        if fresh.account_attestation_fingerprint != account.fingerprint:
            raise ConnectivityFinalFreshnessRejected("fresh flat/account binding mismatch")
        if fresh.credential_reference != account.credential_reference:
            raise ConnectivityFinalFreshnessRejected("fresh flat credential reference mismatch")
        if not fresh.clean_for_first_canary:
            raise ConnectivityFinalFreshnessRejected(
                "fresh PAPER account is not flat: positions/open orders exist"
            )

    def _validate_fresh_market(
        self, *, fresh: AlpacaPaperEquityMarketAttestation, symbol: str
    ) -> None:
        if not isinstance(fresh, AlpacaPaperEquityMarketAttestation):
            raise ConnectivityFinalFreshnessRejected("fresh market gateway returned invalid type")
        if fresh.market.symbol != symbol:
            raise ConnectivityFinalFreshnessRejected("fresh market symbol drifted")
        if fresh.feed != ALPACA_BASIC_EQUITY_FEED or fresh.currency != ALPACA_MARKET_DATA_CURRENCY:
            raise ConnectivityFinalFreshnessRejected("fresh market is not exact IEX/USD")

    def _artifact_payload(
        self,
        *,
        permit: ConnectivityFinalFreshnessPermit,
        state: ConnectivityFinalFreshnessState,
        fresh_account: AlpacaPaperAccountAttestation,
        fresh_asset: AlpacaPaperEquityAssetAttestation,
        fresh_flat: PaperFlatAccountAttestation,
        fresh_market: AlpacaPaperEquityMarketAttestation,
        decision: RiskDecision,
    ) -> dict[str, object]:
        account_payload = account_attestation_payload(fresh_account)
        market_payload = market_evidence_payload(fresh_market)
        return {
            "schema_version": 1,
            "environment": "PAPER",
            "purpose": "CONNECTIVITY_CANARY",
            "status": state.status.value,
            "permit": permit.payload(),
            "registry_event_hash": state.event_hash,
            "fresh_account": account_payload,
            "fresh_asset": {**fresh_asset.to_dict(), "attestation_fingerprint": fresh_asset.fingerprint},
            "fresh_flat": {**fresh_flat.to_dict(), "attestation_fingerprint": fresh_flat.fingerprint},
            "fresh_market": market_payload,
            "fresh_risk_decision": _risk_payload(decision),
            "network_methods": ["GET", "GET", "GET", "GET", "GET"],
            "network_read_count": 5,
            "credentials_persisted": False,
            "initial_preflight_artifacts_modified": False,
            "oms_staging_authorized": False,
            "external_post_authorized": False,
            "external_order_submitted": False,
            "strategy_health_required": False,
            "strategy_health_created": False,
            "strategy_trading_authorized": False,
            "capital_authority": "NONE",
            "profitability_claim": False,
            "live_trading": "BLOCKED",
            "next_action": "EXPLICIT_CONNECTIVITY_EXECUTION_DECISION_REQUIRED",
        }


def _build_permit(
    *,
    order_id: str,
    client_order_id: str,
    attempt_id: str,
    operator: ConnectivityOperatorDecision,
    operator_event_hash: str,
    preparation_hash: str,
    connectivity_binding_hash: str,
    standard_package_hash: str,
    canary_approval_hash: str,
    submission_binding_hash: str,
    bracket_payload_hash: str,
    instrument_rules_fingerprint: str,
    initial_account_fingerprint: str,
    fresh_account: AlpacaPaperAccountAttestation,
    fresh_asset: AlpacaPaperEquityAssetAttestation,
    fresh_flat: PaperFlatAccountAttestation,
    fresh_market: AlpacaPaperEquityMarketAttestation,
    decision: RiskDecision,
    portfolio: PortfolioSnapshot,
    effective_notional_cap: Decimal,
    core_hash: str,
    issued_at: datetime,
    expires_at: datetime,
) -> ConnectivityFinalFreshnessPermit:
    values: dict[str, object] = {
        "order_id": order_id,
        "client_order_id": client_order_id,
        "attempt_id": attempt_id,
        "operator_context_hash": operator.context.context_hash,
        "operator_decision_hash": operator.decision_hash,
        "operator_event_hash": operator_event_hash,
        "preparation_hash": preparation_hash,
        "connectivity_binding_hash": connectivity_binding_hash,
        "standard_package_hash": standard_package_hash,
        "canary_approval_hash": canary_approval_hash,
        "submission_binding_hash": submission_binding_hash,
        "bracket_payload_hash": bracket_payload_hash,
        "instrument_rules_fingerprint": instrument_rules_fingerprint,
        "initial_account_fingerprint": initial_account_fingerprint,
        "fresh_account_fingerprint": fresh_account.fingerprint,
        "fresh_asset_fingerprint": fresh_asset.fingerprint,
        "fresh_flat_fingerprint": fresh_flat.fingerprint,
        "fresh_market_attestation_fingerprint": fresh_market.fingerprint,
        "fresh_market_fingerprint": market_fingerprint(fresh_market.market),
        "fresh_risk_decision_id": decision.decision_id,
        "fresh_risk_decision_fingerprint": risk_decision_fingerprint(decision),
        "safety_state_version": decision.safety_state_version,
        "fresh_portfolio_snapshot_id": portfolio.snapshot_id,
        "effective_notional_cap": effective_notional_cap,
        "core_db_sha256_after_fresh_safety": core_hash,
        "issued_at": issued_at.astimezone(timezone.utc),
        "expires_at": expires_at.astimezone(timezone.utc),
    }
    payload = _permit_payload_from_values(values)
    values["permit_hash"] = _hash(payload)
    return ConnectivityFinalFreshnessPermit(**values)  # type: ignore[arg-type]


def _permit_payload(
    permit: ConnectivityFinalFreshnessPermit, *, include_hash: bool
) -> dict[str, object]:
    values = {
        "order_id": permit.order_id,
        "client_order_id": permit.client_order_id,
        "attempt_id": permit.attempt_id,
        "operator_context_hash": permit.operator_context_hash,
        "operator_decision_hash": permit.operator_decision_hash,
        "operator_event_hash": permit.operator_event_hash,
        "preparation_hash": permit.preparation_hash,
        "connectivity_binding_hash": permit.connectivity_binding_hash,
        "standard_package_hash": permit.standard_package_hash,
        "canary_approval_hash": permit.canary_approval_hash,
        "submission_binding_hash": permit.submission_binding_hash,
        "bracket_payload_hash": permit.bracket_payload_hash,
        "instrument_rules_fingerprint": permit.instrument_rules_fingerprint,
        "initial_account_fingerprint": permit.initial_account_fingerprint,
        "fresh_account_fingerprint": permit.fresh_account_fingerprint,
        "fresh_asset_fingerprint": permit.fresh_asset_fingerprint,
        "fresh_flat_fingerprint": permit.fresh_flat_fingerprint,
        "fresh_market_attestation_fingerprint": permit.fresh_market_attestation_fingerprint,
        "fresh_market_fingerprint": permit.fresh_market_fingerprint,
        "fresh_risk_decision_id": permit.fresh_risk_decision_id,
        "fresh_risk_decision_fingerprint": permit.fresh_risk_decision_fingerprint,
        "safety_state_version": permit.safety_state_version,
        "fresh_portfolio_snapshot_id": permit.fresh_portfolio_snapshot_id,
        "effective_notional_cap": permit.effective_notional_cap,
        "core_db_sha256_after_fresh_safety": permit.core_db_sha256_after_fresh_safety,
        "issued_at": permit.issued_at,
        "expires_at": permit.expires_at,
    }
    payload = _permit_payload_from_values(values)
    if include_hash:
        payload["permit_hash"] = permit.permit_hash
    return payload


def _permit_payload_from_values(values: Mapping[str, object]) -> dict[str, object]:
    return {
        "order_id": values["order_id"],
        "client_order_id": values["client_order_id"],
        "attempt_id": values["attempt_id"],
        "operator_context_hash": values["operator_context_hash"],
        "operator_decision_hash": values["operator_decision_hash"],
        "operator_event_hash": values["operator_event_hash"],
        "preparation_hash": values["preparation_hash"],
        "connectivity_binding_hash": values["connectivity_binding_hash"],
        "standard_package_hash": values["standard_package_hash"],
        "canary_approval_hash": values["canary_approval_hash"],
        "submission_binding_hash": values["submission_binding_hash"],
        "bracket_payload_hash": values["bracket_payload_hash"],
        "instrument_rules_fingerprint": values["instrument_rules_fingerprint"],
        "initial_account_fingerprint": values["initial_account_fingerprint"],
        "fresh_account_fingerprint": values["fresh_account_fingerprint"],
        "fresh_asset_fingerprint": values["fresh_asset_fingerprint"],
        "fresh_flat_fingerprint": values["fresh_flat_fingerprint"],
        "fresh_market_attestation_fingerprint": values["fresh_market_attestation_fingerprint"],
        "fresh_market_fingerprint": values["fresh_market_fingerprint"],
        "fresh_risk_decision_id": values["fresh_risk_decision_id"],
        "fresh_risk_decision_fingerprint": values["fresh_risk_decision_fingerprint"],
        "safety_state_version": values["safety_state_version"],
        "fresh_portfolio_snapshot_id": values["fresh_portfolio_snapshot_id"],
        "effective_notional_cap": str(values["effective_notional_cap"]),
        "core_db_sha256_after_fresh_safety": values["core_db_sha256_after_fresh_safety"],
        "issued_at": _iso(values["issued_at"]),
        "expires_at": _iso(values["expires_at"]),
        "eligibility_ttl_seconds": _FINAL_TTL_SECONDS,
        "oms_staging_authorized": False,
        "external_post_authorized": False,
        "capital_authority": "NONE",
        "live_trading": "BLOCKED",
    }


def _permit_from_payload(payload: Mapping[str, object]) -> ConnectivityFinalFreshnessPermit:
    expected = {
        "order_id", "client_order_id", "attempt_id", "operator_context_hash",
        "operator_decision_hash", "operator_event_hash", "preparation_hash",
        "connectivity_binding_hash", "standard_package_hash", "canary_approval_hash",
        "submission_binding_hash", "bracket_payload_hash", "instrument_rules_fingerprint",
        "initial_account_fingerprint", "fresh_account_fingerprint", "fresh_asset_fingerprint",
        "fresh_flat_fingerprint", "fresh_market_attestation_fingerprint", "fresh_market_fingerprint",
        "fresh_risk_decision_id", "fresh_risk_decision_fingerprint", "safety_state_version",
        "fresh_portfolio_snapshot_id", "effective_notional_cap", "core_db_sha256_after_fresh_safety",
        "issued_at", "expires_at", "eligibility_ttl_seconds", "oms_staging_authorized",
        "external_post_authorized", "capital_authority", "live_trading", "permit_hash",
    }
    if set(payload) != expected:
        raise ConnectivityFinalFreshnessIntegrityError("final freshness permit payload is non-canonical")
    for key, expected_value in (
        ("eligibility_ttl_seconds", _FINAL_TTL_SECONDS),
        ("oms_staging_authorized", False),
        ("external_post_authorized", False),
        ("capital_authority", "NONE"),
        ("live_trading", "BLOCKED"),
    ):
        if payload.get(key) != expected_value:
            raise ConnectivityFinalFreshnessIntegrityError(f"unsafe final freshness permit field: {key}")
    try:
        return ConnectivityFinalFreshnessPermit(
            order_id=_required_str(payload, "order_id"),
            client_order_id=_required_str(payload, "client_order_id"),
            attempt_id=_required_str(payload, "attempt_id"),
            operator_context_hash=_required_hash(payload, "operator_context_hash"),
            operator_decision_hash=_required_hash(payload, "operator_decision_hash"),
            operator_event_hash=_required_hash(payload, "operator_event_hash"),
            preparation_hash=_required_hash(payload, "preparation_hash"),
            connectivity_binding_hash=_required_hash(payload, "connectivity_binding_hash"),
            standard_package_hash=_required_hash(payload, "standard_package_hash"),
            canary_approval_hash=_required_hash(payload, "canary_approval_hash"),
            submission_binding_hash=_required_hash(payload, "submission_binding_hash"),
            bracket_payload_hash=_required_hash(payload, "bracket_payload_hash"),
            instrument_rules_fingerprint=_required_hash(payload, "instrument_rules_fingerprint"),
            initial_account_fingerprint=_required_hash(payload, "initial_account_fingerprint"),
            fresh_account_fingerprint=_required_hash(payload, "fresh_account_fingerprint"),
            fresh_asset_fingerprint=_required_hash(payload, "fresh_asset_fingerprint"),
            fresh_flat_fingerprint=_required_hash(payload, "fresh_flat_fingerprint"),
            fresh_market_attestation_fingerprint=_required_hash(payload, "fresh_market_attestation_fingerprint"),
            fresh_market_fingerprint=_required_hash(payload, "fresh_market_fingerprint"),
            fresh_risk_decision_id=_required_str(payload, "fresh_risk_decision_id"),
            fresh_risk_decision_fingerprint=_required_hash(payload, "fresh_risk_decision_fingerprint"),
            safety_state_version=_required_int(payload, "safety_state_version"),
            fresh_portfolio_snapshot_id=_required_str(payload, "fresh_portfolio_snapshot_id"),
            effective_notional_cap=_positive_decimal(payload.get("effective_notional_cap"), "effective_notional_cap"),
            core_db_sha256_after_fresh_safety=_required_hash(payload, "core_db_sha256_after_fresh_safety"),
            issued_at=_datetime(payload.get("issued_at"), "issued_at"),
            expires_at=_datetime(payload.get("expires_at"), "expires_at"),
            permit_hash=_required_hash(payload, "permit_hash"),
        )
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ConnectivityFinalFreshnessIntegrityError("invalid final freshness permit") from exc


def _risk_payload(decision: RiskDecision) -> dict[str, object]:
    return {
        "decision_id": decision.decision_id,
        "intent_id": decision.intent_id,
        "status": decision.status.value,
        "reason_code": decision.reason_code,
        "reason_detail": decision.reason_detail,
        "evaluated_at": decision.evaluated_at.isoformat(),
        "valid_until": decision.valid_until.isoformat(),
        "limits_version": decision.limits_version,
        "intent_fingerprint": decision.intent_fingerprint,
        "market_fingerprint": decision.market_fingerprint,
        "approved_notional": str(decision.approved_notional) if decision.approved_notional is not None else None,
        "risk_reducing": decision.risk_reducing,
        "safety_state_version": decision.safety_state_version,
        "risk_decision_fingerprint": risk_decision_fingerprint(decision),
    }


def _checkpoint_core(runtime: SQLiteRuntime) -> None:
    conn = runtime.connect()
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is not None and int(row[0]) != 0:
            raise ConnectivityFinalFreshnessRejected("cannot checkpoint core after fresh Safety decision")
    finally:
        conn.close()


def _write_json_exclusive(path: Path, payload: Mapping[str, object]) -> None:
    if path.exists():
        raise ConnectivityFinalFreshnessConflict(f"refusing to overwrite {path.name}")
    if path.is_symlink():
        raise ConnectivityFinalFreshnessConflict(f"{path.name} cannot be symlink")
    raw = json.dumps(dict(payload), sort_keys=True, indent=2, allow_nan=False) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)
    path.chmod(0o600)


def _read_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ConnectivityFinalFreshnessRejected(f"{label} must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectivityFinalFreshnessRejected(f"cannot read {label}") from exc
    if not isinstance(payload, dict):
        raise ConnectivityFinalFreshnessRejected(f"{label} root must be object")
    return payload


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConnectivityFinalFreshnessRejected(f"{key} must be object")
    return value


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ConnectivityFinalFreshnessRejected(f"{key} must be non-empty string")
    return value


def _required_hash(payload: Mapping[str, object], key: str) -> str:
    value = _required_str(payload, key)
    if not _HASH_RE.fullmatch(value):
        raise ConnectivityFinalFreshnessRejected(f"{key} must be lowercase SHA-256")
    return value


def _required_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ConnectivityFinalFreshnessRejected(f"{key} must be boolean")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConnectivityFinalFreshnessRejected(f"{key} must be integer")
    return value


def _positive_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ConnectivityFinalFreshnessRejected(f"{label} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed <= 0:
        raise ConnectivityFinalFreshnessRejected(f"{label} must be finite and positive")
    return parsed


def _positive_or_zero_decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ConnectivityFinalFreshnessRejected(f"{label} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or parsed < 0:
        raise ConnectivityFinalFreshnessRejected(f"{label} must be finite and non-negative")
    return parsed


def _datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be datetime string")
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _strict_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConnectivityFinalFreshnessIntegrityError(f"{label} must be integer")
    return value


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} must be canonical identifier")


def _validate_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: object) -> str:
    if not isinstance(value, datetime):
        raise ValueError("datetime value is required")
    _require_aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat()


def _json_object(raw: str, label: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectivityFinalFreshnessIntegrityError(f"{label} JSON is invalid") from exc
    if not isinstance(payload, dict):
        raise ConnectivityFinalFreshnessIntegrityError(f"{label} must be object")
    return payload


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _control_hash(sequence: int, head: str) -> str:
    return _hash({"event_sequence": sequence, "event_head_hash": head})


def _event_hash(
    *,
    sequence: int,
    permit_hash: str,
    occurred_at: datetime,
    payload_json: str,
    previous_event_hash: str,
) -> str:
    return _hash(
        {
            "sequence": sequence,
            "event_type": ConnectivityFinalFreshnessStatus.ISSUED.value,
            "permit_hash": permit_hash,
            "occurred_at": _iso(occurred_at),
            "payload_json": payload_json,
            "previous_event_hash": previous_event_hash,
        }
    )


__all__ = [
    "ConnectivityFinalFreshnessConflict",
    "ConnectivityFinalFreshnessError",
    "ConnectivityFinalFreshnessGuard",
    "ConnectivityFinalFreshnessIntegrityError",
    "ConnectivityFinalFreshnessPermit",
    "ConnectivityFinalFreshnessRejected",
    "ConnectivityFinalFreshnessResult",
    "ConnectivityFinalFreshnessState",
    "ConnectivityFinalFreshnessStatus",
    "SQLiteConnectivityFinalFreshnessRegistry",
]
