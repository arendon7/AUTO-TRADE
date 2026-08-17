from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts/mac_dashboard_one_shot.py"
PREPARE = ROOT / "scripts/mac_crypto_approval_prepare.py"
ISSUER = ROOT / "scripts/r6_issue_crypto_operator_decision_uat.py"
LAUNCHER = ROOT / "ABRIR_AUTO_TRADE.command"
TEST_GATE = ROOT / "tests/test_mac_crypto_one_shot_approval_gate.py"
TEST_PREPARE = ROOT / "tests/test_mac_crypto_approval_prepare.py"

FORBIDDEN = (
    "r6_execute_paper_canary.py",
    "alpaca_paper_writer",
    "FinalGuardedCryptoEntryTransport",
    "FinalGuardedCryptoProtectionTransport",
    "alpaca_paper_crypto_pre_io",
    "stage_external_submission",
    "submit_once",
    ".consume(",
    'env[WRITE_ENV] = "ENABLED"',
    "export R6_EXTERNAL_PAPER_WRITE=ENABLED",
)


def main() -> int:
    errors: list[str] = []
    for path in (WRAPPER, PREPARE, ISSUER, LAUNCHER, TEST_GATE, TEST_PREPARE):
        if not path.is_file():
            errors.append(f"missing one-shot approval contract file: {path.relative_to(ROOT)}")

    wrapper = WRAPPER.read_text(encoding="utf-8") if WRAPPER.is_file() else ""
    for anchor in (
        '"/api/canary-approval-prepare"',
        '"/api/canary-approval-record"',
        '"/api/canary-approval-prepare-result"',
        '"/api/canary-approval-record-result"',
        "secrets.compare_digest",
        "_issuer.issue(",
        '"decision_consumed": False',
        '"reusable_for_real_execution": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        "APPROVAL_RESULT_TTL_SECONDS = 120",
        "MAX_APPROVAL_RESULTS = 16",
        'env[WRITE_ENV] = "DISABLED"',
        "approval preparation request id already exists; no replay permitted",
        "approval record request id already exists; no replay permitted",
        "this approval preparation has already been claimed for recording",
        "UAT_APPROVAL_RECORDED_NOT_EXECUTABLE",
    ):
        if anchor not in wrapper:
            errors.append(f"one-shot approval wrapper missing anchor: {anchor}")
    if "record_operator_approval(" in wrapper:
        errors.append("Mac approval wrapper may not mint authority directly; it must delegate to the canonical issuer")
    for forbidden in FORBIDDEN:
        if forbidden in wrapper:
            errors.append(f"one-shot approval wrapper contains forbidden execution surface: {forbidden}")

    prepare = PREPARE.read_text(encoding="utf-8") if PREPARE.is_file() else ""
    for anchor in (
        'APPROVAL_DECISION_TTL_MS = 90_000',
        'APPROVAL_STRATEGY_ID = "R6_CRYPTO_PAPER_ONE_SHOT_APPROVAL_UAT"',
        "_CaptureContext",
        "_ApprovalSafetyLimits",
        'f"approval-uat-{package.package_hash[:24]}"',
        '"approval_context": context.to_dict()',
        '"approval_challenge": challenge',
        '"approval_recorded": False',
        '"decision_consumed": False',
        '"uat_only": True',
        '"reusable_for_real_execution": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        '"CRYPTO_PAPER_ONE_SHOT_APPROVAL_PREPARED"',
    ):
        if anchor not in prepare:
            errors.append(f"one-shot approval prepare script missing anchor: {anchor}")
    for forbidden in FORBIDDEN + ("record_operator_approval(",):
        if forbidden in prepare:
            errors.append(f"approval prepare script contains forbidden authority: {forbidden}")

    issuer = ISSUER.read_text(encoding="utf-8") if ISSUER.is_file() else ""
    for anchor in (
        "SQLiteCryptoOperatorDecisionRegistry",
        "crypto_operator_confirmation_challenge",
        "secrets.compare_digest",
        "registry.record_operator_approval(",
        'state.status is not CryptoOperatorDecisionStatus.ISSUED',
        '"decision_consumed": False',
        '"uat_only": True',
        '"reusable_for_real_execution": False',
        '"execution_authority": "NONE"',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
        '_MAX_UAT_APPROVAL_TTL = timedelta(seconds=60)',
        '_MIN_REMAINING_PACKAGE_LIFE = timedelta(seconds=5)',
        '_ATTEMPT_PREFIX = "approval-uat-"',
    ):
        if anchor not in issuer:
            errors.append(f"canonical crypto UAT issuer missing anchor: {anchor}")
    for forbidden in FORBIDDEN:
        if forbidden in issuer:
            errors.append(f"canonical crypto UAT issuer contains forbidden execution surface: {forbidden}")
    if issuer.count("record_operator_approval(") != 1:
        errors.append("canonical crypto UAT issuer must contain exactly one authority-minting call")

    launcher = LAUNCHER.read_text(encoding="utf-8") if LAUNCHER.is_file() else ""
    for anchor in (
        "scripts/mac_dashboard_one_shot.py",
        "scripts/mac_dashboard.py",
        "export R6_EXTERNAL_PAPER_WRITE=DISABLED",
        "unset APCA_API_KEY_ID",
        "unset APCA_API_SECRET_KEY",
    ):
        if anchor not in launcher:
            errors.append(f"approval-UAT launcher missing safety anchor: {anchor}")
    for forbidden in FORBIDDEN:
        if forbidden in launcher:
            errors.append(f"approval-UAT launcher contains forbidden execution surface: {forbidden}")

    tests = ""
    if TEST_GATE.is_file():
        tests += TEST_GATE.read_text(encoding="utf-8")
    if TEST_PREPARE.is_file():
        tests += "\n" + TEST_PREPARE.read_text(encoding="utf-8")
    for anchor in (
        "test_primary_control_center_http_prepare_and_record_recover_same_attempt_without_replay",
        'assert prepare_calls["count"] == 1',
        'assert record_calls["count"] == 1',
        "secret-paper-key",
        "secret-paper-secret",
        "test_wrong_challenge_records_nothing_and_can_be_corrected",
        "test_expiring_package_requires_fresh_preparation_before_approval",
        "test_prepare_restores_preview_module_after_success",
        "test_prepare_restores_preview_module_after_failure",
    ):
        if anchor not in tests:
            errors.append(f"one-shot approval tests missing regression anchor: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE Mac crypto one-shot approval UAT boundary: PASS "
        "(fresh preparation + exact human challenge + canonical durable ISSUED issuer only; "
        "dashboard cannot mint directly; no consume, Final Guard, broker POST, capital or LIVE authority)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
