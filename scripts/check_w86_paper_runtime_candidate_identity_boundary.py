from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/paper_runtime_candidate_identity.py"

errors: list[str] = []
if not SOURCE.is_file():
    errors.append("missing W86 candidate runtime identity module")
    text = ""
    tree = ast.parse("")
else:
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SOURCE))

required_markers = (
    'PAPER_RUNTIME_CANDIDATE_IDENTITY_VERSION = "W86_PAPER_RUNTIME_CANDIDATE_IDENTITY_V1"',
    "class PaperRuntimeCandidateIdentityProof",
    "def bind_paper_runtime_candidate_identity(",
    "source_proof: W85DurableEligibilitySnapshotProof",
    "final_verification: PaperCandidateAdmissionFinalVerification",
    "w83_resolution: PromotionStrategyVersionResolution",
    "product_economics: FeeProductEconomicsEvidence",
    "source_proof.current_state is not PaperCandidateEligibilityState.ACTIVE",
    "source_proof.final_admission_verification_hash != final_verification.verification_hash",
    "final_verification.w83_resolution_hash != w83_resolution.resolution_hash",
    "final_verification.w83_binding_hash != w83_resolution.binding_evidence_hash",
    "source_proof.selected_trial_fingerprint",
    "source_proof.strategy_spec_hash",
    "source_proof.loaded_runtime_code_hash",
    "source_proof.fee_product_economics_hash",
    "source_proof.intent_fingerprint",
    "w83_resolution.fee_product_economics_hash != product_economics.evidence_hash",
    "w83_resolution.intent_fingerprint != product_economics.intent_fingerprint",
    '"product_id": product_economics.product_id',
    '"asset_class": product_economics.asset_class',
    '"venue": product_economics.venue',
    '"symbol": product_economics.symbol',
    '"side": product_economics.side.value',
    '"base_currency": product_economics.base_currency',
    '"quote_currency": product_economics.quote_currency',
    '"product_identity_verified": True',
    '"strategy_runtime_identity_verified": True',
    '"paper_execution_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
)
for marker in required_markers:
    if marker not in text:
        errors.append(f"missing W86 candidate-identity boundary marker: {marker}")

forbidden_text = (
    "OrderIntent(",
    "SQLiteRuntime(",
    "sqlite3.connect",
    "SQLitePaperCandidateAdmissionRegistry",
    "SQLitePaperCandidateLifecycleRegistry",
    "CapitalSafetyKernel",
    "stage_external_handoff",
    "submit_order",
    "place_order",
    "broker_post",
    "requests.",
    "httpx.",
    "urllib.request",
    "socket.",
    "websockets.",
    "paper-api.alpaca",
    "api.alpaca.markets",
    "APCA_API_KEY",
    "APCA_API_SECRET",
)
for marker in forbidden_text:
    if marker in text:
        errors.append(f"forbidden W86 identity authority marker: {marker}")

forbidden_import_prefixes = (
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.health_bridge",
    "autotrade.connectivity",
    "autotrade.paper_close",
    "autotrade.persistence",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "websockets",
    "sqlite3",
)
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith(forbidden_import_prefixes):
                errors.append(f"forbidden W86 identity import: {alias.name}")
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module.startswith(forbidden_import_prefixes):
            errors.append(f"forbidden W86 identity import: {module}")

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
    "evaluate",
    "stage_external_handoff",
    "register_policy",
    "assess_and_record",
    "append",
}
used_forbidden = sorted(call_names & forbidden_calls)
if used_forbidden:
    errors.append(f"forbidden W86 identity call surface: {used_forbidden}")

if "paper_execution is not False" not in text:
    errors.append("W86 identity proof constructor must reject PAPER execution authority")
if 'capital != "NONE"' not in text or 'live != "BLOCKED"' not in text:
    errors.append("W86 identity proof constructor must reject capital/LIVE escalation")
if "product_economics.symbol" not in text or "product_economics.venue" not in text:
    errors.append("W86 identity must derive symbol/venue from exact W82 product evidence")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print(
    "W86 PAPER RUNTIME CANDIDATE IDENTITY BOUNDARY PASS — runtime product/symbol/side/venue/currency are derived from the exact W82 product evidence hash bound by the exact W83 resolution already bound by final W85 admission verification; ACTIVE atomic W85 source proof required; no caller-selected instrument authority, broker/network/SQLite/OMS/Safety/OrderIntent/execution/capital/LIVE authority"
)
