from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_operator_decision.py"
UAT_ISSUER = ROOT / "scripts/r6_issue_crypto_operator_decision_uat.py"
EXECUTION_ISSUER = ROOT / "scripts/mac_crypto_first_canary_approval.py"
PRODUCTION_ROOTS = (ROOT / "src", ROOT / "scripts")
CRYPTO_OPERATOR_MODULE_FRAGMENT = "alpaca_paper_crypto_operator_decision"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_reconciliation",
    "alpaca_paper_writer",
    "alpaca_paper_operator_decision",
    "alpaca_paper_bracket",
)
NETWORK_ROOTS = {"http", "urllib", "socket", "requests", "httpx", "websocket", "websockets"}
FORBIDDEN_TEXT = (
    "AlpacaPaperCredentials",
    "AlpacaPaperCryptoWriter",
    "CryptoPaperWriterConfig",
    "submit_once(",
    "stage_external_submission",
    "ENTRY_SUBMISSION_UNKNOWN",
    "PROTECTION_SUBMISSION_UNKNOWN",
    "R6_EXTERNAL_PAPER_WRITE",
)


def main() -> int:
    errors: list[str] = []
    if not MODULE.is_file():
        errors.append("crypto operator decision module is missing")
    else:
        text = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(MODULE))
        imports = _imports(tree)
        for module in imports:
            if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                errors.append(f"crypto operator decision imports forbidden execution/equity authority: {module}")
            if module.split(".", 1)[0] in NETWORK_ROOTS:
                errors.append(f"crypto operator decision imports forbidden network stack: {module}")
        for token in FORBIDDEN_TEXT:
            if token in text:
                errors.append(f"crypto operator decision contains forbidden authority token: {token}")
        required = {
            '"HUMAN_OPERATOR"': "exact human source marker is missing",
            '"APPROVE_SINGLE_CRYPTO_PAPER_CANARY_ENTRY"': "exact crypto approval action is missing",
            "_MAX_DECISION_TTL = timedelta(minutes=2)": "two-minute maximum decision TTL is missing",
            "PreparedCryptoPaperCanaryPackage": "prepared-package binding is missing",
            "package.network_write_authorized is not False": "non-authorizing package assertion is missing",
            'package.next_action != "OPERATOR_DECISION_REQUIRED"': "operator-decision-required assertion is missing",
            'package.order_status != "VALIDATED"': "OMS VALIDATED package assertion is missing",
            'package.broker_order_type != "limit"': "LIMIT first-canary assertion is missing",
            'package.time_in_force != "ioc"': "IOC first-canary assertion is missing",
            "expires > package_deadline": "approval cannot outlive package deadline",
            'ISSUED = "ISSUED"': "ISSUED state is missing",
            'CONSUMED = "CONSUMED"': "CONSUMED state is missing",
            "alpaca_crypto_operator_decision_events": "durable event table is missing",
            "alpaca_crypto_operator_decision_control": "durable control anchor is missing",
            "BEGIN IMMEDIATE": "serialized durable mutation is missing",
            "preparation_hash": "package/attempt preparation binding is missing",
            "decision_hash": "decision hash is missing",
            "event_hash": "tamper-evident event hash is missing",
            "previous_event_hash": "event-chain previous hash is missing",
            "consumed_attempt_id": "one-shot attempt consumption binding is missing",
            "crypto_operator_confirmation_challenge": "explicit human challenge helper is missing",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"crypto operator decision: {reason}")

    errors.extend(_validate_uat_issuer())
    errors.extend(_validate_execution_issuer())
    errors.extend(_scan_for_unauthorized_issuance_calls())

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 crypto operator decision boundary: PASS "
        "(HUMAN_OPERATOR only; exact package+attempt binding; <=2m and package-bounded TTL; "
        "first-canary execution approval <=90s with >=5s package life remaining; "
        "tamper-evident ISSUED->CONSUMED; isolated UAT issuer plus exact first-canary execution issuer; "
        "approval issuers have no credentials/network/writer/POST/consumption authority)"
    )
    return 0


def _validate_uat_issuer() -> list[str]:
    errors: list[str] = []
    if not UAT_ISSUER.is_file():
        return ["canonical crypto UAT operator issuer is missing"]
    source = UAT_ISSUER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(UAT_ISSUER))
    errors.extend(_validate_issuer_imports(tree, label="crypto UAT issuer"))
    for anchor in (
        "CryptoOperatorDecisionContext.from_dict",
        "crypto_operator_confirmation_challenge(context)",
        "secrets.compare_digest(confirmation, challenge)",
        "SQLiteCryptoOperatorDecisionRegistry",
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
    ):
        if anchor not in source:
            errors.append(f"canonical crypto UAT issuer missing anchor: {anchor}")
    for forbidden in (
        ".consume(",
        "AlpacaPaperCredentials",
        "AlpacaPaperCryptoWriter",
        "FinalGuardedCryptoEntryTransport",
        "stage_external_submission",
        "submit_once(",
        "R6_EXTERNAL_PAPER_WRITE",
    ):
        if forbidden in source:
            errors.append(f"canonical crypto UAT issuer contains forbidden authority: {forbidden}")
    if source.count("record_operator_approval(") != 1:
        errors.append("canonical crypto UAT issuer must contain exactly one issuance call")
    return errors


def _validate_execution_issuer() -> list[str]:
    errors: list[str] = []
    if not EXECUTION_ISSUER.is_file():
        return ["first-canary execution operator issuer is missing"]
    source = EXECUTION_ISSUER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(EXECUTION_ISSUER))
    errors.extend(_validate_issuer_imports(tree, label="first-canary execution issuer"))
    for anchor in (
        "CryptoOperatorDecisionContext.from_dict",
        "crypto_operator_confirmation_challenge(context)",
        "secrets.compare_digest(confirmation, challenge)",
        "SQLiteCryptoOperatorDecisionRegistry",
        "registry.record_operator_approval(",
        'state.status is not CryptoOperatorDecisionStatus.ISSUED',
        'ATTEMPT_PREFIX = "first-canary-"',
        'MAX_APPROVAL_TTL = timedelta(seconds=90)',
        'MIN_REMAINING_PACKAGE_LIFE = timedelta(seconds=5)',
        'EXPECTED_SYMBOL = "BTC/USD"',
        'os.environ.get(WRITE_ENV) == "ENABLED"',
        '"decision_consumed": False',
        '"uat_only": False',
        '"reusable_for_uat": False',
        '"reusable_for_other_attempt": False',
        '"execution_authority": "NONE_UNTIL_PRE_CONSUME_OMS_PRE_IO"',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"capital_authority": "NONE"',
        '"live_trading": "BLOCKED"',
    ):
        if anchor not in source:
            errors.append(f"first-canary execution issuer missing anchor: {anchor}")
    for forbidden in (
        ".consume(",
        "AlpacaPaperCredentials",
        "AlpacaPaperCryptoWriter",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "FinalGuardedCryptoEntryTransport",
        "stage_external_submission",
        "submit_once(",
        "ENTRY_SUBMISSION_UNKNOWN",
        "PROTECTION_SUBMISSION_UNKNOWN",
    ):
        if forbidden in source:
            errors.append(f"first-canary execution issuer contains forbidden authority: {forbidden}")
    if source.count("record_operator_approval(") != 1:
        errors.append("first-canary execution issuer must contain exactly one issuance call")
    return errors


def _validate_issuer_imports(tree: ast.AST, *, label: str) -> list[str]:
    errors: list[str] = []
    for module in _imports(tree):
        if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
            errors.append(f"{label} imports forbidden execution/equity authority: {module}")
        if module.split(".", 1)[0] in NETWORK_ROOTS:
            errors.append(f"{label} imports forbidden network stack: {module}")
    return errors


def _scan_for_unauthorized_issuance_calls() -> list[str]:
    errors: list[str] = []
    allowed_callers = {UAT_ISSUER.resolve(), EXECUTION_ISSUER.resolve()}
    for root in PRODUCTION_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == MODULE.resolve():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, SyntaxError) as exc:
                errors.append(f"cannot inspect production approval surface {path.relative_to(ROOT)}: {exc}")
                continue
            imports = _imports(tree)
            if not any(CRYPTO_OPERATOR_MODULE_FRAGMENT in module for module in imports):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if _call_name(node.func) != "record_operator_approval":
                    continue
                if path.resolve() not in allowed_callers:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: crypto operator approval issuance is restricted to exact audited UAT/execution issuers"
                    )
    return errors


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
