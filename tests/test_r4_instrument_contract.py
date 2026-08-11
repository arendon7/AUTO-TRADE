from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.contract_payloads import contract_payload
from autotrade.contract_registry import ContractRegistry, ContractValidationError
from autotrade.instrument_master import AuthoritativeInstrumentRules, InstrumentTradingStatus


def instrument_rules(now):
    return AuthoritativeInstrumentRules(
        venue="TEST-VENUE",
        symbol="BTC-USD",
        base_currency="BTC",
        quote_currency="USD",
        version=1,
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        min_quantity=Decimal("0.001"),
        max_quantity=Decimal("10"),
        min_notional=Decimal("10"),
        max_notional=Decimal("100000"),
        trading_status=InstrumentTradingStatus.TRADING,
        source="venue-public-instrument-rules",
        source_version="snapshot-1",
        source_payload_sha256="a" * 64,
        observed_at=now,
        valid_until=now + timedelta(minutes=10),
    )


def test_real_instrument_rules_validate_against_machine_contract(now):
    registry = ContractRegistry.load_default()
    item = instrument_rules(now)
    contract_id, payload = contract_payload(item)
    assert contract_id == "AuthoritativeInstrumentRules@1"
    assert payload["fingerprint"] == item.fingerprint
    registry.validate(contract_id, payload)


def test_instrument_contract_rejects_schema_drift_and_nonfinite_decimals(now):
    registry = ContractRegistry.load_default()
    contract_id, payload = contract_payload(instrument_rules(now))

    drift = dict(payload)
    drift["research_precision"] = "1E-8"
    with pytest.raises(ContractValidationError, match="unknown fields"):
        registry.validate(contract_id, drift)

    nonfinite = dict(payload)
    nonfinite["price_tick"] = "NaN"
    with pytest.raises(ContractValidationError, match="finite"):
        registry.validate(contract_id, nonfinite)


def test_instrument_contract_rejects_unknown_status_and_naive_timestamp(now):
    registry = ContractRegistry.load_default()
    contract_id, payload = contract_payload(instrument_rules(now))

    bad_status = dict(payload)
    bad_status["trading_status"] = "MAYBE"
    with pytest.raises(ContractValidationError, match="one of"):
        registry.validate(contract_id, bad_status)

    bad_time = dict(payload)
    bad_time["observed_at"] = now.replace(tzinfo=None).isoformat()
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        registry.validate(contract_id, bad_time)
