from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_final_guard.py"
NETWORK_ROOTS = {"http", "urllib", "socket", "requests", "httpx", "websocket", "websockets"}
FORBIDDEN_IMPORT_FRAGMENTS = (
    "alpaca_paper_crypto_writer",
    "alpaca_paper_writer",
    "alpaca_paper_bracket",
)
FORBIDDEN_TEXT = (
    "AlpacaPaperCredentials",
    "CryptoPaperWriterConfig",
    "AlpacaPaperCryptoWriter",
    "submit_once(",
    "record_operator_approval(",
    "stage_external_submission(",
    "mark_entry_submission_unknown(",
    "R6_EXTERNAL_PAPER_WRITE",
)


def main() -> int:
    errors: list[str] = []
    if not MODULE.is_file():
        errors.append("crypto final guard module is missing")
    else:
        text = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(MODULE))
        for module in _imports(tree):
            if module.split(".", 1)[0] in NETWORK_ROOTS:
                errors.append(f"crypto final guard imports forbidden network stack: {module}")
            if any(fragment in module for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                errors.append(f"crypto final guard imports forbidden writer/equity authority: {module}")
        for token in FORBIDDEN_TEXT:
            if token in text:
                errors.append(f"crypto final guard contains forbidden mutation/write token: {token}")
        required = {
            'PRE_CONSUME = "PRE_CONSUME"': "PRE_CONSUME phase is missing",
            'PRE_IO = "PRE_IO"': "PRE_IO phase is missing",
            "PreparedCryptoPaperCanaryPackage": "prepared package binding is missing",
            "CryptoOperatorDecision": "human operator-decision binding is missing",
            "SQLiteCryptoOperatorDecisionRegistry": "durable operator registry verification is missing",
            "SQLiteCryptoPaperLifecycle": "durable crypto lifecycle verification is missing",
            "OrderStore": "authoritative OMS order store is missing",
            "SafetyStateStore": "authoritative Safety store is missing",
            "PortfolioStore": "authoritative Portfolio store is missing",
            "HealthBridgeControlProvider": "authoritative Health bridge is missing",
            "fresh_account.account_reference != prepared_account.account_reference": "stable account identity comparison is missing",
            "fresh_asset.contract_fingerprint != prepared_asset.contract_fingerprint": "stable asset-contract comparison is missing",
            "fresh_product_profile.contract_fingerprint != prepared_product_profile.contract_fingerprint": "stable product-contract comparison is missing",
            "_FINAL_EVIDENCE_TTL = timedelta(seconds=5)": "five-second final evidence TTL is missing",
            "fresh_flat_account.clean_for_first_canary": "fresh broker-flatness gate is missing",
            "safety.version != package.risk_decision_safety_state_version": "Safety version binding is missing",
            "portfolio.reconciliation_ok": "Portfolio reconciliation gate is missing",
            "portfolio.broker_state_known": "broker-known gate is missing",
            "HealthRiskMode.NORMAL": "Health NORMAL gate is missing",
            "CryptoOperatorDecisionStatus.ISSUED": "PRE_CONSUME operator ISSUED gate is missing",
            "CryptoOperatorDecisionStatus.CONSUMED": "PRE_IO operator CONSUMED gate is missing",
            "OrderStatus.VALIDATED": "PRE_CONSUME OMS VALIDATED gate is missing",
            "OrderStatus.SUBMITTING": "PRE_IO OMS SUBMITTING gate is missing",
            "CryptoLifecycleStatus.ENTRY_PREPARED": "PRE_CONSUME lifecycle gate is missing",
            "CryptoLifecycleStatus.ENTRY_SUBMISSION_UNKNOWN": "PRE_IO UNKNOWN-before-I/O gate is missing",
            "previous_attestation.attestation_hash": "two-phase cryptographic chain is missing",
            "Safety state changed between PRE_CONSUME and PRE_IO": "between-phase Safety race gate is missing",
            "Portfolio State changed between PRE_CONSUME and PRE_IO": "between-phase Portfolio race gate is missing",
            "fresh market evidence changed between PRE_CONSUME and PRE_IO": "between-phase fresh-market race gate is missing",
        }
        for needle, reason in required.items():
            if needle not in text:
                errors.append(f"crypto final guard: {reason}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 crypto final freshness boundary: PASS "
        "(offline PRE_CONSUME->PRE_IO chain; fresh same-account/same-product evidence; "
        "Safety+Portfolio+Health+operator+lifecycle recheck; UNKNOWN-before-I/O required; "
        "no credentials/network/writer/mutation authority)"
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
