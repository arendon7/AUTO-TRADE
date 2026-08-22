from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import os
from pathlib import Path
import secrets
import time
from typing import Callable

from autotrade.brokers.alpaca_paper_crypto_asset import AlpacaPaperCryptoAssetGateway
from autotrade.brokers.alpaca_paper_crypto_market_data import (
    AlpacaPaperCryptoMarketAttestation,
    AlpacaPaperCryptoMarketDataConfig,
    AlpacaPaperCryptoMarketDataGateway,
)
from autotrade.brokers.alpaca_paper_gateway import (
    AlpacaPaperCredentials,
    AlpacaPaperGatewayConfig,
)
from autotrade.brokers.paper_portfolio import AlpacaPaperPortfolioGateway
from autotrade.domain import MarketSnapshot, OrderType
from autotrade.paper_close_attempt import (
    PaperCloseAttemptWorkspace,
    pending_burned_close_attempts,
)
from autotrade.paper_close_control_plane import (
    PreparedPaperCloseControlPlane,
    R7RiskReducingOrderManagementSystem,
    prepare_paper_close_control_plane,
)
from autotrade.paper_close_execution_bridge import (
    PaperCloseExecutionBridge,
    bind_paper_close_execution_authority,
)
from autotrade.paper_close_lifecycle import (
    PaperCloseLifecycleStatus,
    SQLitePaperCloseLifecycle,
)
from autotrade.paper_close_plan import (
    MAX_CLOSE_SLIPPAGE_BPS,
    PaperCloseMode,
    PaperCryptoClosePlan,
    prepare_crypto_close_plan,
)
from autotrade.paper_close_reconciliation import (
    AlpacaPaperCloseReconciliationGateway,
    PaperCloseReconciliation,
)
from autotrade.paper_close_writer import (
    PaperCloseOperatorDecision,
    PaperCloseWriteReceipt,
    PaperCloseWriter,
    PaperCloseWriterConfig,
    issue_paper_close_operator_decision,
)
from autotrade.paper_operations_read_model import (
    PaperOperationsReadModel,
    PaperOperationsSnapshot,
    read_paper_safety_snapshot,
    read_workspace_paper_account,
)
from autotrade.persistence import SQLiteEventLedger, SQLiteOrderStore, SQLiteRuntime
from autotrade.safety import CapitalSafetyKernel, SafetyLimits
from autotrade.state import SafetyControlState, SafetyStateStore


CLOSE_WRITE_ENV = "R7_CLOSE_PAPER_WRITE"
FIRST_CLOSE_SYMBOL = "BTC/USD"
FIRST_CLOSE_SLIPPAGE_BPS = Decimal("25")
AUTO_RECONCILE_DELAYS_SECONDS = (0.0, 1.0, 2.0, 4.0)


class PaperCloseOperatorError(RuntimeError):
    pass


class PaperCloseOperatorBlocked(PaperCloseOperatorError):
    pass


class _NoBrokerSurface:
    def submit(self, **_kwargs):
        raise PaperCloseOperatorBlocked("R7 close control plane has no generic broker submission surface")


class ReadOnlyCanonicalSafetyStateStore(SafetyStateStore):
    """Read-through canonical Safety state; all mutation methods are denied."""

    def __init__(self, *, workspace_path: Path, now_provider: Callable[[], datetime]) -> None:
        self._workspace = workspace_path
        self._now_provider = now_provider

    def get(self) -> SafetyControlState:
        return read_paper_safety_snapshot(
            self._workspace,
            now=_aware(self._now_provider()),
        ).state

    def activate(self, *, reason: str, now: datetime) -> SafetyControlState:
        del reason, now
        raise PaperCloseOperatorBlocked("R7 close Safety store is read-only")

    def reset(self, *, now: datetime) -> SafetyControlState:
        del now
        raise PaperCloseOperatorBlocked("R7 close Safety store is read-only")

    def activate_circuit(self, *, reason: str, now: datetime) -> SafetyControlState:
        del reason, now
        raise PaperCloseOperatorBlocked("R7 close Safety store is read-only")

    def acknowledge_circuit(self, *, reason: str, now: datetime) -> SafetyControlState:
        del reason, now
        raise PaperCloseOperatorBlocked("R7 close Safety store is read-only")


@dataclass(slots=True)
class PreparedPaperCloseOperatorSession:
    attempt: PaperCloseAttemptWorkspace
    plan: PaperCryptoClosePlan
    operations: PaperOperationsSnapshot
    market_attestation: AlpacaPaperCryptoMarketAttestation
    control_plane: PreparedPaperCloseControlPlane | None
    oms: R7RiskReducingOrderManagementSystem | None
    lifecycle: SQLitePaperCloseLifecycle
    decision: PaperCloseOperatorDecision | None = None

    def summary(self) -> dict[str, object]:
        source = self.operations.close_source
        return {
            "attempt_id_internal": self.attempt.attempt_id,
            "environment": "PAPER",
            "symbol": self.plan.symbol,
            "mode": self.plan.mode.value,
            "side": "SELL",
            "order_type": "LIMIT",
            "time_in_force": "IOC",
            "quantity": str(self.plan.quantity),
            "reference_price": str(self.plan.reference_price),
            "limit_price": str(self.plan.limit_price),
            "estimated_notional_usd": str(self.plan.quantity * self.plan.limit_price),
            "max_slippage_bps": str(self.plan.max_slippage_bps),
            "source_attempt_id": None if source is None else source.source.attempt_id,
            "source_strategy_id": None if source is None else source.source.strategy_id,
            "plan_expires_at": self.plan.expires_at.astimezone(timezone.utc).isoformat(),
            "risk_reducing": True,
            "network_write_authorized": False,
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        }


class PaperCloseOperator:
    """Production facade for one risk-reducing R7 PAPER close attempt.

    The first operator close is FULL BTC/USD only. It never retries a POST. A
    restart after the writer crosses durable UNKNOWN can only enter ``recover``.
    """

    def __init__(
        self,
        *,
        workspace_path: Path,
        now_provider: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        operations_reader: PaperOperationsReadModel | None = None,
        asset_reader: AlpacaPaperCryptoAssetGateway | None = None,
        market_reader: AlpacaPaperCryptoMarketDataGateway | None = None,
        portfolio_reader: AlpacaPaperPortfolioGateway | None = None,
        writer_factory: Callable[[], PaperCloseWriter] | None = None,
        reconciliation_factory: Callable[[], AlpacaPaperCloseReconciliationGateway] | None = None,
        safety_store_factory: Callable[[Path, Callable[[], datetime]], SafetyStateStore] | None = None,
    ) -> None:
        if not isinstance(workspace_path, Path):
            raise TypeError("workspace_path must be pathlib.Path")
        expanded = workspace_path.expanduser()
        if expanded.is_symlink() or not expanded.is_dir():
            raise PaperCloseOperatorBlocked("existing non-symlink PAPER workspace is required")
        self.workspace = expanded.resolve()
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        enabled = AlpacaPaperGatewayConfig(enabled=True)
        self._operations_reader = operations_reader or PaperOperationsReadModel(workspace_path=self.workspace)
        self._asset_reader = asset_reader or AlpacaPaperCryptoAssetGateway(config=enabled)
        self._market_reader = market_reader or AlpacaPaperCryptoMarketDataGateway(
            AlpacaPaperCryptoMarketDataConfig(enabled=True)
        )
        self._portfolio_reader = portfolio_reader or AlpacaPaperPortfolioGateway(config=enabled)
        self._writer_factory = writer_factory or (
            lambda: PaperCloseWriter(config=PaperCloseWriterConfig(enabled=True))
        )
        self._reconciliation_factory = reconciliation_factory or (
            lambda: AlpacaPaperCloseReconciliationGateway(
                config=AlpacaPaperGatewayConfig(enabled=True),
                portfolio_gateway=self._portfolio_reader,
            )
        )
        self._safety_store_factory = safety_store_factory or (
            lambda workspace, clock: ReadOnlyCanonicalSafetyStateStore(
                workspace_path=workspace,
                now_provider=clock,
            )
        )

    def pending_recovery_attempt(self) -> str | None:
        pending = pending_burned_close_attempts(workspace_path=self.workspace)
        if len(pending) > 1:
            raise PaperCloseOperatorBlocked(
                "multiple burned R7 close attempts require manual reconciliation; no new write authority"
            )
        return pending[0] if pending else None

    def prepare_full_close(self, *, credentials: AlpacaPaperCredentials) -> PreparedPaperCloseOperatorSession:
        self._require_credentials(credentials)
        pending = self.pending_recovery_attempt()
        if pending is not None:
            raise PaperCloseOperatorBlocked(
                "a burned R7 close attempt is unresolved; reconcile it by GET before preparing another close"
            )

        operations = self._operations_reader.snapshot(
            credentials=credentials,
            now=self._now(),
        )
        if not operations.ready_for_close_preparation or operations.close_source is None:
            detail = ", ".join(operations.blockers) if operations.blockers else "source provenance unavailable"
            raise PaperCloseOperatorBlocked(f"fresh PAPER exposure is not ready for certified close: {detail}")
        if len(operations.portfolio.positions) != 1 or operations.portfolio.open_orders:
            raise PaperCloseOperatorBlocked("first R7 close requires one position and zero open orders")
        position = operations.portfolio.positions[0]
        if position.symbol != FIRST_CLOSE_SYMBOL or position.asset_class != "crypto" or position.side != "long":
            raise PaperCloseOperatorBlocked("first R7 close is restricted to the existing positive BTC/USD exposure")
        if position.available_quantity != position.quantity:
            raise PaperCloseOperatorBlocked("full close requires the complete broker position quantity to be available")

        durable_account = operations.account_anchor.attestation
        fresh_account = operations.portfolio.account
        if (
            fresh_account.account_id != durable_account.account_id
            or fresh_account.account_reference != durable_account.account_reference
        ):
            raise PaperCloseOperatorBlocked(
                "fresh PAPER account does not match durable workspace account evidence"
            )
        if fresh_account.credential_reference != credentials.credential_reference:
            raise PaperCloseOperatorBlocked(
                "fresh PAPER account credential binding does not match current credentials"
            )

        asset = self._asset_reader.attest_asset(
            credentials=credentials,
            account_attestation_fingerprint=fresh_account.fingerprint,
            expected_credential_reference=fresh_account.credential_reference,
            now=self._now(),
            symbol=FIRST_CLOSE_SYMBOL,
        )
        market_attestation = self._market_reader.attest_snapshot(
            credentials=credentials,
            now=self._now(),
            symbol=FIRST_CLOSE_SYMBOL,
        )
        market = market_attestation.market
        minimum_limit = position.current_price * (
            Decimal("1") - FIRST_CLOSE_SLIPPAGE_BPS / Decimal("10000")
        )
        candidate_limit = _ceil_to_increment(minimum_limit, asset.price_increment)
        if candidate_limit > market.bid:
            raise PaperCloseOperatorBlocked(
                "fresh BTC/USD bid is already below the 25 bps close floor; prepare again when a marketable limit exists"
            )
        plan = prepare_crypto_close_plan(
            portfolio=operations.portfolio,
            symbol=FIRST_CLOSE_SYMBOL,
            now=self._now(),
            quantity=None,
            limit_price=candidate_limit,
            max_slippage_bps=FIRST_CLOSE_SLIPPAGE_BPS,
        )
        if plan.mode is not PaperCloseMode.FULL or plan.quantity != position.available_quantity:
            raise PaperCloseOperatorBlocked("operator surface may prepare FULL close only")
        if plan.max_slippage_bps > MAX_CLOSE_SLIPPAGE_BPS:
            raise PaperCloseOperatorBlocked("close slippage exceeds hard R7 bound")

        attempt_id = "r7-close-" + secrets.token_hex(16)
        attempt = PaperCloseAttemptWorkspace.create(
            workspace_path=self.workspace,
            attempt_id=attempt_id,
        )
        attempt.write_plan(plan)
        runtime = SQLiteRuntime(attempt.database_path)
        lifecycle = SQLitePaperCloseLifecycle(runtime)
        lifecycle.prepare(attempt_id=attempt_id, plan=plan, at=self._now())
        return PreparedPaperCloseOperatorSession(
            attempt=attempt,
            plan=plan,
            operations=operations,
            market_attestation=market_attestation,
            control_plane=None,
            oms=None,
            lifecycle=lifecycle,
        )

    def approve(
        self,
        *,
        prepared: PreparedPaperCloseOperatorSession,
    ) -> PaperCloseOperatorDecision:
        if not isinstance(prepared, PreparedPaperCloseOperatorSession):
            raise PaperCloseOperatorBlocked("exact in-memory close preparation is required")
        decision = issue_paper_close_operator_decision(
            attempt_id=prepared.attempt.attempt_id,
            plan=prepared.plan,
            confirmation="CERRAR PAPER",
            now=self._now(),
        )
        prepared.decision = decision
        return decision

    def execute_once(
        self,
        *,
        prepared: PreparedPaperCloseOperatorSession,
        credentials: AlpacaPaperCredentials,
    ) -> dict[str, object]:
        self._require_credentials(credentials)
        if os.environ.get(CLOSE_WRITE_ENV, "DISABLED") != "ENABLED":
            raise PaperCloseOperatorBlocked("R7 close PAPER write gate is disabled")
        if not isinstance(prepared, PreparedPaperCloseOperatorSession) or prepared.decision is None:
            raise PaperCloseOperatorBlocked("fresh approved close preparation is required")
        state = prepared.lifecycle.snapshot(prepared.attempt.attempt_id).state
        if state.status is not PaperCloseLifecycleStatus.PREPARED or state.submission_attempt_count != 0:
            raise PaperCloseOperatorBlocked("close attempt is no longer eligible for its one POST")

        control_plane, oms, final_market = self._build_fresh_execution_control_plane(
            prepared=prepared,
            credentials=credentials,
        )
        prepared.control_plane = control_plane
        prepared.oms = oms
        prepared.market_attestation = final_market

        stage_time = self._now()
        _, handoff = oms.stage_risk_reducing_external_submission(
            prepared=control_plane,
            market=final_market.market,
            now=stage_time,
        )
        authority = bind_paper_close_execution_authority(
            plan=prepared.plan,
            operator_decision=prepared.decision,
            control_plane=control_plane,
            oms_handoff=handoff,
            now=self._now(),
        )
        fresh_portfolio = self._portfolio_reader.snapshot(
            credentials=credentials,
            expected_account_id=prepared.operations.account_anchor.attestation.account_id,
            now=self._now(),
        )
        writer = self._writer_factory()
        bridge = PaperCloseExecutionBridge(writer=writer)
        receipt: PaperCloseWriteReceipt | None = None
        try:
            receipt = bridge.execute_once(
                authority=authority,
                plan=prepared.plan,
                operator_decision=prepared.decision,
                control_plane=control_plane,
                lifecycle=prepared.lifecycle,
                fresh_portfolio=fresh_portfolio,
                credentials=credentials,
                now=self._now(),
            )
            prepared.attempt.write_receipt(_receipt_document(receipt))
            settlement = self._bounded_reconcile(
                attempt=prepared.attempt,
                plan=prepared.plan,
                credentials=credentials,
                expected_account_id=prepared.operations.account_anchor.attestation.account_id,
            )
        except Exception as exc:
            burned = prepared.lifecycle.snapshot(prepared.attempt.attempt_id).state
            if burned.submission_attempt_count != 1:
                raise
            settlement = _settlement_document(
                state=burned,
                reconciliation=None,
                error=str(exc),
            )
            return {
                "ok": False,
                "phase": "RECOVERY_ONLY",
                "broker_write_performed": "UNKNOWN_AFTER_DURABLE_PRE_IO",
                "broker_post_attempt_burned": True,
                "attempt_id_internal": prepared.attempt.attempt_id,
                "client_order_id": None if receipt is None else receipt.client_order_id,
                "broker_order_id": None if receipt is None else receipt.broker_order_id,
                "broker_post_status": None if receipt is None else receipt.broker_status,
                "settlement": settlement,
                "retry_post": False,
                "credentials_persisted": False,
                "live_trading": "BLOCKED",
            }
        return {
            "ok": settlement["terminal"],
            "phase": "CLOSE_RECONCILED" if settlement["terminal"] else "RECOVERY_ONLY",
            "broker_write_performed": True,
            "broker_post_attempt_burned": True,
            "attempt_id_internal": prepared.attempt.attempt_id,
            "client_order_id": receipt.client_order_id,
            "broker_order_id": receipt.broker_order_id,
            "broker_post_status": receipt.broker_status,
            "settlement": settlement,
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        }

    def _build_fresh_execution_control_plane(
        self,
        *,
        prepared: PreparedPaperCloseOperatorSession,
        credentials: AlpacaPaperCredentials,
    ) -> tuple[PreparedPaperCloseControlPlane, R7RiskReducingOrderManagementSystem, AlpacaPaperCryptoMarketAttestation]:
        """Re-authorize Safety/OMS from fresh broker and market truth immediately before POST.

        Human review is bound to the exact durable plan. Capital authority is not:
        the Safety RiskDecision and OMS order are deliberately created only here,
        after a fresh broker/market check, so operator reading time cannot age the
        capital decision into an unsafe or unusable state.
        """
        if prepared.decision is None or not prepared.decision.valid_at(self._now()):
            raise PaperCloseOperatorBlocked(
                "human close approval expired before final execution; prepare and approve a fresh close"
            )
        fresh_operations = self._operations_reader.snapshot(
            credentials=credentials,
            now=self._now(),
        )
        if not fresh_operations.ready_for_close_preparation or fresh_operations.close_source is None:
            detail = ", ".join(fresh_operations.blockers) if fresh_operations.blockers else "source provenance unavailable"
            raise PaperCloseOperatorBlocked(
                f"fresh PAPER exposure is no longer ready for final close: {detail}"
            )
        if len(fresh_operations.portfolio.positions) != 1 or fresh_operations.portfolio.open_orders:
            raise PaperCloseOperatorBlocked(
                "final close authorization requires exactly one position and zero open orders"
            )
        fresh_position = fresh_operations.portfolio.positions[0]
        if (
            fresh_position.symbol != prepared.plan.symbol
            or fresh_position.asset_class != "crypto"
            or fresh_position.side != "long"
            or fresh_position.quantity != prepared.plan.observed_position_quantity
            or fresh_position.available_quantity != prepared.plan.quantity
        ):
            raise PaperCloseOperatorBlocked(
                "broker position/available quantity changed after review; prepare a new close plan"
            )
        if (
            fresh_operations.portfolio.account.account_id
            != prepared.operations.portfolio.account.account_id
            or fresh_operations.portfolio.account.account_reference
            != prepared.plan.account_reference
            or fresh_operations.portfolio.account.credential_reference
            != credentials.credential_reference
        ):
            raise PaperCloseOperatorBlocked(
                "final PAPER account/credential binding changed after close review"
            )
        original_source = prepared.operations.close_source
        if original_source is None or fresh_operations.close_source.binding_hash != original_source.binding_hash:
            raise PaperCloseOperatorBlocked(
                "certified first-canary close provenance changed before final execution"
            )

        final_market = self._market_reader.attest_snapshot(
            credentials=credentials,
            now=self._now(),
            symbol=prepared.plan.symbol,
        )
        bid = final_market.market.bid
        minimum_marketable_limit = bid * (
            Decimal("1") - prepared.plan.max_slippage_bps / Decimal("10000")
        )
        if prepared.plan.limit_price > bid:
            raise PaperCloseOperatorBlocked(
                "approved SELL LIMIT is no longer marketable against the fresh BTC/USD bid; prepare a new close plan"
            )
        if prepared.plan.limit_price < minimum_marketable_limit:
            raise PaperCloseOperatorBlocked(
                "approved SELL LIMIT is now more aggressive than the fresh 25 bps envelope; prepare a new close plan"
            )

        runtime = SQLiteRuntime(prepared.attempt.database_path)
        ledger = SQLiteEventLedger(runtime)
        safety_store = self._safety_store_factory(self.workspace, self._now_provider)
        safety = CapitalSafetyKernel(
            _close_safety_limits(
                position_quantity=fresh_position.quantity,
                market=final_market.market,
            ),
            ledger,
            state_store=safety_store,
        )
        oms = R7RiskReducingOrderManagementSystem(
            broker=_NoBrokerSurface(),
            ledger=ledger,
            order_store=SQLiteOrderStore(runtime),
            safety_state_store=safety_store,
        )
        control_plane = prepare_paper_close_control_plane(
            attempt_id=prepared.attempt.attempt_id,
            plan=prepared.plan,
            # The durable plan remains bound to the exact broker snapshot the
            # human reviewed. Fresh broker truth above is a stricter execution
            # guard; the writer independently reads broker Portfolio again.
            broker_portfolio=prepared.operations.portfolio,
            market=final_market.market,
            source_entry_order=original_source.source.source_order,
            source_lifecycle=original_source.source.source_lifecycle,
            safety=safety,
            oms=oms,
            now=self._now(),
        )
        return control_plane, oms, final_market

    def recover(
        self,
        *,
        credentials: AlpacaPaperCredentials,
        attempt_id: str | None = None,
    ) -> dict[str, object]:
        self._require_credentials(credentials)
        pending = pending_burned_close_attempts(workspace_path=self.workspace)
        if attempt_id is None:
            if len(pending) != 1:
                raise PaperCloseOperatorBlocked(
                    "close recovery requires exactly one burned nonterminal attempt"
                )
            attempt_id = pending[0]
        elif attempt_id not in pending:
            raise PaperCloseOperatorBlocked("requested close attempt is not the unique pending burned attempt")
        attempt = PaperCloseAttemptWorkspace.open(
            workspace_path=self.workspace,
            attempt_id=attempt_id,
        )
        plan = attempt.read_plan()
        account = read_workspace_paper_account(self.workspace).attestation
        settlement = self._bounded_reconcile(
            attempt=attempt,
            plan=plan,
            credentials=credentials,
            expected_account_id=account.account_id,
        )
        return {
            "ok": True,
            "phase": "CLOSE_RECONCILED" if settlement["terminal"] else "RECOVERY_ONLY",
            "broker_write_performed": False,
            "broker_post_attempt_burned": True,
            "attempt_id_internal": attempt_id,
            "settlement": settlement,
            "retry_post": False,
            "credentials_persisted": False,
            "live_trading": "BLOCKED",
        }

    def _bounded_reconcile(
        self,
        *,
        attempt: PaperCloseAttemptWorkspace,
        plan: PaperCryptoClosePlan,
        credentials: AlpacaPaperCredentials,
        expected_account_id: str,
    ) -> dict[str, object]:
        lifecycle = SQLitePaperCloseLifecycle(SQLiteRuntime(attempt.database_path))
        last_error: str | None = None
        last: PaperCloseReconciliation | None = None
        for delay in AUTO_RECONCILE_DELAYS_SECONDS:
            if delay:
                self._sleep(delay)
            try:
                last = self._reconciliation_factory().reconcile(
                    lifecycle=lifecycle,
                    attempt_id=attempt.attempt_id,
                    plan=plan,
                    credentials=credentials,
                    expected_account_id=expected_account_id,
                    now=self._now(),
                )
            except Exception as exc:
                last_error = str(exc)
                continue
            state = lifecycle.snapshot(attempt.attempt_id).state
            if state.status in {
                PaperCloseLifecycleStatus.FLAT_RECONCILED,
                PaperCloseLifecycleStatus.TERMINAL_RECONCILED,
            }:
                return _settlement_document(state=state, reconciliation=last, error=None)
        state = lifecycle.snapshot(attempt.attempt_id).state
        return _settlement_document(state=state, reconciliation=last, error=last_error)

    def _now(self) -> datetime:
        return _aware(self._now_provider())

    @staticmethod
    def _require_credentials(credentials: AlpacaPaperCredentials) -> None:
        if not isinstance(credentials, AlpacaPaperCredentials):
            raise PaperCloseOperatorBlocked("ephemeral Alpaca PAPER credentials are required")


def _close_safety_limits(*, position_quantity: Decimal, market: MarketSnapshot) -> SafetyLimits:
    if not isinstance(position_quantity, Decimal) or not position_quantity.is_finite() or position_quantity <= 0:
        raise PaperCloseOperatorBlocked("close Safety requires positive broker position quantity")
    current_notional = position_quantity * market.last
    if not current_notional.is_finite() or current_notional <= 0:
        raise PaperCloseOperatorBlocked("close Safety current notional is invalid")
    return SafetyLimits(
        limits_version="r7-first-close-risk-reducing-v1",
        allowed_symbols=frozenset({FIRST_CLOSE_SYMBOL}),
        allowed_order_types=frozenset({OrderType.LIMIT}),
        max_order_notional=current_notional,
        max_position_notional=current_notional,
        max_strategy_gross_exposure=current_notional,
        max_portfolio_gross_exposure=current_notional,
        max_net_exposure=current_notional,
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("0.01"),
        max_drawdown=Decimal("0.0001"),
        max_open_orders=1,
        stale_market_data_ms=5_000,
        price_deviation_bps=Decimal("100"),
        decision_ttl_ms=14_000,
    )


def _ceil_to_increment(value: Decimal, increment: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0 or not increment.is_finite() or increment <= 0:
        raise PaperCloseOperatorBlocked("close price/tick must be positive finite Decimal")
    units = (value / increment).to_integral_value(rounding=ROUND_FLOOR)
    if units * increment < value:
        units += 1
    price = units * increment
    if price <= 0:
        raise PaperCloseOperatorBlocked("tick-rounded close limit is not positive")
    return price


def _receipt_document(receipt: PaperCloseWriteReceipt) -> dict[str, object]:
    return {
        "schema_version": 1,
        "document_type": "R7_PAPER_CLOSE_WRITE_RECEIPT",
        "attempt_id": receipt.attempt_id,
        "plan_hash": receipt.plan_hash,
        "decision_hash": receipt.decision_hash,
        "client_order_id": receipt.client_order_id,
        "request_payload_sha256": receipt.request_payload_sha256,
        "broker_order_id": receipt.broker_order_id,
        "broker_status": receipt.broker_status,
        "request_id": receipt.request_id,
        "response_sha256": receipt.response_sha256,
        "submitted_at": receipt.submitted_at.astimezone(timezone.utc).isoformat(),
        "credentials_persisted": False,
        "retry_post": False,
        "live_trading": "BLOCKED",
    }


def _settlement_document(*, state, reconciliation: PaperCloseReconciliation | None, error: str | None) -> dict[str, object]:
    terminal = state.status in {
        PaperCloseLifecycleStatus.FLAT_RECONCILED,
        PaperCloseLifecycleStatus.TERMINAL_RECONCILED,
    }
    remaining = state.confirmed_remaining_position
    return {
        "terminal": terminal,
        "lifecycle_status": state.status.value,
        "submission_attempt_count": state.submission_attempt_count,
        "broker_order_id": state.broker_order_id,
        "broker_order_status": state.broker_order_status,
        "broker_filled_quantity": str(state.broker_filled_quantity),
        "remaining_position": str(remaining),
        "flat": terminal and remaining == 0,
        "residual_exposure_requires_new_certification": terminal and remaining > 0,
        "reconciliation_fingerprint": None if reconciliation is None else reconciliation.fingerprint,
        "last_error": error,
        "next_action": (
            "DONE_FLAT"
            if terminal and remaining == 0
            else "STOP_AND_CERTIFY_RESIDUAL_EXPOSURE"
            if terminal
            else "RECONCILE_GET_ONLY_NEVER_RETRY_POST"
        ),
        "retry_post": False,
        "live_trading": "BLOCKED",
    }


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("time must be timezone-aware")
    return value.astimezone(timezone.utc)


__all__ = [
    "AUTO_RECONCILE_DELAYS_SECONDS",
    "CLOSE_WRITE_ENV",
    "FIRST_CLOSE_SLIPPAGE_BPS",
    "FIRST_CLOSE_SYMBOL",
    "PaperCloseOperator",
    "PaperCloseOperatorBlocked",
    "PaperCloseOperatorError",
    "PreparedPaperCloseOperatorSession",
    "ReadOnlyCanonicalSafetyStateStore",
]
