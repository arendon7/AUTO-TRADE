from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest

from autotrade.research.shadow import (
    FrozenShadowConfig,
    ShadowConflict,
    ShadowIntegrityError,
    SQLitePortfolioShadowRegistry,
    StrategyShadowObservation,
)


UTC = timezone.utc
ACTIVATED = datetime(2026, 8, 11, 6, 30, tzinfo=UTC)
P0 = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def config(**overrides) -> FrozenShadowConfig:
    values = {
        "config_id": "shadow-v1",
        "activated_at": ACTIVATED,
        "initial_nav": Decimal("100000"),
        "strategy_weights": {
            "trend": Decimal("0.6"),
            "meanrev": Decimal("0.4"),
        },
        "source_config_hash": h("frozen-portfolio-config"),
    }
    values.update(overrides)
    return FrozenShadowConfig(**values)


def obs(
    strategy_id: str,
    start: datetime,
    value: str,
    *,
    end: datetime | None = None,
    source: str | None = None,
) -> StrategyShadowObservation:
    return StrategyShadowObservation(
        strategy_id=strategy_id,
        period_started_at=start,
        period_ended_at=end or start + timedelta(minutes=1),
        return_fraction=Decimal(value),
        source_fingerprint=h(source or f"{strategy_id}:{start.isoformat()}:{value}"),
    )


def period(start: datetime = P0, *, trend: str = "0.01", meanrev: str = "-0.005"):
    return (
        obs("trend", start, trend),
        obs("meanrev", start, meanrev),
    )


def initialized(path) -> SQLitePortfolioShadowRegistry:
    registry = SQLitePortfolioShadowRegistry(path)
    registry.register_config(config())
    return registry


def test_weights_require_exact_decimal_sum_without_tolerance() -> None:
    with pytest.raises(ValueError, match="sum exactly"):
        config(
            strategy_weights={
                "trend": Decimal("0.6000000000000000000000000000"),
                "meanrev": Decimal("0.3999999999999999999999999999"),
            }
        )


def test_config_is_frozen_idempotently_and_conflicting_reconfiguration_fails(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = SQLitePortfolioShadowRegistry(db)
    first = registry.register_config(config())
    second = registry.register_config(config())

    assert first.fingerprint == second.fingerprint
    with pytest.raises(ShadowConflict, match="already frozen"):
        registry.register_config(
            config(strategy_weights={"trend": Decimal("0.7"), "meanrev": Decimal("0.3")})
        )


def test_period_is_exactly_recomputed_and_nav_is_deterministic(tmp_path) -> None:
    registry = initialized(tmp_path / "shadow.sqlite")
    record = registry.append_period(period())

    # 0.6 * 0.01 + 0.4 * -0.005 = 0.004 exactly.
    assert record.weighted_return == Decimal("0.004")
    assert record.nav_before == Decimal("100000")
    assert record.nav_after == Decimal("100400.000")
    assert set(record.observation_payloads) == {"trend", "meanrev"}
    assert all(len(value) == 64 for value in record.observation_fingerprints.values())

    control = registry.control_state()
    assert control.sequence == 1
    assert control.head_hash == record.record_hash
    assert control.nav == record.nav_after


def test_identical_period_replay_is_idempotent_and_does_not_double_count(tmp_path) -> None:
    registry = initialized(tmp_path / "shadow.sqlite")
    first = registry.append_period(period())
    replay = registry.append_period(tuple(reversed(period())))

    assert replay == first
    assert registry.list_records() == (first,)
    assert registry.control_state().sequence == 1


def test_conflicting_replay_for_same_period_fails_closed(tmp_path) -> None:
    registry = initialized(tmp_path / "shadow.sqlite")
    registry.append_period(period())

    with pytest.raises(ShadowConflict, match="conflicting replay"):
        registry.append_period(period(trend="0.011"))
    assert registry.control_state().sequence == 1


def test_exact_frozen_strategy_universe_is_required(tmp_path) -> None:
    registry = initialized(tmp_path / "shadow.sqlite")

    with pytest.raises(ShadowIntegrityError, match="exact frozen strategy universe"):
        registry.append_period((obs("trend", P0, "0.01"),))
    with pytest.raises(ShadowIntegrityError, match="exact frozen strategy universe"):
        registry.append_period(
            period()
            + (obs("extra", P0, "0.01"),)
        )


def test_observations_must_be_timestamp_synchronized(tmp_path) -> None:
    registry = initialized(tmp_path / "shadow.sqlite")

    with pytest.raises(ShadowIntegrityError, match="synchronized timestamps"):
        registry.append_period(
            (
                obs("trend", P0, "0.01"),
                obs("meanrev", P0 + timedelta(seconds=1), "-0.005"),
            )
        )


def test_period_before_activation_and_noncontiguous_periods_fail_closed(tmp_path) -> None:
    registry = initialized(tmp_path / "shadow.sqlite")
    with pytest.raises(ShadowIntegrityError, match="precedes config activation"):
        registry.append_period(period(ACTIVATED - timedelta(minutes=1)))

    registry.append_period(period(P0))
    with pytest.raises(ShadowIntegrityError, match="strictly contiguous"):
        registry.append_period(period(P0 + timedelta(minutes=2)))
    assert len(registry.list_records()) == 1


def test_two_contiguous_periods_form_hash_chain_and_persist_across_reopen(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = initialized(db)
    first = registry.append_period(period(P0))
    second = registry.append_period(period(P0 + timedelta(minutes=1), trend="0.002", meanrev="0.001"))

    assert second.sequence == 2
    assert second.previous_record_hash == first.record_hash
    assert second.nav_before == first.nav_after

    reopened = SQLitePortfolioShadowRegistry(db)
    records = reopened.list_records()
    assert records == (first, second)
    assert reopened.control_state().head_hash == second.record_hash


def test_mutating_record_json_is_detected_on_read(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = initialized(db)
    registry.append_period(period())

    with sqlite3.connect(db) as conn:
        raw = conn.execute("SELECT record_json FROM shadow_records WHERE sequence = 1").fetchone()[0]
        payload = json.loads(raw)
        payload["weighted_return"] = "0.99"
        conn.execute(
            "UPDATE shadow_records SET record_json = ? WHERE sequence = 1",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()

    with pytest.raises(ShadowIntegrityError, match="hash mismatch"):
        registry.list_records()


def test_mutating_component_payload_is_detected_as_record_tampering(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = initialized(db)
    registry.append_period(period())

    with sqlite3.connect(db) as conn:
        raw = conn.execute("SELECT record_json FROM shadow_records WHERE sequence = 1").fetchone()[0]
        payload = json.loads(raw)
        component = json.loads(payload["observation_payloads"]["trend"])
        component["return_fraction"] = "0.50"
        payload["observation_payloads"]["trend"] = json.dumps(
            component, sort_keys=True, separators=(",", ":")
        )
        conn.execute(
            "UPDATE shadow_records SET record_json = ? WHERE sequence = 1",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()

    with pytest.raises(ShadowIntegrityError, match="hash mismatch"):
        registry.list_records()


def test_tail_deletion_is_detected_by_control_head_anchor(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = initialized(db)
    registry.append_period(period(P0))
    registry.append_period(period(P0 + timedelta(minutes=1)))

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM shadow_records WHERE sequence = 2")
        conn.commit()

    with pytest.raises(ShadowIntegrityError, match="control sequence"):
        registry.list_records()


def test_middle_deletion_is_detected_as_sequence_gap(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = initialized(db)
    registry.append_period(period(P0))
    registry.append_period(period(P0 + timedelta(minutes=1)))
    registry.append_period(period(P0 + timedelta(minutes=2)))

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM shadow_records WHERE sequence = 2")
        conn.commit()

    with pytest.raises(ShadowIntegrityError, match="sequence gap"):
        registry.list_records()


def test_control_anchor_mutation_is_detected(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = initialized(db)
    registry.append_period(period())

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE shadow_control SET head_hash = ? WHERE slot = 1", (h("forged"),))
        conn.commit()

    with pytest.raises(ShadowIntegrityError, match="control head"):
        registry.control_state()


def test_config_mutation_is_detected(tmp_path) -> None:
    db = tmp_path / "shadow.sqlite"
    registry = initialized(db)

    with sqlite3.connect(db) as conn:
        raw = conn.execute("SELECT config_json FROM shadow_config WHERE slot = 1").fetchone()[0]
        payload = json.loads(raw)
        payload["initial_nav"] = "99999"
        conn.execute(
            "UPDATE shadow_config SET config_json = ? WHERE slot = 1",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")),),
        )
        conn.commit()

    with pytest.raises(ShadowIntegrityError, match="fingerprint mismatch"):
        registry.get_config()
