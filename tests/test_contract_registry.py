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
    assert len(ids) == 9
    assert len(set(ids)) == 9
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

    payload = valid_intent(now)
    payload["created_at"] = now.replace(tzinfo=None).isoformat()
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        registry.validate("OrderIntent@1", payload)

    with pytest.raises(ContractRegistryError, match="unknown contract"):
        registry.get("Missing@1")


def test_scalar_contract_types_are_strict(now):
    registry = ContractRegistry.load_default()
    decision = {
        "decision_id": "d",
        "intent_id": "i",
        "status": "APPROVED",
        "reason_code": "APPROVED",
        "reason_detail": "ok",
        "evaluated_at": now.isoformat(),
        "valid_until": now.isoformat(),
        "limits_version": "v1",
        "intent_fingerprint": "a",
        "market_fingerprint": "b",
        "approved_notional": "100",
        "risk_reducing": False,
        "safety_state_version": 1,
    }
    registry.validate("RiskDecision@1", decision)

    for key, invalid, message in (
        ("decision_id", 1, "string"),
        ("decision_id", "", "non-empty"),
        ("risk_reducing", 1, "boolean"),
        ("safety_state_version", True, "integer"),
        ("evaluated_at", "not-time", "invalid timestamp"),
        ("approved_notional", "Infinity", "finite"),
    ):
        bad = dict(decision)
        bad[key] = invalid
        with pytest.raises(ContractValidationError, match=message):
            registry.validate("RiskDecision@1", bad)


def test_array_and_object_shapes_are_enforced(now):
    registry = ContractRegistry.load_default()
    with pytest.raises(ContractValidationError, match="must be array"):
        registry.validate("BrokerExecution@1", {"status": "SUBMITTED", "fills": {}})
    with pytest.raises(ContractValidationError, match="must be object"):
        registry.validate(
            "BrokerExecution@1", {"status": "SUBMITTED", "fills": ["not-object"]}
        )
    with pytest.raises(ContractValidationError, match="must be object"):
        registry.validate(
            "OrderRecord@1",
            {
                "order_id": "o",
                "intent": "bad",
                "risk_decision_id": "r",
                "status": "SUBMITTED",
                "created_at": now.isoformat(),
                "submitted_at": None,
                "filled_quantity": "0",
                "average_fill_price": None,
            },
        )


def registry_doc(*, compatibility="additive", version=1, extra_fields=None):
    fields = {
        "id": {"type": "string", "non_empty": True},
    }
    fields.update(extra_fields or {})
    return {
        "registry_version": 1,
        "contracts": [
            {
                "name": "Example",
                "version": version,
                "compatibility": compatibility,
                "allow_extra_fields": False,
                "fields": fields,
            }
        ],
    }


def test_additive_compatibility_accepts_only_optional_new_fields():
    document = {
        "registry_version": 1,
        "contracts": registry_doc()["contracts"]
        + registry_doc(
            version=2,
            extra_fields={"note": {"type": "string", "required": False}},
        )["contracts"],
    }
    registry = ContractRegistry(document)
    registry.assert_additive_compatible("Example@1", "Example@2")

    required_doc = deepcopy(document)
    required_doc["contracts"][1]["fields"]["note"]["required"] = True
    with pytest.raises(ContractCompatibilityError, match="must be optional"):
        ContractRegistry(required_doc).assert_additive_compatible("Example@1", "Example@2")

    changed_doc = deepcopy(document)
    changed_doc["contracts"][1]["fields"]["id"]["non_empty"] = False
    with pytest.raises(ContractCompatibilityError, match="existing field changed"):
        ContractRegistry(changed_doc).assert_additive_compatible("Example@1", "Example@2")


def test_additive_compatibility_rejects_wrong_name_version_policy_and_removed_field():
    document = {
        "registry_version": 1,
        "contracts": [
            registry_doc()["contracts"][0],
            registry_doc(version=2)["contracts"][0],
            {
                "name": "Other",
                "version": 2,
                "compatibility": "additive",
                "fields": {"id": {"type": "string", "non_empty": True}},
            },
            {
                "name": "Example",
                "version": 3,
                "compatibility": "strict",
                "fields": {"id": {"type": "string", "non_empty": True}},
            },
            {
                "name": "Example",
                "version": 4,
                "compatibility": "additive",
                "fields": {"note": {"type": "string", "required": False}},
            },
        ],
    }
    registry = ContractRegistry(document)
    with pytest.raises(ContractCompatibilityError, match="names"):
        registry.assert_additive_compatible("Example@1", "Other@2")
    with pytest.raises(ContractCompatibilityError, match="must increase"):
        registry.assert_additive_compatible("Example@2", "Example@1")
    with pytest.raises(ContractCompatibilityError, match="declare additive"):
        registry.assert_additive_compatible("Example@2", "Example@3")
    with pytest.raises(ContractCompatibilityError, match="field removed"):
        registry.assert_additive_compatible("Example@1", "Example@4")


def test_registry_definition_fails_closed_on_malformed_documents():
    invalid_documents = [
        ({"registry_version": 0, "contracts": [{}]}, "registry_version"),
        ({"registry_version": 1, "contracts": []}, "non-empty array"),
        ({"registry_version": 1, "contracts": ["bad"]}, "must be objects"),
        (
            {
                "registry_version": 1,
                "contracts": [
                    {"name": "X", "version": 1, "compatibility": "strict", "fields": {"a": {"type": "wat"}}}
                ],
            },
            "unsupported type",
        ),
        (
            {
                "registry_version": 1,
                "contracts": [
                    {
                        "name": "X",
                        "version": 1,
                        "compatibility": "strict",
                        "fields": {"a": {"type": "array", "items_contract": "Missing@1"}},
                    }
                ],
            },
            "references missing contract",
        ),
    ]
    for document, message in invalid_documents:
        with pytest.raises(ContractRegistryError, match=message):
            ContractRegistry(document)


def test_registry_rejects_duplicate_ids_unknown_keys_bad_flags_and_bad_references():
    duplicate = registry_doc()
    duplicate["contracts"].append(deepcopy(duplicate["contracts"][0]))
    with pytest.raises(ContractRegistryError, match="duplicate contract id"):
        ContractRegistry(duplicate)

    unknown = registry_doc()
    unknown["contracts"][0]["extra"] = 1
    with pytest.raises(ContractRegistryError, match="unknown contract keys"):
        ContractRegistry(unknown)

    bad_flag = registry_doc()
    bad_flag["contracts"][0]["fields"]["id"]["required"] = "yes"
    with pytest.raises(ContractRegistryError, match="flags must be boolean"):
        ContractRegistry(bad_flag)

    bad_enum = registry_doc()
    bad_enum["contracts"][0]["fields"]["id"]["enum"] = [1]
    with pytest.raises(ContractRegistryError, match="enum must be string array"):
        ContractRegistry(bad_enum)

    bad_items = registry_doc()
    bad_items["contracts"][0]["fields"]["id"]["items_contract"] = "X@1"
    with pytest.raises(ContractRegistryError, match="requires array type"):
        ContractRegistry(bad_items)

    bad_object = registry_doc()
    bad_object["contracts"][0]["fields"]["id"]["object_contract"] = "X@1"
    with pytest.raises(ContractRegistryError, match="requires object type"):
        ContractRegistry(bad_object)
