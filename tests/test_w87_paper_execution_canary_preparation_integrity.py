from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

import autotrade.paper_execution_canary_preparation as prep_module
import autotrade.paper_execution_canary_preparation_guard as guard_module
from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.domain import MarketSnapshot, OrderStatus
from autotrade.paper_execution_canary_preparation import (
    PaperExecutionCanaryPreparationIntegrityError,
    PaperExecutionCanaryPreparationResult,
)
from autotrade.paper_execution_canary_preparation_guard import (
    PaperExecutionCanaryPreparationGuardBlocked,
)
from autotrade.product_profile import ProductCapabilities
from autotrade.state import VersionedPortfolioSnapshot
from test_w87_paper_execution_canary_preparation import _prepare, _stack


def _tamper_call(obj, field, value, call, error=PaperExecutionCanaryPreparationIntegrityError):
    original = getattr(obj, field)
    object.__setattr__(obj, field, value)
    try:
        with pytest.raises(error):
            call()
    finally:
        object.__setattr__(obj, field, original)


def test_w87_c_receipt_rejects_structural_corruption(monkeypatch, tmp_path):
    _, result = _prepare(monkeypatch, tmp_path)
    receipt = result.receipt

    corruptions = (
        ("bridge_id", ""),
        ("contract_version", "W87_WRONG_VERSION"),
        ("admission_hash", "g" * 64),
        ("account_id", ""),
        ("symbol", "test/usd"),
        ("quantity", Decimal("0")),
        ("unresolved_local_unknown_orders", True),
        ("status", "BLOCKED"),
        ("oms_order_status", OrderStatus.SUBMITTING.value),
        ("lifecycle_status", CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN.value),
        ("lifecycle_entry_attempt_count", 1),
    )
    for field, value in corruptions:
        with pytest.raises(PaperExecutionCanaryPreparationIntegrityError):
            replace(receipt, **{field: value})

    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="notional"):
        replace(receipt, notional_usd=receipt.notional_usd + Decimal("0.001"))
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="positive execution window"):
        replace(receipt, prepared_at=receipt.package_execution_deadline)
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="timezone-aware"):
        replace(receipt, prepared_at=datetime(2026, 8, 27, 9, 0, 0))


def test_w87_c_bridge_rejects_wrong_boundary_object_types(monkeypatch, tmp_path):
    (
        sealed,
        admission,
        risk_result,
        _,
        _,
        _,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)
    base = {
        "bridge_id": "w87-integrity-types",
        "admission": admission,
        "sealed_result": sealed,
        "risk_result": risk_result,
        "coordinator": coordinator,
        "runtime": runtime,
    }
    for field in ("admission", "sealed_result", "risk_result", "coordinator", "runtime"):
        kwargs = dict(base)
        kwargs[field] = object()
        with pytest.raises(TypeError):
            prep_module.prepare_paper_execution_canary(**kwargs)


def test_w87_c_exact_binding_checks_fail_closed_independently(monkeypatch, tmp_path):
    sealed, admission, risk_result, *_ = _stack(monkeypatch, tmp_path)

    def validate():
        prep_module._validate_exact_bindings(
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
        )

    _tamper_call(admission, "readiness_seal_hash", "0" * 64, validate)
    _tamper_call(risk_result.receipt, "admission_hash", "1" * 64, validate)
    _tamper_call(risk_result.receipt, "readiness_seal_hash", "2" * 64, validate)
    _tamper_call(risk_result.receipt, "pipeline_receipt_hash", "3" * 64, validate)
    _tamper_call(admission, "account_id", "different-account", validate)
    _tamper_call(risk_result.receipt, "intent_fingerprint", "4" * 64, validate)
    _tamper_call(risk_result.receipt, "market_snapshot_fingerprint", "5" * 64, validate)
    _tamper_call(
        admission,
        "canary_quantity",
        admission.canary_quantity + Decimal("0.001"),
        validate,
    )


def test_w87_c_reconstructed_asset_and_market_hashes_are_authoritative(monkeypatch, tmp_path):
    sealed, *_ = _stack(monkeypatch, tmp_path)
    asset_proof = sealed.pipeline.asset_truth
    market_proof = sealed.pipeline.market_truth

    _tamper_call(
        asset_proof,
        "asset_attestation_fingerprint",
        "6" * 64,
        lambda: prep_module._rebuild_asset_attestation(sealed),
    )
    _tamper_call(
        asset_proof,
        "asset_contract_fingerprint",
        "7" * 64,
        lambda: prep_module._rebuild_asset_attestation(sealed),
    )
    _tamper_call(
        market_proof,
        "market_snapshot_fingerprint",
        "8" * 64,
        lambda: prep_module._rebuild_market_attestation(sealed),
    )
    _tamper_call(
        market_proof,
        "market_attestation_fingerprint",
        "9" * 64,
        lambda: prep_module._rebuild_market_attestation(sealed),
    )


def test_w87_c_cross_evidence_checks_reject_each_identity_drift(monkeypatch, tmp_path):
    sealed, admission, risk_result, *_ = _stack(monkeypatch, tmp_path)
    asset = prep_module._rebuild_asset_attestation(sealed)
    market = prep_module._rebuild_market_attestation(sealed)
    profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=asset.fingerprint,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )

    def validate(*, effective_market=market, effective_profile=profile):
        prep_module._validate_reconstructed_evidence(
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            asset=asset,
            market_attestation=effective_market,
            product_profile=effective_profile,
        )

    _tamper_call(
        sealed.pipeline.receipt,
        "account_attestation_fingerprint",
        "a" * 64,
        validate,
    )
    _tamper_call(
        sealed.pipeline.asset_truth,
        "account_attestation_fingerprint",
        "b" * 64,
        validate,
    )
    _tamper_call(
        sealed.pipeline.account_attestation,
        "account_id",
        "different-account",
        validate,
    )
    _tamper_call(
        sealed.pipeline.market_truth,
        "asset_attestation_fingerprint",
        "c" * 64,
        validate,
    )

    changed_market = replace(
        market.market,
        last=market.market.last + Decimal("0.01"),
    )
    changed_attestation = replace(market, market=changed_market)
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="Safety market"):
        validate(effective_market=changed_attestation)

    wrong_profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint="d" * 64,
        observed_at=asset.observed_at,
        fractionable=asset.fractionable,
        marginable=asset.marginable,
        shortable=asset.shortable,
    )
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="ProductCapabilities"):
        validate(effective_profile=wrong_profile)


def test_w87_c_prepared_result_requires_exact_oms_and_lifecycle_stop(monkeypatch, tmp_path):
    stack, result = _prepare(monkeypatch, tmp_path)
    sealed, admission, risk_result, *_ = stack
    prepared = result.coordinator_result
    profile = ProductCapabilities.crypto_alpaca_paper(
        source_fingerprint=sealed.pipeline.asset_truth.asset_attestation_fingerprint,
        observed_at=sealed.pipeline.asset_truth.asset_observed_at,
        fractionable=sealed.pipeline.asset_truth.fractionable,
        marginable=sealed.pipeline.asset_truth.marginable,
        shortable=sealed.pipeline.asset_truth.shortable,
    )

    def validate(candidate):
        prep_module._validate_prepared_result(
            prepared=candidate,
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            product_profile=profile,
        )

    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="OMS VALIDATED"):
        validate(replace(prepared, order=replace(prepared.order, status=OrderStatus.SUBMITTING)))
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="ENTRY_PREPARED"):
        validate(
            replace(
                prepared,
                lifecycle_state=replace(
                    prepared.lifecycle_state,
                    status=CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN,
                ),
            )
        )
    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="consumed an entry attempt"):
        validate(
            replace(
                prepared,
                lifecycle_state=replace(prepared.lifecycle_state, entry_attempt_count=1),
            )
        )


def test_w87_c_result_rejects_package_receipt_divergence(monkeypatch, tmp_path):
    _, first = _prepare(monkeypatch, tmp_path, bridge_id="w87-result-one")
    _, second = _prepare(monkeypatch, tmp_path / "second", bridge_id="w87-result-two")

    with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="coordinator result differs"):
        PaperExecutionCanaryPreparationResult(
            receipt=first.receipt,
            package=first.package,
            coordinator_result=second.coordinator_result,
        )


def test_w87_c_local_unknown_reader_rejects_corrupt_durable_state(monkeypatch, tmp_path):
    stack, _ = _prepare(monkeypatch, tmp_path)
    runtime = stack[-1]
    conn = runtime.connect()
    try:
        row = conn.execute(
            "SELECT lifecycle_id, state_json FROM alpaca_crypto_lifecycle_control LIMIT 1"
        ).fetchone()
        lifecycle_id = str(row["lifecycle_id"])
        original = str(row["state_json"])
    finally:
        conn.close()

    def write(raw):
        conn = runtime.connect()
        try:
            conn.execute(
                "UPDATE alpaca_crypto_lifecycle_control SET state_json = ? WHERE lifecycle_id = ?",
                (raw, lifecycle_id),
            )
        finally:
            conn.close()

    try:
        write("{")
        with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="canonical JSON"):
            prep_module._count_unresolved_local_unknown(runtime)

        write('{"status": 7}')
        with pytest.raises(PaperExecutionCanaryPreparationIntegrityError, match="canonical status"):
            prep_module._count_unresolved_local_unknown(runtime)

        write('{"status":"ENTRY_SUBMISSION_UNKNOWN"}')
        assert prep_module._count_unresolved_local_unknown(runtime) == 1
    finally:
        write(original)


def test_w87_c_guard_rejects_invalid_readers_and_nonflat_integrity(monkeypatch, tmp_path):
    (
        sealed,
        admission,
        risk_result,
        safety,
        portfolio_store,
        _,
        coordinator,
        runtime,
    ) = _stack(monkeypatch, tmp_path)

    with pytest.raises(TypeError, match="CapitalSafetyKernel"):
        guard_module.prepare_guarded_paper_execution_canary(
            bridge_id="w87-bad-safety",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=object(),
            portfolio_store=portfolio_store,
            coordinator=coordinator,
            runtime=runtime,
        )
    with pytest.raises(TypeError, match="portfolio_store"):
        guard_module.prepare_guarded_paper_execution_canary(
            bridge_id="w87-bad-portfolio-reader",
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
            safety=safety,
            portfolio_store=object(),
            coordinator=coordinator,
            runtime=runtime,
        )

    safety_state = safety.state_store.get()
    with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="non-versioned"):
        guard_module._validate_guard_state(
            safety_state=safety_state,
            portfolio=object(),
            risk_result=risk_result,
        )

    original = portfolio_store.get()
    corrupt = VersionedPortfolioSnapshot(
        version=original.version,
        snapshot=replace(original.snapshot, gross_exposure=Decimal("1")),
    )
    with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="integrity failed"):
        guard_module._validate_guard_state(
            safety_state=safety_state,
            portfolio=corrupt,
            risk_result=risk_result,
        )

    nonflat = VersionedPortfolioSnapshot(
        version=original.version,
        snapshot=replace(original.snapshot, reconciliation_ok=False),
    )
    with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="flat reconciled"):
        guard_module._validate_guard_state(
            safety_state=safety_state,
            portfolio=nonflat,
            risk_result=risk_result,
        )


def test_w87_c_r6_capacity_guard_rejects_notional_mismatch_and_zero_capacity(monkeypatch, tmp_path):
    sealed, admission, risk_result, *_ = _stack(monkeypatch, tmp_path)

    _tamper_call(
        admission,
        "canary_notional_usd",
        admission.canary_notional_usd + Decimal("0.01"),
        lambda: guard_module._require_r6_first_canary_capacity(
            admission=admission,
            sealed_result=sealed,
            risk_result=risk_result,
        ),
        error=PaperExecutionCanaryPreparationGuardBlocked,
    )

    account = sealed.pipeline.account_attestation
    original_buying_power = account.buying_power
    original_portfolio_value = account.portfolio_value
    object.__setattr__(account, "buying_power", Decimal("0"))
    object.__setattr__(account, "portfolio_value", Decimal("0"))
    try:
        with pytest.raises(PaperExecutionCanaryPreparationGuardBlocked, match="conservative cap"):
            guard_module._require_r6_first_canary_capacity(
                admission=admission,
                sealed_result=sealed,
                risk_result=risk_result,
            )
    finally:
        object.__setattr__(account, "buying_power", original_buying_power)
        object.__setattr__(account, "portfolio_value", original_portfolio_value)
