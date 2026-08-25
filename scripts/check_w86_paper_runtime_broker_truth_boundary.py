from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/paper_runtime_broker_truth.py"

errors: list[str] = []
if not SOURCE.is_file():
    errors.append("missing W86 PAPER broker-truth module")
    text = ""
    tree = ast.parse("")
else:
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SOURCE))

required_markers = (
    'PAPER_RUNTIME_BROKER_TRUTH_VERSION = "W86_PAPER_RUNTIME_BROKER_TRUTH_V1"',
    "class PaperRuntimeBrokerTruthPolicy",
    "class PaperRuntimeBrokerTruthProof",
    "def read_and_bind_paper_runtime_broker_truth(",
    "def bind_paper_runtime_broker_truth(",
    "AlpacaPaperAccountGateway(",
    "attest_active_crypto_account(",
    "AlpacaPaperFlatAccountGateway(",
    ".attest_account(",
    ".attest_flatness(",
    'config.base_url != f"https://{ALPACA_PAPER_TRADING_HOST}"',
    'value.asset_class != "crypto"',
    "value.venue != ALPACA_PAPER_CRYPTO_MODEL_VENUE",
    'value.quote_currency != "USD"',
    "value.account_id != account.account_id",
    "value.account_attestation_fingerprint != account.fingerprint",
    "value.credential_reference != account.credential_reference",
    "max_account_age_seconds",
    "max_crypto_status_age_seconds",
    "max_portfolio_age_seconds",
    "max_cross_read_skew_seconds",
    '"account_environment_verified": True',
    '"crypto_entitlement_verified": True',
    '"portfolio_truth_verified": True',
    '"read_only_broker_truth": True',
    '"network_write_performed": False',
    '"paper_runtime_ready": False',
    '"paper_execution_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)
for marker in required_markers:
    if marker not in text:
        errors.append(f"missing W86 broker-truth boundary marker: {marker}")

allowed_broker_modules = {
    "autotrade.brokers.alpaca_paper_gateway",
    "autotrade.brokers.alpaca_paper_crypto_account_status",
    "autotrade.brokers.alpaca_paper_flat_account",
}
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith("autotrade.brokers") and alias.name not in allowed_broker_modules:
                errors.append(f"forbidden W86 broker import: {alias.name}")
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module.startswith("autotrade.brokers") and module not in allowed_broker_modules:
            errors.append(f"forbidden W86 broker import: {module}")

forbidden_text = (
    "OrderIntent(",
    "CapitalSafetyKernel",
    "SQLiteRuntime(",
    "sqlite3.connect",
    "paper_close",
    "paper_writer",
    "crypto_writer",
    "submission",
    "lifecycle",
    "health_bridge",
    "requests.",
    "httpx.",
    "urllib.request",
    "socket.",
    "websockets.",
    "api.alpaca.markets",
    "stage_external_handoff",
    "submit_order",
    "place_order",
    "cancel_order",
    "replace_order",
)
for marker in forbidden_text:
    if marker in text:
        errors.append(f"forbidden W86 broker-truth authority marker: {marker}")

call_names: set[str] = set()
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    func = node.func
    if isinstance(func, ast.Name):
        call_names.add(func.id)
    elif isinstance(func, ast.Attribute):
        call_names.add(func.attr)

forbidden_calls = {
    "post",
    "put",
    "patch",
    "delete",
    "submit_order",
    "place_order",
    "cancel_order",
    "replace_order",
    "stage_external_handoff",
    "reserve",
    "evaluate",
    "append",
}
used_forbidden = sorted(call_names & forbidden_calls)
if used_forbidden:
    errors.append(f"forbidden W86 broker-truth call surface: {used_forbidden}")

required_imports = (
    "AlpacaPaperAccountGateway",
    "AlpacaPaperCryptoAccountStatusAttestation",
    "attest_active_crypto_account",
    "AlpacaPaperFlatAccountGateway",
    "PaperFlatAccountAttestation",
)
for name in required_imports:
    if name not in text:
        errors.append(f"missing certified GET-only W86 broker primitive: {name}")

if "network_write is not False" not in text:
    errors.append("W86 broker truth constructor must reject network-write escalation")
if "paper_runtime_ready is not False" not in text:
    errors.append("W86 broker truth may not mint runtime readiness")
if 'capital != "NONE"' not in text or 'live != "BLOCKED"' not in text:
    errors.append("W86 broker truth must reject capital/LIVE escalation")
if "flat_account.clean_for_first_canary" not in text:
    errors.append("W86 broker truth must preserve observed flat/non-flat state")
if "return bind_paper_runtime_broker_truth(" not in text:
    errors.append("W86 network reader must terminate in immutable broker-truth binding")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print(
    "W86 PAPER BROKER TRUTH BOUNDARY PASS — exact ACTIVE W86 crypto candidate -> explicit PAPER account + crypto entitlement + positions/open-orders GET-only readers -> finite self-hashed broker truth; account/credential/currency/source/freshness/skew are fail-closed; non-flat truth never becomes readiness; no OMS/Safety/Health/writer/submission/network-write/execution/capital/LIVE authority"
)
