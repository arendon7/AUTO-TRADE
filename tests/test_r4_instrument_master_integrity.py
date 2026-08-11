from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from autotrade.instrument_master import (
    AuthoritativeInstrumentRules,
    InstrumentConstraintViolation,
    InstrumentRuleConflict,
    InstrumentTradingStatus,
    SQLiteInstrumentMaster,
)
from autotrade.persistence import SQLiteRuntime


def rules(now, **overrides):
    values = {
        "venue": "TEST-VENUE",
        "symbol": "BTC-USD",
        "base_currency": "BTC",
        "quote_currency": "USD",
        "version": 1,
        "price_tick": Decimal("0.01"),
        "quantity_step": Decimal("0.001"),
        "min_quantity": Decimal("0.001"),
        "max_quantity": Decimal("10.000"),
        "min_notional": Decimal("10"),
        "max_notional": Decimal("100000"),
        "trading_status": InstrumentTradingStatus.TRADING,
        "source": "venue-public-instrument-rules",
        "source_version": "snapshot-1",
        "source_payload_sha256": "a" * 64,
        "observed_at": now,
        "valid_until": now + timedelta(minutes=10),
    }
    values.update(overrides)
    return AuthoritativeInstrumentRules(**values)


def runtime_and_store(tmp_path, name="master.db"):
    runtime = SQLiteRuntime(tmp_path / name)
    return runtime, SQLiteInstrumentMaster(runtime)


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def test_identity_and_source_fields_must_be_canonical_not_whitespace_aliases(now):
    for field in (
        "venue",
        "symbol",
        "base_currency",
        "quote_currency",
        "source",
        "source_version",
    ):
        baseline = rules(now)
        with pytest.raises(ValueError, match="surrounding whitespace"):
            replace(baseline, **{field: f" {getattr(baseline, field)}"})


def test_quantity_bounds_must_align_to_authoritative_step(now):
    with pytest.raises(ValueError, match="min_quantity must align"):
        rules(now, min_quantity=Decimal("0.0015"))
    with pytest.raises(ValueError, match="max_quantity must align"):
        rules(now, max_quantity=Decimal("10.0005"))


def test_payload_and_persisted_fingerprint_are_independent_integrity_checks(tmp_path, now):
    runtime, store = runtime_and_store(tmp_path)
    original = rules(now)
    store.publish(original, now=now)

    tampered = replace(
        original,
        price_tick=Decimal("0.05"),
        source_version="tampered-snapshot",
        source_payload_sha256="b" * 64,
    )
    # Attacker/corruption rewrites payload and its *embedded* fingerprint but
    # cannot silently satisfy the separate persisted fingerprint commitment.
    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE instrument_master SET payload_json = ? WHERE venue = ? AND symbol = ? AND version = 1",
            (canonical(tampered.to_payload()), original.venue, original.symbol),
        )
    finally:
        conn.close()

    for reader in (
        lambda: store.latest(venue=original.venue, symbol=original.symbol),
        lambda: store.get_version(venue=original.venue, symbol=original.symbol, version=1),
        lambda: store.history(venue=original.venue, symbol=original.symbol),
    ):
        with pytest.raises(InstrumentRuleConflict, match="stored instrument-rule fingerprint mismatch"):
            reader()


def test_persisted_fingerprint_column_tamper_fails_closed(tmp_path, now):
    runtime, store = runtime_and_store(tmp_path, "fingerprint.db")
    original = rules(now)
    store.publish(original, now=now)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE instrument_master SET fingerprint = ? WHERE venue = ? AND symbol = ? AND version = 1",
            ("b" * 64, original.venue, original.symbol),
        )
    finally:
        conn.close()

    with pytest.raises(InstrumentRuleConflict, match="stored instrument-rule fingerprint mismatch"):
        store.latest(venue=original.venue, symbol=original.symbol)


def test_invalid_stored_fingerprint_format_fails_closed(tmp_path, now):
    runtime, store = runtime_and_store(tmp_path, "bad-fingerprint.db")
    original = rules(now)
    store.publish(original, now=now)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE instrument_master SET fingerprint = 'CORRUPT' WHERE venue = ? AND symbol = ? AND version = 1",
            (original.venue, original.symbol),
        )
    finally:
        conn.close()

    with pytest.raises(InstrumentRuleConflict, match="fingerprint is invalid"):
        store.latest(venue=original.venue, symbol=original.symbol)


def test_invalid_stored_json_fails_closed(tmp_path, now):
    runtime, store = runtime_and_store(tmp_path, "bad-json.db")
    original = rules(now)
    store.publish(original, now=now)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE instrument_master SET payload_json = '{bad-json' WHERE venue = ? AND symbol = ? AND version = 1",
            (original.venue, original.symbol),
        )
    finally:
        conn.close()

    with pytest.raises(InstrumentRuleConflict, match="payload is invalid"):
        store.latest(venue=original.venue, symbol=original.symbol)


def test_corrupted_previous_version_blocks_new_publication(tmp_path, now):
    runtime, store = runtime_and_store(tmp_path, "publish-after-corruption.db")
    original = rules(now)
    store.publish(original, now=now)

    conn = runtime.connect()
    try:
        conn.execute(
            "UPDATE instrument_master SET fingerprint = ? WHERE venue = ? AND symbol = ? AND version = 1",
            ("c" * 64, original.venue, original.symbol),
        )
    finally:
        conn.close()

    next_rules = replace(
        original,
        version=2,
        source_version="snapshot-2",
        source_payload_sha256="d" * 64,
        observed_at=now + timedelta(minutes=1),
        valid_until=now + timedelta(minutes=11),
    )
    with pytest.raises(InstrumentRuleConflict, match="stored instrument-rule fingerprint mismatch"):
        store.publish(next_rules, now=now + timedelta(minutes=1))


def test_nonfinite_candidate_inputs_fail_before_tick_math(now):
    item = rules(now)
    for quantity in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(InstrumentConstraintViolation, match="quantity must be finite"):
            item.validate_candidate(quantity=quantity, price=Decimal("10000.00"))
    for price in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
        with pytest.raises(InstrumentConstraintViolation, match="price must be finite"):
            item.validate_candidate(quantity=Decimal("1.000"), price=price)


def test_invalid_decimal_payload_is_normalized_to_value_error(now):
    payload = rules(now).to_payload()
    payload.pop("fingerprint")
    payload["price_tick"] = "not-decimal"
    with pytest.raises(ValueError, match="invalid decimal"):
        AuthoritativeInstrumentRules.from_payload(payload)
