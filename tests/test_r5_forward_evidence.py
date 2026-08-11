from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import sqlite3

import pytest

from autotrade.research.forward import (
    ForwardEvidenceConflict,
    ForwardEvidenceIntegrityError,
    FrozenForwardPolicy,
    SQLiteForwardEvidenceRegistry,
)
from autotrade.research.shadow import (
    FrozenShadowConfig,
    ShadowIntegrityError,
    SQLitePortfolioShadowRegistry,
    StrategyShadowObservation,
)


UTC = timezone.utc
ACTIVATED = datetime(2026, 8, 11, 7, 0, tzinfo=UTC)


def h(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def shadow_config() -> FrozenShadowConfig:
    return FrozenShadowConfig(
        config_id="shadow-v1",
        activated_at=ACTIVATED - timedelta(minutes=30),
        initial_nav=Decimal("100000"),
        strategy_weights={"trend": Decimal("0.6"), "meanrev": Decimal("0.4")},
        source_config_hash=h("portfolio-frozen"),
    )


def observation(strategy_id: str, start: datetime, value: str) -> StrategyShadowObservation:
    return StrategyShadowObservation(
        strategy_id=strategy_id,
        period_started_at=start,
        period_ended_at=start + timedelta(minutes=1),
        return_fraction=Decimal(value),
        source_fingerprint=h(f"{strategy_id}:{start.isoformat()}:{value}"),
    )


def append_shadow_period(
    registry: SQLitePortfolioShadowRegistry,
    start: datetime,
    *,
    trend: str = "0.01",
    meanrev: str = "-0.005",
):
    return registry.append_period(
        (
            observation("trend", start, trend),
            observation("meanrev", start, meanrev),
        )
    )


def make_shadow(path) -> SQLitePortfolioShadowRegistry:
    registry = SQLitePortfolioShadowRegistry(path)
    registry.register_config(shadow_config())
    return registry


def policy(*, activation: datetime = ACTIVATED, config_fingerprint: str | None = None) -> FrozenForwardPolicy:
    return FrozenForwardPolicy(
        campaign_id="forward-v1",
        activated_at=activation,
        shadow_config_fingerprint=config_fingerprint or shadow_config().fingerprint,
        frozen_parameters_hash=h("frozen-selection-and-thresholds"),
        source_code_hash=h("source-code"),
    )


def make_forward(path, *, forward_policy: FrozenForwardPolicy | None = None) -> SQLiteForwardEvidenceRegistry:
    registry = SQLiteForwardEvidenceRegistry(path)
    registry.register_policy(forward_policy or policy())
    return registry


def test_forward_policy_is_frozen_and_idempotent(tmp_path) -> None:
    registry = SQLiteForwardEvidenceRegistry(tmp_path / "forward.sqlite")
    first = registry.register_policy(policy())
    second = registry.register_policy(policy())
    assert first.fingerprint == second.fingerprint

    with pytest.raises(ForwardEvidenceConflict, match="already frozen"):
        registry.register_policy(
            FrozenForwardPolicy(
                campaign_id="forward-v1",
                activated_at=ACTIVATED,
                shadow_config_fingerprint=shadow_config().fingerprint,
                frozen_parameters_hash=h("different-parameters"),
                source_code_hash=h("source-code"),
            )
        )


def test_forward_evidence_is_sourced_from_verified_shadow_and_bound_to_policy(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "shadow.sqlite")
    source = append_shadow_period(shadow, ACTIVATED)
    forward = make_forward(tmp_path / "forward.sqlite")

    evidence = forward.append_shadow_record(
        shadow_registry=shadow,
        shadow_record_hash=source.record_hash,
    )

    assert evidence.domain == "FORWARD_POST_ACTIVATION"
    assert evidence.shadow_record_hash == source.record_hash
    assert evidence.shadow_config_fingerprint == shadow_config().fingerprint
    assert evidence.portfolio_return == source.weighted_return
    assert evidence.nav_after == source.nav_after
    assert forward.control_state().head_hash == evidence.evidence_hash


def test_unknown_or_malformed_shadow_hash_is_rejected(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "shadow.sqlite")
    forward = make_forward(tmp_path / "forward.sqlite")

    with pytest.raises(ForwardEvidenceIntegrityError, match="lowercase SHA-256"):
        forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash="not-a-hash")
    with pytest.raises(ForwardEvidenceIntegrityError, match="not present"):
        forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=h("missing"))


def test_forward_evidence_cannot_predate_activation(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "shadow.sqlite")
    source = append_shadow_period(shadow, ACTIVATED - timedelta(minutes=1))
    forward = make_forward(tmp_path / "forward.sqlite")

    with pytest.raises(ForwardEvidenceIntegrityError, match="predate activation"):
        forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=source.record_hash)
    assert forward.list_records() == ()


def test_forward_policy_rejects_shadow_from_different_frozen_config(tmp_path) -> None:
    altered = FrozenShadowConfig(
        config_id="shadow-v2",
        activated_at=ACTIVATED - timedelta(minutes=30),
        initial_nav=Decimal("100000"),
        strategy_weights={"trend": Decimal("0.7"), "meanrev": Decimal("0.3")},
        source_config_hash=h("portfolio-frozen-v2"),
    )
    shadow = SQLitePortfolioShadowRegistry(tmp_path / "shadow.sqlite")
    shadow.register_config(altered)
    source = shadow.append_period(
        (
            observation("trend", ACTIVATED, "0.01"),
            observation("meanrev", ACTIVATED, "-0.005"),
        )
    )
    forward = make_forward(tmp_path / "forward.sqlite")

    with pytest.raises(ForwardEvidenceIntegrityError, match="config does not match"):
        forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=source.record_hash)


def test_identical_forward_replay_is_idempotent(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "shadow.sqlite")
    source = append_shadow_period(shadow, ACTIVATED)
    forward = make_forward(tmp_path / "forward.sqlite")

    first = forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=source.record_hash)
    replay = forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=source.record_hash)

    assert replay == first
    assert forward.list_records() == (first,)
    assert forward.control_state().sequence == 1


def test_conflicting_same_period_shadow_evidence_fails_closed(tmp_path) -> None:
    shadow_a = make_shadow(tmp_path / "shadow-a.sqlite")
    shadow_b = make_shadow(tmp_path / "shadow-b.sqlite")
    source_a = append_shadow_period(shadow_a, ACTIVATED, trend="0.01")
    source_b = append_shadow_period(shadow_b, ACTIVATED, trend="0.02")
    assert source_a.record_hash != source_b.record_hash

    forward = make_forward(tmp_path / "forward.sqlite")
    forward.append_shadow_record(shadow_registry=shadow_a, shadow_record_hash=source_a.record_hash)

    with pytest.raises(ForwardEvidenceConflict, match="conflicting forward evidence"):
        forward.append_shadow_record(shadow_registry=shadow_b, shadow_record_hash=source_b.record_hash)
    assert forward.control_state().sequence == 1


def test_forward_periods_must_be_contiguous_even_if_shadow_contains_gap_candidate(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "shadow.sqlite")
    first = append_shadow_period(shadow, ACTIVATED)
    append_shadow_period(shadow, ACTIVATED + timedelta(minutes=1))
    third = append_shadow_period(shadow, ACTIVATED + timedelta(minutes=2))
    forward = make_forward(tmp_path / "forward.sqlite")

    forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=first.record_hash)
    with pytest.raises(ForwardEvidenceIntegrityError, match="strictly contiguous"):
        forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=third.record_hash)
    assert len(forward.list_records()) == 1


def test_forward_chain_persists_across_reopen(tmp_path) -> None:
    shadow_db = tmp_path / "shadow.sqlite"
    forward_db = tmp_path / "forward.sqlite"
    shadow = make_shadow(shadow_db)
    first_source = append_shadow_period(shadow, ACTIVATED)
    second_source = append_shadow_period(shadow, ACTIVATED + timedelta(minutes=1))
    forward = make_forward(forward_db)
    first = forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=first_source.record_hash)
    second = forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=second_source.record_hash)

    reopened = SQLiteForwardEvidenceRegistry(forward_db)
    assert reopened.list_records() == (first, second)
    assert reopened.control_state().head_hash == second.evidence_hash


def test_forward_tail_deletion_is_detected_by_control_anchor(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "shadow.sqlite")
    source = append_shadow_period(shadow, ACTIVATED)
    db = tmp_path / "forward.sqlite"
    forward = make_forward(db)
    forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=source.record_hash)

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM forward_records WHERE sequence = 1")
        conn.commit()

    with pytest.raises(ForwardEvidenceIntegrityError, match="control sequence"):
        forward.list_records()


def test_forward_record_mutation_is_detected(tmp_path) -> None:
    shadow = make_shadow(tmp_path / "shadow.sqlite")
    source = append_shadow_period(shadow, ACTIVATED)
    db = tmp_path / "forward.sqlite"
    forward = make_forward(db)
    forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=source.record_hash)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE forward_records SET evidence_json = replace(evidence_json, '0.004', '0.9') WHERE sequence = 1"
        )
        conn.commit()

    with pytest.raises(ForwardEvidenceIntegrityError):
        forward.list_records()


def test_tampered_shadow_source_is_rejected_before_forward_append(tmp_path) -> None:
    shadow_db = tmp_path / "shadow.sqlite"
    shadow = make_shadow(shadow_db)
    source = append_shadow_period(shadow, ACTIVATED)
    forward = make_forward(tmp_path / "forward.sqlite")

    with sqlite3.connect(shadow_db) as conn:
        conn.execute("UPDATE shadow_records SET record_json = '{}' WHERE sequence = 1")
        conn.commit()

    with pytest.raises(ShadowIntegrityError):
        forward.append_shadow_record(shadow_registry=shadow, shadow_record_hash=source.record_hash)
    assert forward.list_records() == ()
