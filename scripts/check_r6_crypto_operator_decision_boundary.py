from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_operator_decision.py"
PRODUCTION_ROOTS = (ROOT / "src", ROOT / "scripts")

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

    errors.extend(_scan_for_unauthorized_issuance_calls())

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 crypto operator decision boundary: PASS "
        "(HUMAN_OPERATOR only; exact package+attempt binding; <=2m and package-bounded TTL; "
        "tamper-evident ISSUED->CONSUMED; no production approval caller; no credentials/network/writer/POST authority)"
    )
    return 0


def _scan_for_unauthorized_issuance_calls() -> list[str]:
    errors: list[str] = []
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
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node.func)
                if name == "record_operator_approval":
                    errors.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: crypto operator approval issuance is not authorized from production surfaces yet"
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
