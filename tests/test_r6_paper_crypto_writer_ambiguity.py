from __future__ import annotations

from datetime import timedelta

import pytest

from autotrade.brokers.alpaca_paper_crypto_lifecycle import CryptoLifecycleStatus
from autotrade.brokers.alpaca_paper_crypto_writer import (
    AlpacaPaperCryptoWriter,
    AlpacaPaperCryptoWriterConfig,
    CryptoPaperWriterAmbiguous,
    CryptoPaperWriterIntegrityError,
)
from test_r6_paper_crypto_writer import CREDS, NOW, GuardedRecordingTransport, _setup


def test_transport_integrity_failure_after_unknown_is_ambiguous_and_reconciliation_only(tmp_path) -> None:
    lifecycle, _asset_value, _profile_value, entry, binding = _setup(
        tmp_path,
        lifecycle_id="crypto-writer-post-io-integrity",
    )

    def assert_unknown_before_fault() -> None:
        state = lifecycle.snapshot(binding.lifecycle_id).state
        assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
        assert state.entry_attempt_count == 1
        assert state.restart_action == "RECONCILE_ONLY"

    transport = GuardedRecordingTransport(
        error=CryptoPaperWriterIntegrityError("simulated oversized/untrusted response"),
        before=assert_unknown_before_fault,
    )
    writer = AlpacaPaperCryptoWriter(
        config=AlpacaPaperCryptoWriterConfig(enabled=True),
        transport=transport,
    )

    with pytest.raises(CryptoPaperWriterAmbiguous, match="must be reconciled"):
        writer.submit_once(
            lifecycle=lifecycle,
            lifecycle_id=binding.lifecycle_id,
            order=entry,
            credentials=CREDS,
            now=NOW + timedelta(seconds=1),
        )

    state = lifecycle.snapshot(binding.lifecycle_id).state
    assert state.status is CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN
    assert state.entry_attempt_count == 1
    assert state.restart_action == "RECONCILE_ONLY"
    assert len(transport.calls) == 1

    with pytest.raises(Exception):
        writer.submit_once(
            lifecycle=lifecycle,
            lifecycle_id=binding.lifecycle_id,
            order=entry,
            credentials=CREDS,
            now=NOW + timedelta(seconds=2),
        )
    assert len(transport.calls) == 1
