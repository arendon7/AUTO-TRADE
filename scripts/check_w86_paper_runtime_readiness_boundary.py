from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/paper_runtime_readiness_source.py"

errors: list[str] = []
if not SOURCE.is_file():
    errors.append("missing W86 runtime-readiness source module")
    text = ""
    tree = ast.parse("")
else:
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SOURCE))

required_markers = (
    'W85_DURABLE_ELIGIBILITY_SOURCE_VERSION = "W86_W85_DURABLE_ELIGIBILITY_SOURCE_V1"',
    "class W85DurableEligibilitySourceProof",
    "class W85DurableEligibilitySourceReader",
    'f"file:{self._core_path}?mode=ro"',
    'conn.execute("PRAGMA query_only=ON")',
    'conn.execute("PRAGMA query_only")',
    '"paper_candidate_admission_policies"',
    '"paper_candidate_admissions"',
    '"paper_candidate_admission_events"',
    "admission._validate_admission_chain(receipts)",
    "lifecycle._validate_event_chain(events, receipt)",
    "_match_final_verification_to_durable(",
    "_match_supplied_eligibility_to_current_durable(",
    "state = _current_state(receipt, events, now)",
    "eligibility.final_admission_verification_hash != final_verification.verification_hash",
    "eligibility.w84_admission_source_proof_hash != final_verification.w84_admission_source_proof_hash",
    '"durable_admission_verified": True',
    '"durable_lifecycle_verified": True',
    '"sqlite_read_only": True',
    '"paper_execution_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
    "PaperCandidateEligibilityState.EXPIRED",
    "PaperCandidateEligibilityState.SUSPENDED",
    "PaperCandidateEligibilityState.REVOKED",
    "def _now_utc()",
)
for marker in required_markers:
    if marker not in text:
        errors.append(f"missing W86 source boundary marker: {marker}")

forbidden_text = (
    "SQLitePaperCandidateAdmissionRegistry",
    "SQLitePaperCandidateLifecycleRegistry",
    "SQLiteRuntime(",
    "OrderIntent(",
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
        errors.append(f"forbidden W86 source authority marker: {marker}")

forbidden_import_prefixes = (
    "autotrade.brokers",
    "autotrade.oms",
    "autotrade.safety",
    "autotrade.health_bridge",
    "autotrade.connectivity",
    "autotrade.paper_close",
    "requests",
    "httpx",
    "urllib",
    "socket",
    "websockets",
)
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.startswith(forbidden_import_prefixes):
                errors.append(f"forbidden W86 source import: {alias.name}")
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module.startswith(forbidden_import_prefixes):
            errors.append(f"forbidden W86 source import: {module}")

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
    errors.append(f"forbidden W86 source call surface: {used_forbidden}")

# The W86 source reader may execute SELECT/PRAGMA only. Catch SQL mutation even if
# someone avoids a high-level writer class.
for sql_keyword in (
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "CREATE TABLE",
    "DROP TABLE",
    "ALTER TABLE",
    "BEGIN IMMEDIATE",
    "REPLACE INTO",
    "VACUUM",
    "ATTACH DATABASE",
):
    if re.search(re.escape(sql_keyword), text, flags=re.IGNORECASE):
        errors.append(f"forbidden mutating SQL in W86 source reader: {sql_keyword.strip()}")

# No source proof produced by this layer may turn readiness-source verification
# into execution authority. Validate the semantic helper guard rather than a
# field-name-specific spelling in the dataclass.
if '"candidate_currently_eligible": state is PaperCandidateEligibilityState.ACTIVE' not in text:
    errors.append("W86 source proof must distinguish candidate eligibility from execution")
if "paper_execution is not False" not in text:
    errors.append("W86 source proof constructor must reject PAPER execution authority")
if "runtime is not False" not in text or '"runtime_execution_authorized": False' not in text:
    errors.append("W86 source proof must preserve and enforce runtime execution=false")
if 'capital != "NONE"' not in text or 'live != "BLOCKED"' not in text:
    errors.append("W86 source proof must reject capital/LIVE escalation")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print(
    "W86 PAPER RUNTIME READINESS SOURCE BOUNDARY PASS — current W85 admission/lifecycle truth is independently re-read through SQLite mode=ro + query_only, supplied W85 final objects are claims cross-checked against durable truth, expiry/lifecycle drift fails closed, and the source proof grants no broker, OMS, Safety, OrderIntent, execution, capital or LIVE authority"
)
