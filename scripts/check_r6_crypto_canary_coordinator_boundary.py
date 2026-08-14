from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_canary_coordinator.py"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "alpaca_paper_crypto_writer",
    "alpaca_paper_crypto_reconciliation",
    "alpaca_paper_operator_decision",
    "connectivity_workspace_post",
    "connectivity_workspace_stage",
)
NETWORK_ROOTS = {"http", "urllib", "socket", "requests", "httpx", "websocket", "websockets"}
FORBIDDEN_TEXT = (
    "AlpacaPaperCredentials",
    "CryptoPaperWriterConfig",
    "AlpacaPaperCryptoWriter",
    "submit_once(",
    "record_operator_approval",
    "stage_external_submission",
    "ENTRY_SUBMISSION_UNKNOWN",
    "PROTECTION_SUBMISSION_UNKNOWN",
    'network_write_authorized": True',
)


def main() -> int:
    errors: list[str] = []
    if not COORDINATOR.is_file():
        errors.append("crypto canary coordinator module is missing")
    else:
        text = COORDINATOR.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(COORDINATOR))
        imports = _imports(tree)
        for module in imports:
            if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                errors.append(f"crypto coordinator imports forbidden execution/operator authority: {module}")
            if module.split(".", 1)[0] in NETWORK_ROOTS:
                errors.append(f"crypto coordinator imports forbidden direct network stack: {module}")
        for token in FORBIDDEN_TEXT:
            if token in text:
                errors.append(f"crypto coordinator contains forbidden authority token: {token}")
        required = {
            "class PreparedCryptoPaperCanaryPackage": "immutable prepared package contract is missing",
            "class CryptoPaperCanaryCoordinator": "crypto coordinator is missing",
            "self._oms.validate_for_external_submission(": "OMS VALIDATED preparation is missing",
            "ProductCapabilities": "ProductCapabilities binding is missing",
            "AssetClass.CRYPTO": "CRYPTO product assertion is missing",
            "MarketHoursModel.CONTINUOUS_24_7": "24/7 product assertion is missing",
            "ProtectionModel.CRYPTO_STOP_LIMIT": "crypto protection model assertion is missing",
            "BrokerOrderType.LIMIT": "LIMIT first-canary restriction is missing",
            "TimeInForce.IOC": "IOC first-canary restriction is missing",
            "FIRST_CANARY_MAX_NOTIONAL": "bounded first-canary notional is missing",
            "FIRST_CANARY_MAX_ACCOUNT_FRACTION": "account-relative notional cap is missing",
            "unresolved_unknown_orders != 0": "UNKNOWN-order clean-state gate is missing",
            "relevant_open_orders != 0": "open-order clean-state gate is missing",
            "confirmed_pair_position_quantity != 0": "flat-pair gate is missing",
            "lifecycle.prepare(binding)": "durable ENTRY_PREPARED lifecycle binding is missing",
            '"OPERATOR_DECISION_REQUIRED"': "operator-decision terminal action is missing",
            '"network_write_authorized": False': "explicit non-authorizing package flag is missing",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"crypto coordinator: {reason}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 crypto canary coordinator boundary: PASS "
        "(offline only; exact CRYPTO ProductCapabilities; bounded LIMIT IOC; OMS VALIDATED; "
        "ENTRY_PREPARED; operator decision required; no credentials/network/writer/POST authority)"
    )
    return 0


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


if __name__ == "__main__":
    raise SystemExit(main())
