from __future__ import annotations

import ast
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/autotrade/paper_runtime_readiness_source_snapshot.py"

errors: list[str] = []
if not SOURCE.is_file():
    errors.append("missing W86 atomic W85 source snapshot module")
    text = ""
    tree = ast.parse("")
else:
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SOURCE))

required_markers = (
    'W85_DURABLE_ELIGIBILITY_SNAPSHOT_VERSION = "W86_W85_DURABLE_ELIGIBILITY_SNAPSHOT_V2"',
    "class W85DurableEligibilitySnapshotProof",
    "class W85DurableEligibilitySnapshotReader",
    'f"file:{self._core_path}?mode=ro"',
    'conn.execute("PRAGMA query_only=ON")',
    'conn.execute("PRAGMA query_only")',
    'data_version_before = _data_version(conn)',
    'conn.execute("BEGIN")',
    'conn.execute("COMMIT")',
    'conn.execute("ROLLBACK")',
    'data_version_after_snapshot = _data_version(conn)',
    'post_identity = _read_current_identity(',
    'data_version_after_postcheck = _data_version(conn)',
    'snapshot_identity != post_identity',
    '"durable W85 authority changed during W86 source snapshot"',
    '"sqlite_snapshot_consistent": True',
    '"concurrent_durable_change_detected": False',
    '"sqlite_read_only": True',
    '"paper_execution_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
    "source_v1._require_schema(conn)",
    "source_v1._validate_full_admission_chain(conn, receipt)",
    "source_v1._read_and_validate_lifecycle(conn, receipt)",
    "source_v1._match_final_verification_to_durable(",
    "source_v1._match_supplied_eligibility_to_current_durable(",
)
for marker in required_markers:
    if marker not in text:
        errors.append(f"missing W86 V2 snapshot boundary marker: {marker}")

if 'conn.execute("BEGIN IMMEDIATE")' in text or "BEGIN IMMEDIATE" in text:
    errors.append("W86 V2 source snapshot may not acquire writer-style BEGIN IMMEDIATE")

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
    "W85DurableEligibilitySourceReader(",
)
for marker in forbidden_text:
    if marker in text:
        errors.append(f"forbidden W86 V2 snapshot authority marker: {marker}")

for sql_keyword in (
    "INSERT INTO",
    "UPDATE ",
    "DELETE FROM",
    "CREATE TABLE",
    "DROP TABLE",
    "ALTER TABLE",
    "REPLACE INTO",
    "VACUUM",
    "ATTACH DATABASE",
):
    if re.search(re.escape(sql_keyword), text, flags=re.IGNORECASE):
        errors.append(f"forbidden mutating SQL in W86 V2 source snapshot: {sql_keyword.strip()}")

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
                errors.append(f"forbidden W86 V2 import: {alias.name}")
    elif isinstance(node, ast.ImportFrom):
        module = node.module or ""
        if module.startswith(forbidden_import_prefixes):
            errors.append(f"forbidden W86 V2 import: {module}")

call_names: set[str] = set()
source_v1_calls: set[str] = set()
for node in ast.walk(tree):
    if not isinstance(node, ast.Call):
        continue
    func = node.func
    if isinstance(func, ast.Name):
        call_names.add(func.id)
    elif isinstance(func, ast.Attribute):
        call_names.add(func.attr)
        if isinstance(func.value, ast.Name) and func.value.id == "source_v1":
            source_v1_calls.add(func.attr)

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
    errors.append(f"forbidden W86 V2 call surface: {used_forbidden}")

allowed_v1_helpers = {
    "_validate_supplied_final_objects",
    "_require_schema",
    "_read_registration",
    "_read_receipt",
    "_validate_registration_and_receipt",
    "_validate_full_admission_chain",
    "_read_and_validate_lifecycle",
    "_match_final_verification_to_durable",
    "_current_state",
    "_match_supplied_eligibility_to_current_durable",
}
unexpected_v1_calls = sorted(source_v1_calls - allowed_v1_helpers)
if unexpected_v1_calls:
    errors.append(
        f"W86 V2 may only reuse audited pure V1 validation helpers: {unexpected_v1_calls}"
    )

if "paper_execution is not False" not in text:
    errors.append("W86 V2 proof constructor must reject PAPER execution authority")
if 'capital != "NONE"' not in text or 'live != "BLOCKED"' not in text:
    errors.append("W86 V2 proof constructor must reject capital/LIVE escalation")
if "self.sqlite_snapshot_consistent is not True" not in text:
    errors.append("W86 V2 proof must require an atomic SQLite snapshot")
if "self.concurrent_durable_change_detected is not False" not in text:
    errors.append("W86 V2 proof must reject detected concurrent W85 changes")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print(
    "W86 PAPER RUNTIME READINESS SNAPSHOT BOUNDARY PASS — V2 pins W85 policy/admission/lifecycle reads to a mode=ro + query_only plain read transaction, rejects BEGIN IMMEDIATE and all durable writes, detects concurrent commits through data_version plus post-snapshot admission/lifecycle-head verification, reuses only audited pure V1 validators, and grants no broker/OMS/Safety/OrderIntent/execution/capital/LIVE authority"
)
