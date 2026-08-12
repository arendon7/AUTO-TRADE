from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_final_guard import (
    PaperFinalWriteBlocked,
    PaperFinalWritePhase,
)
from test_r6_final_write_guard import NOW, authorize, setup


def mark_unknown(values, attempt_id: str) -> None:
    values[2].mark_submit_attempt_unknown(
        order_id=values[0].order_id,
        attempt_id=attempt_id,
        now=NOW + timedelta(milliseconds=100),
    )


def test_pre_io_without_actual_preconsume_attestation_is_blocked(tmp_path) -> None:
    values = setup(tmp_path)
    mark_unknown(values, "missing-predecessor")
    with pytest.raises(PaperFinalWriteBlocked, match="actual PRE_CONSUME"):
        authorize(
            values,
            phase=PaperFinalWritePhase.PRE_IO,
            attempt_id="missing-predecessor",
        )


def test_intervening_kill_then_reset_still_blocks_by_safety_version(tmp_path) -> None:
    values = setup(tmp_path)
    predecessor = authorize(values)
    mark_unknown(values, "safety-version-attempt")
    values[6].activate(
        reason="intervening-kill",
        now=NOW + timedelta(milliseconds=200),
    )
    values[6].reset(now=NOW + timedelta(milliseconds=300))
    with pytest.raises(PaperFinalWriteBlocked, match="Safety state version changed"):
        authorize(
            values,
            phase=PaperFinalWritePhase.PRE_IO,
            attempt_id="safety-version-attempt",
            previous_attestation=predecessor,
        )


def test_intervening_portfolio_version_blocks_even_if_state_remains_safe(tmp_path) -> None:
    values = setup(tmp_path)
    predecessor = authorize(values)
    mark_unknown(values, "portfolio-version-attempt")
    current = values[7].get()
    updated = replace(current.snapshot, snapshot_id="guard-portfolio-002")
    assert values[7].compare_and_set(
        expected_version=current.version,
        snapshot=updated,
        now=NOW + timedelta(milliseconds=200),
    ) is not None
    with pytest.raises(PaperFinalWriteBlocked, match="Portfolio State version changed"):
        authorize(
            values,
            phase=PaperFinalWritePhase.PRE_IO,
            attempt_id="portfolio-version-attempt",
            previous_attestation=predecessor,
        )


def test_pre_io_hash_is_cryptographically_linked_to_preconsume(tmp_path) -> None:
    values = setup(tmp_path)
    predecessor = authorize(values)
    mark_unknown(values, "linked-attempt")
    successor = authorize(
        values,
        phase=PaperFinalWritePhase.PRE_IO,
        attempt_id="linked-attempt",
        previous_attestation=predecessor,
    )
    assert successor.previous_attestation_hash == predecessor.attestation_hash
    assert successor.attestation_hash != predecessor.attestation_hash
