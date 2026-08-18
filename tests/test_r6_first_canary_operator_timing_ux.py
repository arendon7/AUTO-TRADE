from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]


def test_human_staging_window_is_usable_but_within_existing_operator_authority() -> None:
    restart_safe = runpy.run_path(
        str(ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py")
    )
    approval = runpy.run_path(
        str(ROOT / "scripts/mac_crypto_first_canary_approval.py")
    )
    operator = runpy.run_path(
        str(ROOT / "src/autotrade/brokers/alpaca_paper_crypto_operator_decision.py")
    )

    assert restart_safe["HUMAN_STAGING_TTL_MS"] == 120_000
    assert approval["MAX_APPROVAL_TTL"] == timedelta(seconds=90)
    assert approval["MIN_REMAINING_PACKAGE_LIFE"] == timedelta(seconds=30)
    assert operator["_MAX_DECISION_TTL"] == timedelta(minutes=2)
    assert approval["MAX_APPROVAL_TTL"] <= operator["_MAX_DECISION_TTL"]

    source = (ROOT / "scripts/mac_crypto_first_canary_prepare_restart_safe.py").read_text(
        encoding="utf-8"
    )
    assert 'prepare_callable.__globals__["DECISION_TTL_MS"] = HUMAN_STAGING_TTL_MS' in source


def test_final_pre_post_freshness_controls_are_not_relaxed() -> None:
    execution_gate = runpy.run_path(
        str(ROOT / "src/autotrade/first_canary_execution_gate.py")
    )
    external_consent = runpy.run_path(
        str(ROOT / "src/autotrade/first_canary_external_post_consent.py")
    )

    assert execution_gate["FINAL_EVIDENCE_TTL"] == timedelta(seconds=5)
    assert external_consent["CONSENT_TTL"] == timedelta(seconds=10)


def test_real_paper_ui_revalidates_on_execute_and_blocks_premature_recovery() -> None:
    html = (ROOT / "web/mac_first_canary_real_paper.html").read_text(encoding="utf-8")

    assert 'id="recover" disabled' in html
    assert "const discovery = await discover();" in html
    assert "discovery.selection_status !== 'EXACT_ONE_READY'" in html
    assert "currentStatus.ready_for_real_post !== true" in html
    assert "currentStatus.recovery_get_only !== true" in html
    assert "$('recover').disabled = !(s && s.recovery_get_only === true);" in html
    assert "No se envió ningún POST" in html
    assert "setInterval(" not in html
