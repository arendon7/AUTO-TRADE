from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/mac_bootstrap.sh"
REHEARSAL = ROOT / "scripts/mac_rehearsal.sh"
DOCTOR = ROOT / "scripts/mac_doctor.py"
START = ROOT / "scripts/mac_start.sh"
WORKSPACE_INIT = ROOT / "scripts/mac_create_workspace.py"
ENV_EXAMPLE = ROOT / ".env.example"
RUNBOOK = ROOT / "docs/MAC_PAPER_RUNBOOK.md"
STANDALONE_MANIFEST = ROOT / "MAC_STANDALONE_MANIFEST.txt"


def _is_r7_close_one_app() -> bool:
    if not STANDALONE_MANIFEST.is_file():
        return False
    text = STANDALONE_MANIFEST.read_text(encoding="utf-8")
    return (
        "manifest_version=4" in text
        and "first_canary_unified_surface=ONE_APP" in text
        and "r7_close_write=EXACT_ONE_SHOT_RISK_REDUCING" in text
        and "credentials_persisted=NO" in text
    )


def main() -> int:
    errors: list[str] = []
    r7_close_one_app = _is_r7_close_one_app()
    required_paths = [
        BOOTSTRAP,
        REHEARSAL,
        DOCTOR,
        START,
        WORKSPACE_INIT,
        RUNBOOK,
    ]
    if not r7_close_one_app:
        required_paths.append(ENV_EXAMPLE)
    for path in required_paths:
        if not path.is_file():
            errors.append(f"required Mac rehearsal artifact is missing: {path.relative_to(ROOT)}")

    if r7_close_one_app and ENV_EXAMPLE.exists():
        errors.append(
            "R7 one-app must omit .env.example so PAPER credentials remain memory-only"
        )

    inert_shell_forbidden = (
        "r6_external_paper_preflight.py",
        "r6_external_paper_market_preflight.py",
        "r6_issue_operator_decision.py",
        "r6_execute_paper_canary.py",
        "source .env",
        "curl ",
        "wget ",
        "APCA_API_KEY_ID=",
        "APCA_API_SECRET_KEY=",
    )

    if BOOTSTRAP.is_file():
        text = BOOTSTRAP.read_text(encoding="utf-8")
        required = (
            'unset APCA_API_KEY_ID',
            'unset APCA_API_SECRET_KEY',
            'R6_EXTERNAL_PAPER_WRITE:-',
            '== "ENABLED"',
            'export R6_EXTERNAL_PAPER_WRITE="DISABLED"',
            'scripts/check_r6_live_deny_boundary.py',
            'scripts/check_r6_market_data_boundary.py',
            'scripts/check_r6_operational_execution_boundary.py',
            'scripts/check_r6_readiness_boundary.py',
            'scripts/check_mac_rehearsal_boundary.py',
            'scripts/check_mac_safe_console_boundary.py',
            'tests/test_mac_safe_console.py',
            'tests/test_mac_create_workspace.py',
            'bash scripts/mac_start.sh',
            'scripts/mac_doctor.py',
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"Mac bootstrap safety anchor missing: {anchor}")
        for value in inert_shell_forbidden:
            if value in text:
                errors.append(f"Mac bootstrap must remain broker-inert: forbidden surface {value}")

    if REHEARSAL.is_file():
        text = REHEARSAL.read_text(encoding="utf-8")
        required = (
            'R6_EXTERNAL_PAPER_WRITE:-',
            '== "ENABLED"',
            'unset APCA_API_KEY_ID',
            'unset APCA_API_SECRET_KEY',
            'export R6_EXTERNAL_PAPER_WRITE="DISABLED"',
            'if [[ ! -x .venv/bin/python ]]',
            'scripts/mac_doctor.py',
            'scripts/check_contract_registry.py',
            'scripts/check_debt_register.py',
            'scripts/check_r6_authority.py',
            'scripts/check_r6_live_deny_boundary.py',
            'scripts/check_r6_market_data_boundary.py',
            'scripts/check_r6_operational_lifecycle_boundary.py',
            'scripts/check_r6_operational_execution_boundary.py',
            'scripts/check_r6_readiness_boundary.py',
            'scripts/check_mac_rehearsal_boundary.py',
            'scripts/check_mac_safe_console_boundary.py',
            'tests/test_r6_paper_market_data.py',
            'tests/test_r6_operational_prepare.py',
            'tests/test_r6_execute_paper_canary_cli.py',
            'tests/test_mac_safe_console.py',
            'tests/test_mac_create_workspace.py',
            'No PAPER order was submitted.',
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"Mac rehearsal safety anchor missing: {anchor}")
        for value in inert_shell_forbidden:
            if value in text:
                errors.append(f"Mac rehearsal must remain broker-inert: forbidden surface {value}")

    if DOCTOR.is_file():
        text = DOCTOR.read_text(encoding="utf-8")
        required = (
            '"broker_network_used": False',
            '"broker_write_performed": False',
            '"capital_authority": "NONE"',
            '"live_trading": "BLOCKED"',
            '"SET" if os.environ.get(KEY_ENV) else "NOT_SET"',
            '"SET" if os.environ.get(SECRET_ENV) else "NOT_SET"',
            "inspect_market_aware_readiness(",
        )
        for anchor in required:
            if anchor not in text:
                errors.append(f"Mac doctor non-authorizing anchor missing: {anchor}")
        for value in (
            "urllib",
            "requests",
            "websockets",
            "AlpacaPaperCredentials",
            "AlpacaPaperSingleShotWriter",
            "r6_external_paper_market_preflight",
            "r6_execute_paper_canary",
        ):
            if value in text:
                errors.append(f"Mac doctor contains forbidden network/execution surface: {value}")

    if START.is_file():
        text = START.read_text(encoding="utf-8")
        for anchor in (
            'export R6_EXTERNAL_PAPER_WRITE=DISABLED',
            'bash scripts/mac_bootstrap.sh',
            'mac_safe_console.py',
            'init-workspace',
            'This entry point has NO order execution option.',
        ):
            if anchor not in text:
                errors.append(f"Mac safe-start anchor missing: {anchor}")
        if "r6_execute_paper_canary.py" in text:
            errors.append("Mac safe-start may not expose the PAPER execution command")

    if WORKSPACE_INIT.is_file():
        text = WORKSPACE_INIT.read_text(encoding="utf-8")
        for anchor in (
            'PaperOperationalWorkspace.initialize(',
            'inspect_market_aware_readiness(',
            'operational workspaces must live outside the git repository',
            'R6_EXTERNAL_PAPER_WRITE=ENABLED',
            'credential-free',
            '"broker_network_used": False',
            '"broker_write_performed": False',
            '"execution_authorized": False',
        ):
            if anchor not in text:
                errors.append(f"Mac workspace initializer anchor missing: {anchor}")

    if ENV_EXAMPLE.is_file() and not r7_close_one_app:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        if "R6_EXTERNAL_PAPER_WRITE=DISABLED" not in text:
            errors.append(".env.example must keep external PAPER write disabled")
        for line in text.splitlines():
            if line.startswith("APCA_API_KEY_ID=") and line != "APCA_API_KEY_ID=":
                errors.append(".env.example must not contain a PAPER key")
            if line.startswith("APCA_API_SECRET_KEY=") and line != "APCA_API_SECRET_KEY=":
                errors.append(".env.example must not contain a PAPER secret")

    if RUNBOOK.is_file():
        text = RUNBOOK.read_text(encoding="utf-8")
        for anchor in (
            "## STOP — antes de cualquier orden PAPER real",
            "R6_EXTERNAL_PAPER_WRITE=DISABLED",
            "bash scripts/mac_start.sh",
            "init-workspace",
            "r6_external_paper_market_preflight.py",
            "--allow-paper-market-read",
            "external PAPER order sent: **NO**",
            "LIVE trading: **BLOCKED**",
        ):
            if anchor not in text:
                errors.append(f"Mac runbook safety anchor missing: {anchor}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    mode = "R7 memory-only one-app" if r7_close_one_app else "legacy rehearsal"
    print(
        "AUTO-TRADE Mac rehearsal boundary: PASS "
        f"({mode}; bootstrap/rehearsal/safe-start/workspace/doctor broker-inert; "
        "credentials redacted; PAPER write disabled by default)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
