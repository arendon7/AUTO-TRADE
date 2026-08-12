from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from hashlib import sha256
import json
from pathlib import Path

from autotrade.connectivity_canary_authority import CONNECTIVITY_CANARY_STRATEGY_ID, SQLiteConnectivityCanaryAuthorityStore
from autotrade.connectivity_preparation_binding import ConnectivityPreparationBinding, SQLiteConnectivityPreparationBindingStore
from autotrade.domain import RiskDecision, RiskDecisionStatus, OrderStatus, intent_fingerprint, market_fingerprint, risk_decision_fingerprint
from autotrade.instrument_master import SQLiteInstrumentMaster
from autotrade.oms import OrderManagementSystem
from autotrade.persistence import SQLiteEventLedger, SQLiteOrderStore, SQLitePortfolioStore, SQLiteRuntime, SQLiteSafetyStateStore, _portfolio_for_storage

from .alpaca_paper_asset_evidence import PaperAssetEvidenceStore
from .alpaca_paper_bracket import PaperEquityVenueRules
from .alpaca_paper_canary_coordinator import PaperCanaryCoordinator, PreparedPaperCanaryPackage
from .alpaca_paper_canary_permit import SQLitePaperCanaryPermitRegistry
from .alpaca_paper_connectivity_candidate import CANDIDATE_ARTIFACT
from .alpaca_paper_connectivity_gate import CERTIFIED_TRACKS, ConnectivityCanaryGate
from .alpaca_paper_flat_account_evidence import PaperFlatAccountEvidenceStore
from .alpaca_paper_gateway import AlpacaPaperAccountAttestation
from .alpaca_paper_market_evidence import PaperMarketEvidenceStore
from .alpaca_paper_operational import PaperOperationalWorkspace, _file_sha256, _read_json_object, _write_json_idempotent, account_attestation_payload
from .alpaca_paper_submission import SQLitePaperSubmissionRegistry

CONNECTIVITY_PREP_ARTIFACT = "connectivity_preparation.json"
MAX_ACCOUNT_AGE_SECONDS = 30
MAX_ASSET_AGE_SECONDS = 300
MAX_FLAT_AGE_SECONDS = 30
MAX_MARKET_AGE_SECONDS = 5
INSTRUMENT_MAX_AGE_SECONDS = 300


class PaperConnectivityPreparationError(RuntimeError):
    pass


class PaperConnectivityPreparationRejected(PaperConnectivityPreparationError):
    pass


class _NoBrokerSurface:
    def submit(self, **_kwargs):
        raise PaperConnectivityPreparationRejected("connectivity preparation has no broker submission surface")


@dataclass(frozen=True, slots=True)
class PreparedConnectivityCanary:
    order_id: str
    attempt_id: str
    connectivity_authority_id: str
    connectivity_binding_id: str
    standard_package_hash: str
    bracket_payload_hash: str
    preparation_hash: str
    core_db_sha256_after_preparation: str
    artifact_path: Path


class PaperConnectivityPreparationBridge:
    """Credential-free, network-free preparation for one CONNECTIVITY_CANARY."""

    def __init__(self, workspace: PaperOperationalWorkspace) -> None:
        if not isinstance(workspace, PaperOperationalWorkspace):
            raise TypeError("PaperOperationalWorkspace is required")
        self._workspace = workspace

    def prepare(self, *, now: datetime) -> PreparedConnectivityCanary:
        _require_aware(now)
        instant = now.astimezone(timezone.utc)
        candidate_path = self._workspace.root / CANDIDATE_ARTIFACT
        if not candidate_path.is_file():
            raise PaperConnectivityPreparationRejected("connectivity_candidate.json is required before connectivity preparation")
        if (self._workspace.root / CONNECTIVITY_PREP_ARTIFACT).exists():
            raise PaperConnectivityPreparationRejected("connectivity preparation artifact already exists")
        self._require_normal_operator_artifacts_absent()
        if not self._workspace.core_db_path.is_file():
            raise PaperConnectivityPreparationRejected("connectivity candidate core.sqlite3 is missing")

        candidate = _read_json_object(candidate_path)
        _validate_candidate_envelope(candidate)
        candidate_hash = _string(candidate, "candidate_hash")
        if candidate_hash != _hash({k: v for k, v in candidate.items() if k != "candidate_hash"}):
            raise PaperConnectivityPreparationRejected("connectivity candidate hash mismatch")
        expected_core_hash = _string(candidate, "core_db_sha256")
        if _file_sha256(self._workspace.core_db_path) != expected_core_hash:
            raise PaperConnectivityPreparationRejected("core.sqlite3 changed after connectivity candidate construction")

        runtime = SQLiteRuntime(self._workspace.core_db_path)
        ledger = SQLiteEventLedger(runtime)
        if not ledger.verify_integrity():
            raise PaperConnectivityPreparationRejected("core Event Ledger integrity failed")
        order_id = _string(candidate, "order_id")
        order = SQLiteOrderStore(runtime).get_by_order_id(order_id)
        if order is None or order.status is not OrderStatus.VALIDATED:
            raise PaperConnectivityPreparationRejected("connectivity preparation requires the exact OMS VALIDATED candidate order")
        if order.intent.strategy_id != CONNECTIVITY_CANARY_STRATEGY_ID:
            raise PaperConnectivityPreparationRejected("candidate strategy_id is not CONNECTIVITY_CANARY")
        if intent_fingerprint(order.intent) != _string(candidate, "intent_fingerprint"):
            raise PaperConnectivityPreparationRejected("candidate intent fingerprint mismatch")

        authority = SQLiteConnectivityCanaryAuthorityStore(runtime).get_for_order(order_id)
        if authority is None:
            raise PaperConnectivityPreparationRejected("durable CONNECTIVITY_CANARY authority is missing")
        candidate_authority = _object(candidate, "authority")
        if authority.authority_id != candidate_authority.get("authority_id") or authority.authority_hash != candidate_authority.get("authority_hash"):
            raise PaperConnectivityPreparationRejected("candidate/authority binding mismatch")
        if not authority.is_valid_at(instant):
            raise PaperConnectivityPreparationRejected("CONNECTIVITY_CANARY authority expired before preparation")

        decision = _risk_decision(candidate)
        if decision.status is not RiskDecisionStatus.APPROVED:
            raise PaperConnectivityPreparationRejected("connectivity RiskDecision is not APPROVED")
        if decision.decision_id != order.risk_decision_id or decision.decision_id != authority.risk_decision_id:
            raise PaperConnectivityPreparationRejected("RiskDecision identity mismatch")
        if risk_decision_fingerprint(decision) != authority.risk_decision_fingerprint:
            raise PaperConnectivityPreparationRejected("RiskDecision fingerprint mismatch")
        if decision.safety_state_version != authority.safety_state_version or instant >= decision.valid_until:
            raise PaperConnectivityPreparationRejected("RiskDecision is stale or Safety binding changed")

        safety_state = SQLiteSafetyStateStore(runtime).get()
        if safety_state.kill_switch_active or safety_state.version != authority.safety_state_version:
            raise PaperConnectivityPreparationRejected("durable Safety state blocks or drifted after candidate")
        versioned_portfolio = SQLitePortfolioStore(runtime).get()
        if versioned_portfolio.version != authority.portfolio_version:
            raise PaperConnectivityPreparationRejected("Portfolio version drifted after candidate")
        snapshot = versioned_portfolio.snapshot
        _, snapshot_hash = _portfolio_for_storage(snapshot)
        if snapshot.snapshot_id != authority.portfolio_snapshot_id or snapshot_hash != authority.portfolio_snapshot_hash:
            raise PaperConnectivityPreparationRejected("Portfolio baseline binding mismatch")
        if snapshot.gross_exposure != 0 or snapshot.net_exposure != 0 or snapshot.open_orders != 0 or not snapshot.reconciliation_ok or not snapshot.broker_state_known:
            raise PaperConnectivityPreparationRejected("connectivity Portfolio baseline is no longer clean")

        account = _read_account(self._workspace)
        asset = PaperAssetEvidenceStore(self._workspace).read()
        flat = PaperFlatAccountEvidenceStore(self._workspace).read()
        market_attestation = PaperMarketEvidenceStore(self._workspace).read()
        market = market_attestation.market
        _require_fresh(account.attested_at, instant, MAX_ACCOUNT_AGE_SECONDS, "account")
        _require_fresh(asset.observed_at, instant, MAX_ASSET_AGE_SECONDS, "asset")
        _require_fresh(flat.attested_at, instant, MAX_FLAT_AGE_SECONDS, "flat-account")
        _require_fresh(market.observed_at, instant, MAX_MARKET_AGE_SECONDS, "market")
        if account.fingerprint != authority.account_attestation_fingerprint:
            raise PaperConnectivityPreparationRejected("account evidence changed after candidate")
        if asset.fingerprint != authority.asset_attestation_fingerprint:
            raise PaperConnectivityPreparationRejected("asset evidence changed after candidate")
        if flat.fingerprint != authority.baseline_flat_account_fingerprint or not flat.clean_for_first_canary:
            raise PaperConnectivityPreparationRejected("flat-account evidence changed or is no longer clean")
        if market_attestation.fingerprint != authority.market_evidence_fingerprint or market_fingerprint(market) != authority.market_fingerprint:
            raise PaperConnectivityPreparationRejected("market evidence changed after candidate")
        if market.symbol != order.intent.symbol or asset.symbol != order.intent.symbol:
            raise PaperConnectivityPreparationRejected("candidate symbol/evidence mismatch")

        rules = SQLiteInstrumentMaster(runtime).require_tradable(
            venue="ALPACA_PAPER",
            symbol=order.intent.symbol,
            now=instant,
            max_age=timedelta(seconds=INSTRUMENT_MAX_AGE_SECONDS),
        )
        if rules.fingerprint != authority.instrument_rules_fingerprint or rules.price_tick != asset.price_increment:
            raise PaperConnectivityPreparationRejected("Instrument Master binding mismatch")
        venue_rules = PaperEquityVenueRules(
            symbol=order.intent.symbol,
            asset_class="us_equity",
            price_tick=rules.price_tick,
            quantity_step=Decimal("1"),
            minimum_quantity=Decimal("1"),
            instrument_master_fingerprint=rules.fingerprint,
        )
        take_profit, stop_loss = _protection_prices(entry=order.intent.limit_price, tick=rules.price_tick)

        oms = OrderManagementSystem(
            broker=_NoBrokerSurface(),
            ledger=ledger,
            order_store=SQLiteOrderStore(runtime),
            safety_state_store=SQLiteSafetyStateStore(runtime),
        )
        coordinator = PaperCanaryCoordinator(
            oms=oms,
            canary_gate=ConnectivityCanaryGate(authority),  # type: ignore[arg-type]
        )
        submission_registry = SQLitePaperSubmissionRegistry(SQLiteRuntime(self._workspace.submission_db_path))
        permit_registry = SQLitePaperCanaryPermitRegistry(SQLiteRuntime(self._workspace.permit_db_path))
        result = coordinator.prepare(
            intent=order.intent,
            decision=decision,
            market=market,
            account_attestation=account,
            venue_rules=venue_rules,
            take_profit_price=take_profit,
            stop_loss_price=stop_loss,
            submission_registry=submission_registry,
            permit_registry=permit_registry,
            now=instant,
            certified_tracks=tuple(sorted(CERTIFIED_TRACKS)),
            reconciliation_clean=True,
            unresolved_unknown_orders=0,
            kill_switch_engaged=False,
            health_allows_new_exposure=False,
            prior_canary_submissions=0,
        )
        if result.order.status is not OrderStatus.VALIDATED:
            raise PaperConnectivityPreparationRejected("connectivity preparation changed OMS order state")

        binding = ConnectivityPreparationBinding.create(
            order_id=result.package.order_id,
            connectivity_authority_id=authority.authority_id,
            connectivity_authority_hash=authority.authority_hash,
            candidate_hash=candidate_hash,
            standard_package_hash=result.package.package_hash,
            canary_approval_hash=result.approval.approval_hash,
            permit_event_hash=result.permit.event_hash,
            submission_binding_hash=result.binding.fingerprint,
            bracket_payload_hash=result.bracket.payload_hash,
            instrument_master_fingerprint=result.bracket.instrument_master_fingerprint,
            prepared_at=instant,
        )
        SQLiteConnectivityPreparationBindingStore(runtime).record(binding)
        if not ledger.verify_integrity():
            raise PaperConnectivityPreparationRejected("core Event Ledger failed after connectivity binding")
        _checkpoint(runtime)
        core_hash_after = _file_sha256(self._workspace.core_db_path)
        self._require_normal_operator_artifacts_absent()
        wrapper = _wrapper_payload(
            standard_package=result.package,
            bracket=result.bracket.canonical_payload,
            bracket_payload_hash=result.bracket.payload_hash,
            binding=binding,
            candidate_hash=candidate_hash,
            core_db_sha256_before=expected_core_hash,
            core_db_sha256_after=core_hash_after,
        )
        preparation_hash = _hash(wrapper)
        wrapper["preparation_hash"] = preparation_hash
        artifact = self._workspace.root / CONNECTIVITY_PREP_ARTIFACT
        _write_json_idempotent(artifact, wrapper)
        self._require_normal_operator_artifacts_absent()
        return PreparedConnectivityCanary(
            order_id=result.package.order_id,
            attempt_id=result.package.attempt_id,
            connectivity_authority_id=authority.authority_id,
            connectivity_binding_id=binding.binding_id,
            standard_package_hash=result.package.package_hash,
            bracket_payload_hash=result.bracket.payload_hash,
            preparation_hash=preparation_hash,
            core_db_sha256_after_preparation=core_hash_after,
            artifact_path=artifact,
        )

    def _require_normal_operator_artifacts_absent(self) -> None:
        forbidden = (self._workspace.prepared_package_path, self._workspace.expected_bracket_path, self._workspace.operator_context_path, self._workspace.manifest_path)
        existing = [path.name for path in forbidden if path.exists()]
        if existing:
            raise PaperConnectivityPreparationRejected(f"normal operator artifacts are forbidden on connectivity preparation path: {existing}")


def _validate_candidate_envelope(candidate: dict[str, object]) -> None:
    for key, expected in (
        ("schema_version", 1), ("environment", "PAPER"), ("purpose", "CONNECTIVITY_CANARY"),
        ("strategy_id", CONNECTIVITY_CANARY_STRATEGY_ID), ("order_status", "VALIDATED"),
        ("builder_network_used", False), ("credentials_used", False), ("strategy_health_required", False),
        ("strategy_health_created", False), ("strategy_trading_authorized", False), ("external_post_authorized", False),
        ("capital_authority", "NONE"), ("profitability_claim", False), ("live_trading", "BLOCKED"),
        ("next_action", "CONNECTIVITY_PREPARATION_BRIDGE_REQUIRED"),
    ):
        if candidate.get(key) != expected:
            raise PaperConnectivityPreparationRejected(f"unsafe connectivity candidate field: {key}")


def _risk_decision(candidate: dict[str, object]) -> RiskDecision:
    raw = _object(candidate, "risk_decision")
    try:
        decision = RiskDecision(
            decision_id=_string(raw, "decision_id"), intent_id=_string(raw, "intent_id"),
            status=RiskDecisionStatus(_string(raw, "status")), reason_code=_string(raw, "reason_code"),
            reason_detail=_string(raw, "reason_detail"), evaluated_at=_timestamp(raw, "evaluated_at"),
            valid_until=_timestamp(raw, "valid_until"), limits_version=_string(raw, "limits_version"),
            intent_fingerprint=_string(raw, "intent_fingerprint"), market_fingerprint=_string(raw, "market_fingerprint"),
            approved_notional=_nullable_decimal(raw.get("approved_notional")), risk_reducing=_boolean(raw, "risk_reducing"),
            safety_state_version=_integer(raw, "safety_state_version"),
        )
    except (ValueError, InvalidOperation) as exc:
        raise PaperConnectivityPreparationRejected("candidate RiskDecision is invalid") from exc
    if risk_decision_fingerprint(decision) != candidate.get("risk_decision_fingerprint"):
        raise PaperConnectivityPreparationRejected("candidate RiskDecision fingerprint is invalid")
    return decision


def _read_account(workspace: PaperOperationalWorkspace) -> AlpacaPaperAccountAttestation:
    raw = _read_json_object(workspace.account_attestation_path)
    try:
        account = AlpacaPaperAccountAttestation(
            account_id=_string(raw, "account_id"), account_reference=_string(raw, "account_reference"),
            credential_reference=_string(raw, "credential_reference"), status=_string(raw, "status"), currency=_string(raw, "currency"),
            buying_power=_decimal(raw, "buying_power"), portfolio_value=_decimal(raw, "portfolio_value"),
            shorting_enabled=_boolean(raw, "shorting_enabled"), attested_at=_timestamp(raw, "attested_at"),
            request_id=_string(raw, "request_id"), source_host=_string(raw, "source_host"), source_path=_string(raw, "source_path"),
        )
    except (ValueError, InvalidOperation) as exc:
        raise PaperConnectivityPreparationRejected("account evidence is invalid") from exc
    if account_attestation_payload(account) != raw:
        raise PaperConnectivityPreparationRejected("account evidence is non-canonical")
    return account


def _protection_prices(*, entry: Decimal | None, tick: Decimal) -> tuple[Decimal, Decimal]:
    if entry is None or not entry.is_finite() or entry <= 0 or not tick.is_finite() or tick <= 0:
        raise PaperConnectivityPreparationRejected("connectivity price geometry input is invalid")
    tp_target = max(entry * Decimal("1.02"), entry + tick * Decimal("2"))
    sl_target = min(entry * Decimal("0.99"), entry - tick * Decimal("2"))
    tp = (tp_target / tick).to_integral_value(rounding=ROUND_CEILING) * tick
    sl = (sl_target / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if not tp > entry > sl > 0:
        raise PaperConnectivityPreparationRejected("connectivity bracket geometry is invalid")
    return tp, sl


def _wrapper_payload(*, standard_package: PreparedPaperCanaryPackage, bracket: object, bracket_payload_hash: str, binding: ConnectivityPreparationBinding, candidate_hash: str, core_db_sha256_before: str, core_db_sha256_after: str) -> dict[str, object]:
    return {
        "schema_version": 1, "environment": "PAPER", "purpose": "CONNECTIVITY_CANARY",
        "candidate_hash": candidate_hash, "standard_prepared_package": standard_package.canonical_payload(),
        "expected_bracket": bracket, "expected_bracket_payload_hash": bracket_payload_hash,
        "connectivity_preparation_binding": binding.payload(),
        "core_db_sha256_before_preparation": core_db_sha256_before,
        "core_db_sha256_after_preparation": core_db_sha256_after,
        "normal_prepared_package_created": False, "normal_expected_bracket_artifact_created": False,
        "operator_context_created": False, "normal_manifest_created": False,
        "strategy_health_required": False, "strategy_health_created": False, "strategy_trading_authorized": False,
        "operator_authority_created": False, "external_post_authorized": False, "external_order_submitted": False,
        "capital_authority": "NONE", "profitability_claim": False, "live_trading": "BLOCKED",
        "next_action": "CONNECTIVITY_OPERATOR_BRIDGE_REQUIRED",
    }


def _checkpoint(runtime: SQLiteRuntime) -> None:
    conn = runtime.connect()
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is not None and int(row[0]) != 0:
            raise PaperConnectivityPreparationRejected("cannot checkpoint connectivity core database")
    finally:
        conn.close()


def _require_fresh(value: datetime, now: datetime, max_age_seconds: int, label: str) -> None:
    _require_aware(value)
    age = (now - value.astimezone(timezone.utc)).total_seconds()
    if age < 0:
        raise PaperConnectivityPreparationRejected(f"{label} evidence is from the future")
    if age > max_age_seconds:
        raise PaperConnectivityPreparationRejected(f"{label} evidence is stale; repeat the GET-only connectivity preflight sequence")


def _object(raw: dict[str, object], key: str) -> dict[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise PaperConnectivityPreparationRejected(f"{key} must be object")
    return value


def _string(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise PaperConnectivityPreparationRejected(f"{key} must be non-empty string")
    return value


def _boolean(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise PaperConnectivityPreparationRejected(f"{key} must be boolean")
    return value


def _integer(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PaperConnectivityPreparationRejected(f"{key} must be integer")
    return value


def _decimal(raw: dict[str, object], key: str) -> Decimal:
    value = raw.get(key)
    if not isinstance(value, str):
        raise PaperConnectivityPreparationRejected(f"{key} must be decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise PaperConnectivityPreparationRejected(f"{key} must be finite")
    return parsed


def _nullable_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PaperConnectivityPreparationRejected("nullable decimal must be string or null")
    parsed = Decimal(value)
    if not parsed.is_finite():
        raise PaperConnectivityPreparationRejected("nullable decimal must be finite")
    return parsed


def _timestamp(raw: dict[str, object], key: str) -> datetime:
    parsed = datetime.fromisoformat(_string(raw, key))
    _require_aware(parsed)
    return parsed


def _require_aware(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def _hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


__all__ = ["CONNECTIVITY_PREP_ARTIFACT", "PaperConnectivityPreparationBridge", "PaperConnectivityPreparationError", "PaperConnectivityPreparationRejected", "PreparedConnectivityCanary"]
