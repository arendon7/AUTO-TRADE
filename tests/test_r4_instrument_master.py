from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from autotrade.instrument_master import (
    AuthoritativeInstrumentRules,
    InstrumentConstraintViolation,
    InstrumentNotTradable,
    InstrumentRuleConflict,
    InstrumentRuleNotFound,
    InstrumentRuleStale,
    InstrumentTradingStatus,
    SQLiteInstrumentMaster,
)
from autotrade.persistence import SQLiteRuntime
from autotrade.research.market import InstrumentMetadata


SOURCE_HASH = "a" * 64


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
        "max_quantity": Decimal("10"),
        "min_notional": Decimal("10"),
        "max_notional": Decimal("100000"),
        "trading_status": InstrumentTradingStatus.TRADING,
        "source": "venue-public-instrument-rules",
        "source_version": "snapshot-1",
        "source_payload_sha256": SOURCE_HASH,
        "observed_at": now,
        "valid_until": now + timedelta(minutes=10),
    }
    values.update(overrides)
    return AuthoritativeInstrumentRules(**values)


def master(tmp_path):
    runtime = SQLiteRuntime(tmp_path / "instrument-master.db")
    return SQLiteInstrumentMaster(runtime)


def test_rules_fingerprint_is_deterministic_and_bound_to_content(now):
    first = rules(now)
    second = rules(now)
    changed = replace(first, price_tick=Decimal("0.02"))
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert len(first.fingerprint) == 64


def test_payload_roundtrip_and_tamper_detection(now):
    original = rules(now)
    payload = original.to_payload()
    restored = AuthoritativeInstrumentRules.from_payload(payload)
    assert restored == original
    assert restored.fingerprint == original.fingerprint

    tampered = dict(payload)
    tampered["quantity_step"] = "0.002"
    with pytest.raises(InstrumentRuleConflict, match="fingerprint mismatch"):
        AuthoritativeInstrumentRules.from_payload(tampered)


def test_rule_validation_rejects_bad_identity_versions_hashes_and_time(now):
    with pytest.raises(ValueError, match="venue is required"):
        rules(now, venue=" ")
    with pytest.raises(ValueError, match="version"):
        rules(now, version=0)
    with pytest.raises(ValueError, match="trading_status"):
        rules(now, trading_status="TRADING")
    with pytest.raises(ValueError, match="source_payload_sha256"):
        rules(now, source_payload_sha256="not-a-hash")
    with pytest.raises(ValueError, match="observed_at"):
        rules(now.replace(tzinfo=None))
    with pytest.raises(ValueError, match="valid_until"):
        rules(now, valid_until=now)


def test_rule_validation_rejects_invalid_numeric_constraints(now):
    for field in ("price_tick", "quantity_step"):
        with pytest.raises(ValueError, match=field):
            rules(now, **{field: Decimal("0")})
    for field in ("min_quantity", "max_quantity", "min_notional", "max_notional"):
        with pytest.raises(ValueError, match=field):
            rules(now, **{field: Decimal("NaN")})
    with pytest.raises(ValueError, match="min_quantity"):
        rules(now, min_quantity=Decimal("11"), max_quantity=Decimal("10"))
    with pytest.raises(ValueError, match="min_notional"):
        rules(now, min_notional=Decimal("100001"), max_notional=Decimal("100000"))


def test_publish_is_append_only_versioned_and_idempotent(tmp_path, now):
    store = master(tmp_path)
    v1 = rules(now)
    assert store.publish(v1, now=now) == v1
    assert store.publish(v1, now=now + timedelta(seconds=1)) == v1
    assert store.get_version(venue=v1.venue, symbol=v1.symbol, version=1) == v1
    assert store.latest(venue=v1.venue, symbol=v1.symbol) == v1

    v2 = replace(
        v1,
        version=2,
        source_version="snapshot-2",
        source_payload_sha256="b" * 64,
        observed_at=now + timedelta(minutes=1),
        valid_until=now + timedelta(minutes=11),
        price_tick=Decimal("0.05"),
    )
    assert store.publish(v2, now=now + timedelta(minutes=1)) == v2
    assert store.latest(venue=v1.venue, symbol=v1.symbol) == v2
    assert store.history(venue=v1.venue, symbol=v1.symbol) == (v1, v2)


def test_same_version_with_changed_content_fails_closed(tmp_path, now):
    store = master(tmp_path)
    original = rules(now)
    store.publish(original, now=now)
    conflicting = replace(original, price_tick=Decimal("0.05"))
    with pytest.raises(InstrumentRuleConflict, match="version identity conflict"):
        store.publish(conflicting, now=now + timedelta(seconds=1))
    assert store.latest(venue=original.venue, symbol=original.symbol) == original


def test_versions_must_be_contiguous_and_observation_time_monotonic(tmp_path, now):
    store = master(tmp_path)
    with pytest.raises(InstrumentRuleConflict, match="first.*version.*1"):
        store.publish(rules(now, version=2), now=now)

    first = rules(now)
    store.publish(first, now=now)
    with pytest.raises(InstrumentRuleConflict, match="advance exactly by one"):
        store.publish(
            replace(
                first,
                version=3,
                source_version="snapshot-3",
                source_payload_sha256="c" * 64,
                observed_at=now + timedelta(minutes=2),
                valid_until=now + timedelta(minutes=12),
            ),
            now=now + timedelta(minutes=2),
        )
    with pytest.raises(InstrumentRuleConflict, match="observed_at cannot move backwards"):
        store.publish(
            replace(
                first,
                version=2,
                source_version="snapshot-2",
                source_payload_sha256="b" * 64,
                observed_at=now - timedelta(seconds=1),
                valid_until=now + timedelta(minutes=9),
            ),
            now=now + timedelta(minutes=1),
        )


def test_future_observation_cannot_be_published(tmp_path, now):
    store = master(tmp_path)
    with pytest.raises(InstrumentRuleConflict, match="future"):
        store.publish(rules(now + timedelta(seconds=1)), now=now)


def test_missing_current_and_stale_rules_fail_closed(tmp_path, now):
    store = master(tmp_path)
    with pytest.raises(InstrumentRuleNotFound):
        store.latest(venue="TEST-VENUE", symbol="MISSING")

    current = rules(now, valid_until=now + timedelta(hours=1))
    store.publish(current, now=now)
    assert store.require_current(
        venue=current.venue,
        symbol=current.symbol,
        now=now + timedelta(minutes=5),
        max_age=timedelta(minutes=5),
    ) == current
    with pytest.raises(InstrumentRuleStale, match="max_age"):
        store.require_current(
            venue=current.venue,
            symbol=current.symbol,
            now=now + timedelta(minutes=5, microseconds=1),
            max_age=timedelta(minutes=5),
        )


def test_expired_or_future_latest_rules_fail_closed(tmp_path, now):
    store = master(tmp_path)
    expiring = rules(now, valid_until=now + timedelta(minutes=2))
    store.publish(expiring, now=now)
    with pytest.raises(InstrumentRuleStale, match="expired"):
        store.require_current(
            venue=expiring.venue,
            symbol=expiring.symbol,
            now=now + timedelta(minutes=2, microseconds=1),
            max_age=timedelta(hours=1),
        )

    # Corrupting observation time through normal publish is impossible. The
    # future check still exists at read boundary for persisted-clock anomalies.
    runtime = SQLiteRuntime(tmp_path / "future-read.db")
    future_store = SQLiteInstrumentMaster(runtime)
    future = rules(now + timedelta(minutes=1))
    future_store.publish(future, now=now + timedelta(minutes=1))
    with pytest.raises(InstrumentRuleStale, match="future"):
        future_store.require_current(
            venue=future.venue,
            symbol=future.symbol,
            now=now,
            max_age=timedelta(hours=1),
        )


def test_require_current_validates_clock_and_age_policy(tmp_path, now):
    store = master(tmp_path)
    current = rules(now)
    store.publish(current, now=now)
    with pytest.raises(ValueError, match="timezone-aware"):
        store.require_current(
            venue=current.venue,
            symbol=current.symbol,
            now=now.replace(tzinfo=None),
            max_age=timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="max_age"):
        store.require_current(
            venue=current.venue,
            symbol=current.symbol,
            now=now,
            max_age=timedelta(0),
        )


def test_non_trading_status_never_becomes_tradable_by_freshness(tmp_path, now):
    for status in (
        InstrumentTradingStatus.HALTED,
        InstrumentTradingStatus.DISABLED,
        InstrumentTradingStatus.UNKNOWN,
    ):
        runtime = SQLiteRuntime(tmp_path / f"{status.value}.db")
        store = SQLiteInstrumentMaster(runtime)
        item = rules(now, trading_status=status)
        store.publish(item, now=now)
        assert store.require_current(
            venue=item.venue,
            symbol=item.symbol,
            now=now,
            max_age=timedelta(minutes=1),
        ) == item
        with pytest.raises(InstrumentNotTradable, match=status.value):
            store.require_tradable(
                venue=item.venue,
                symbol=item.symbol,
                now=now,
                max_age=timedelta(minutes=1),
            )


def test_candidate_constraints_allow_exact_boundaries_and_reject_epsilon(now):
    item = rules(now)
    assert item.validate_candidate(
        quantity=Decimal("0.001"),
        price=Decimal("10000.00"),
    ) == Decimal("10.00000")
    assert item.validate_candidate(
        quantity=Decimal("10.000"),
        price=Decimal("10000.00"),
    ) == Decimal("100000.00000")

    cases = [
        (Decimal("0.0015"), Decimal("10000.00"), "quantity_step"),
        (Decimal("0.001"), Decimal("10000.001"), "price_tick"),
        (Decimal("0.000"), Decimal("10000.00"), "quantity must"),
        (Decimal("0.001"), Decimal("0"), "price must"),
        (Decimal("0.001"), Decimal("9999.99"), "min_notional"),
        (Decimal("10.001"), Decimal("10000.00"), "max_quantity"),
    ]
    for quantity, price, message in cases:
        with pytest.raises(InstrumentConstraintViolation, match=message):
            item.validate_candidate(quantity=quantity, price=price)


def test_max_notional_is_independently_enforced(now):
    item = rules(now, max_quantity=None, max_notional=Decimal("100000"))
    with pytest.raises(InstrumentConstraintViolation, match="max_notional"):
        item.validate_candidate(quantity=Decimal("10.001"), price=Decimal("10000.00"))


def test_non_trading_rule_cannot_validate_candidate(now):
    item = rules(now, trading_status=InstrumentTradingStatus.HALTED)
    with pytest.raises(InstrumentNotTradable):
        item.validate_candidate(quantity=Decimal("1.000"), price=Decimal("10000.00"))


def test_research_metadata_is_never_implicitly_authoritative(tmp_path, now):
    research_only = InstrumentMetadata(
        symbol="BTC-USD",
        venue="TEST-VENUE",
        quote_currency="USD",
        price_tick=Decimal("1E-8"),
        quantity_step=Decimal("1E-8"),
    )
    store = master(tmp_path)
    with pytest.raises(TypeError, match="AuthoritativeInstrumentRules"):
        store.publish(research_only, now=now)  # type: ignore[arg-type]
    with pytest.raises(InstrumentRuleNotFound):
        store.latest(venue="TEST-VENUE", symbol="BTC-USD")


def test_store_survives_restart_and_multiple_instances(tmp_path, now):
    db = tmp_path / "restart.db"
    first_store = SQLiteInstrumentMaster(SQLiteRuntime(db))
    original = rules(now)
    first_store.publish(original, now=now)

    restarted = SQLiteInstrumentMaster(SQLiteRuntime(db))
    assert restarted.latest(venue=original.venue, symbol=original.symbol) == original
    assert restarted.publish(original, now=now + timedelta(seconds=2)) == original


def test_unknown_payload_fields_or_missing_fields_are_rejected(now):
    payload = rules(now).to_payload()
    payload["unexpected"] = "unsafe"
    with pytest.raises(ValueError, match="invalid instrument-rule payload fields"):
        AuthoritativeInstrumentRules.from_payload(payload)

    payload = rules(now).to_payload()
    del payload["price_tick"]
    with pytest.raises(ValueError, match="invalid instrument-rule payload fields"):
        AuthoritativeInstrumentRules.from_payload(payload)
