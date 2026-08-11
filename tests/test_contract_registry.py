from __future__ import annotations

from copy import deepcopy
from datetime import timezone
import json

import pytest

from autotrade.contract_registry import (
    ContractCompatibilityError,
    ContractRegistry,
    ContractRegistryError,
    ContractValidationError,
)


def valid_intent(now):
    return {
        "intent_id": "intent-1",
        "idempotency_key": "idem-1",
        "strategy_id": "strategy-1",
        "symbol": "TEST-USD",
        "side": "BUY",
        "quantity": "10",
        "order_type": "MARKET",
        "created_at": now.isoformat(),
        "limit_price": None,
    }


def valid_fill(now):
    return {
        "fill_id": "fill-1",
        "order_id": "order-1",
        "symbol": "TEST-USD",
        "side": "BUY",
        "quantity": "4",
        "price": "101.25",
        "occurred_at": now.isoformat(),
    }


def test_default_registry_loads_with_stable_ids_and_fingerprint():
    first = ContractRegistry.load_default()
    second = ContractRegistry.load_default()
    ids = [spec.contract_id for spec in first.all_contracts()]
    assert ids == sorted(ids)
    assert len(ids) == 10
    assert len(set(ids)) == 10
    assert "AuthoritativeInstrumentRules@1" in ids
    assert first.registry_version == 2
    assert first.registry_fingerprint() == second.registry_fingerprint()
    assert len(first.registry_fingerprint()) == 64
    assert all(len(spec.fingerprint) == 64 for spec in first.all_contracts())


def test_nested_broker_execution_and_order_record_validate(now):
    registry = ContractRegistry.load_default()
    registry.validate(
        "BrokerExecution@1",
        {"status": "PARTIALLY_FILLED", "fills": [valid_fill(now)]},
    )
    registry.validate(
        "OrderRecord@1",
        {
            "order_id": "order-1",
            "intent": valid_intent(now),
            "risk_decision_id": "risk-1",
            "status": "SUBMITTED",
            "created_at": now.isoformat(),
            "submitted_at": now.isoformat(),
            "filled_quantity": "0",
            "average_fill_price": None,
        },
    )
    registry.validate(
        "SafetyControlState@1",
        {
            "kill_switch_active": False,
            "kill_switch_reason": "",
            "circuit_active": False,
            "circuit_reason": "",
            "version": 1,
            "updated_at": None,
        },
    )


def test_nested_contract_rejects_bad_fill_and_bad_intent(now):
    registry = ContractRegistry.load_default()
    bad_fill = valid_fill(now)
    bad_fill["quantity"] = "NaN"
    with pytest.raises(ContractValidationError, match="finite"):
        registry.validate(
            "BrokerExecution@1",
            {"status": "PARTIALLY_FILLED", "fills": [bad_fill]},
        )

    bad_intent = valid_intent(now)
    bad_intent["side"] = "HACK"
    with pytest.raises(ContractValidationError, match="one of"):
        registry.validate(
            "OrderRecord@1",
            {
                "order_id": "order-1",
                "intent": bad_intent,
                "risk_decision_id": "risk-1",
                "status": "SUBMITTED",
                "created_at": now.isoformat(),
                "submitted_at": None,
                "filled_quantity": "0",
                "average_fill_price": None,
            },
        )


def test_registry_rejects_unknown_missing_and_wrong_field_types(now):
    registry = ContractRegistry.load_default()
    payload = valid_intent(now)
    payload["unexpected"] = True
    with pytest.raises(ContractValidationError, match="unknown fields"):
        registry.validate("OrderIntent@1", payload)

    payload = valid_intent(now)
    payload.pop("symbol")
    with pytest.raises(ContractValidationError, match="missing required"):
        registry.validate("OrderIntent@1", payload)

    payload = valid_intent(now)
    payload["quantity"] = 10
    with pytest.raises(ContractValidationError, match="encoded as string"):
        registry.validate("OrderIntent@1", payload)


def test_registry_rejects_nonfinite_decimals_and_naive_timestamps(now):
    registry = ContractRegistry.load_default()
    payload = valid_intent(now)
    payload["quantity"] = "Infinity"
    with pytest.raises(ContractValidationError, match="finite"):
        registry.validate("OrderIntent@1", payload)

    payload = valid_intent(now)
    payload["created_at"] = now.replace(tzinfo=None).isoformat()
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        registry.validate("OrderIntent@1", payload)


def test_registry_rejects_boolean_for_integer():
    registry = ContractRegistry.load_default()
    payload = {
        "session_date": "2026-08-10",
        "day_start_equity": "100",
        "peak_equity": "100",
        "current_equity": "100",
        "daily_pnl": "0",
        "drawdown": "0",
        "version": True,
        "updated_at": "2026-08-10T12:00:00+00:00",
    }
    with pytest.raises(ContractValidationError, match="integer"):
        registry.validate("RiskTelemetryState@1", payload)


def test_registry_parser_rejects_duplicate_contract_and_missing_reference():
    base = json.loads(
        (ContractRegistry.load_default.__func__.__globals__["Path"](__file__).resolve().parents[1]
         / "src" / "autotrade" / "contracts" / "registry.json").read_text(encoding="utf-8")
    )
    duplicate = deepcopy(base)
    duplicate["contracts"].append(deepcopy(duplicate["contracts"][0]))
    with pytest.raises(ContractRegistryError, match="duplicate contract"):
        ContractRegistry(duplicate)

    missing_ref = deepcopy(base)
    broker = next(item for item in missing_ref["contracts"] if item["name"] == "BrokerExecution")
    broker["fields"]["fills"]["items_contract"] = "Missing@1"
    with pytest.raises(ContractRegistryError, match="references missing"):
        ContractRegistry(missing_ref)


def test_additive_compatibility_rules():
    document = {
        "registry_version": 1,
        "contracts": [
            {
                "name": "Example",
                "version": 1,
                "compatibility": "additive",
                "allow_extra_fields": False,
                "fields": {"id": {"type": "string", "non_empty": True}},
            },
            {
                "name": "Example",
                "version": 2,
                "compatibility": "additive",
                "allow_extra_fields": False,
                "fields": {
                    "id": {"type": "string", "non_empty": True},
                    "note": {"type": "string", "required": False},
                },
            },
        ],
    }
    registry = ContractRegistry(document)
    registry.assert_additive_compatible("Example@1", "Example@2")

    incompatible = deepcopy(document)
    incompatible["contracts"][1]["fields"]["note"]["required"] = True
    bad = ContractRegistry(incompatible)
    with pytest.raises(ContractCompatibilityError, match="optional"):
        bad.assert_additive_compatible("Example@1", "Example@2")


def test_contract_parser_rejects_bad_top_level_and_field_shapes():
    with pytest.raises(ContractRegistryError):
        ContractRegistry({"registry_version": 0, "contracts": []})
    with pytest.raises(ContractRegistryError, match="contracts"):
        ContractRegistry({"registry_version": 1, "contracts": "bad"})
    with pytest.raises(ContractRegistryError, match="entries"):
        ContractRegistry({"registry_version": 1, "contracts": ["bad"]})


def test_unknown_contract_fails_closed():
    registry = ContractRegistry.load_default()
    with pytest.raises(ContractRegistryError, match="unknown contract"):
        registry.validate("Nope@99", {})
