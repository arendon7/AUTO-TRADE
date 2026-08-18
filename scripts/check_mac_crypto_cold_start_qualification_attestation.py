from __future__ import annotations

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ATTESTATION = ROOT / "scripts/mac_crypto_cold_start_qualification_attestation.py"
DASHBOARD = ROOT / "scripts/mac_dashboard_cold_start_attestation.py"
LAUNCHER = ROOT / "ABRIR_AUTO_TRADE.command"


class BoundaryFailure(RuntimeError):
    pass


def _source(path: Path) -> str:
    if not path.is_file():
        raise BoundaryFailure(f"missing cold-start attestation surface: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def _require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise BoundaryFailure(f"cold-start attestation boundary missing {label}: {needle}")


def _forbid_authority_imports(source: str, *, label: str) -> None:
    tree = ast.parse(source)
    forbidden = (
        "alpaca_paper_crypto_final_guard",
        "alpaca_paper_crypto_protection_final_guard",
        "alpaca_paper_crypto_execution_attempt",
        "alpaca_paper_crypto_execution_bridge",
        "alpaca_paper_crypto_protection_execution_attempt",
        "alpaca_paper_crypto_protection_execution_bridge",
        "alpaca_paper_crypto_writer",
        "alpaca_paper_crypto_pre_io",
        "alpaca_paper_crypto_operator_decision_issuer",
    )
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
        elif isinstance(node, ast.Import):
            module = " ".join(alias.name for alias in node.names)
        if any(token in module for token in forbidden):
            raise BoundaryFailure(f"{label} imports forbidden execution authority: {module}")


def _forbid_write_enable_assignment(source: str) -> None:
    patterns = (
        r"(?m)^\s*(?:export\s+)?R6_EXTERNAL_PAPER_WRITE\s*=\s*['\"]?ENABLED['\"]?\s*$",
        r"(?m)^\s*os\.environ\s*\[\s*['\"]R6_EXTERNAL_PAPER_WRITE['\"]\s*\]\s*=\s*['\"]ENABLED['\"]\s*$",
        r"(?m)^\s*\w+\s*\[\s*WRITE_ENV\s*\]\s*=\s*['\"]ENABLED['\"]\s*$",
    )
    if any(re.search(pattern, source) for pattern in patterns):
        raise BoundaryFailure("cold-start attestation surface must never enable external PAPER writes")


def main() -> int:
    attestation = _source(ATTESTATION)
    dashboard = _source(DASHBOARD)
    launcher = _source(LAUNCHER)

    for source, label in ((attestation, "attestation"), (dashboard, "dashboard")):
        _forbid_authority_imports(source, label=label)
        _forbid_write_enable_assignment(source)

    for needle, label in (
        ('EXPECTED_SYMBOL = "BTC/USD"', "BTC/USD fixed scope"),
        ('MAX_NOTIONAL = Decimal("5")', "USD 5 hard maximum"),
        ('TARGET_NOTIONAL = Decimal("2")', "USD 2 target"),
        ('BROKER_EVIDENCE_MAX_AGE = timedelta(seconds=5)', "fresh broker evidence"),
        ('ATTESTATION_TTL = timedelta(seconds=30)', "short attestation lifetime"),
        ('os.environ.get(WRITE_ENV) == "ENABLED"', "writer-enabled rejection"),
        ('_read_bootstrap_manifest', "durable Portfolio bootstrap binding"),
        ('SQLitePortfolioStore', "durable Portfolio v1 binding"),
        ('current.version != 1', "Portfolio v1 restriction"),
        ('COMMISSIONING_KILL_REASON', "commissioning kill-switch binding"),
        ('health_count != 0 or bridge_count != 0', "Health absence requirement"),
        ('_require_fresh(account.attested_at', "fresh account requirement"),
        ('_require_fresh(flat.attested_at', "fresh flat-account requirement"),
        ('preview.run', "certified preview reuse"),
        ('"broker_reads": 9', "nine broker GET observations"),
        ('"scope": "FIRST_TECHNICAL_CANARY_ONLY"', "first-canary-only scope"),
        ('"health_override_authorized": False', "Health override deny"),
        ('"health_normal_path_modified": False', "normal Health path untouched"),
        ('"kill_switch_reset": False', "kill-switch reset deny"),
        ('"qualification_candidate": True', "candidate-only semantics"),
        ('"qualification_completed": False', "qualification incomplete"),
        ('"profitability_evidence": False', "profitability deny"),
        ('"new_human_approval_required_for_any_future_execution": True', "new approval required"),
        ('"approval_consumed": False', "approval consumption deny"),
        ('"final_guard_opened": False', "Final Guard deny"),
        ('"oms_submitting": False', "OMS staging deny"),
        ('"lifecycle_unknown": False', "UNKNOWN mutation deny"),
        ('"credentials_persisted": False', "credential persistence deny"),
        ('"broker_write_performed": False', "broker write deny"),
        ('"external_post_authorized": False', "POST authority deny"),
        ('"execution_authority": "NONE"', "execution authority deny"),
        ('"capital_authority": "NONE"', "capital authority deny"),
        ('"reusable_for_real_execution": False', "execution reuse deny"),
        ('"live_trading": "BLOCKED"', "LIVE deny"),
    ):
        _require(attestation, needle, label)

    for needle, label in (
        ("8 · Cold-Start Qualification Attestation", "operator-facing section 8"),
        ("/api/cold-start-qualification-attestation", "dedicated localhost endpoint"),
        ("9 GET PAPER", "nine-GET UI disclosure"),
        ("HEALTH MISSING · EXPECTED", "explicit Health-missing UI"),
        ("QUALIFICATION CANDIDATE · NO EXECUTION", "candidate-only UI status"),
        ("execution_authority = ", "execution authority display"),
        ("new_human_approval_required = ", "fresh approval display"),
        ("Final Guard: UNAVAILABLE", "Final Guard UI deny"),
        ("External PAPER write: DISABLED", "external write UI deny"),
        ("LIVE trading: BLOCKED", "LIVE UI deny"),
    ):
        _require(dashboard, needle, label)

    _require(
        launcher,
        'SERVER="$ROOT/scripts/mac_dashboard_cold_start_attestation.py"',
        "launcher selects attestation wrapper",
    )

    forbidden_calls = (
        "record_operator_approval(",
        ".consume(",
        "authorize_pre_consume(",
        "authorize_pre_io(",
        "stage_for_submission(",
        "stage_external_submission(",
        "mark_unknown(",
        ".reset(",
        "apply_assessment(",
        "sync_from_health(",
        "FinalGuardedCryptoEntryTransport(",
        "FinalGuardedCryptoProtectionTransport(",
    )
    for token in forbidden_calls:
        if token in attestation or token in dashboard:
            raise BoundaryFailure(f"cold-start attestation contains forbidden authority call: {token}")

    print("Mac crypto cold-start qualification attestation boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
