from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "src/autotrade/paper_candidate_admission.py",
    ROOT / "src/autotrade/paper_candidate_admission_source_verification.py",
    ROOT / "src/autotrade/paper_candidate_admission_lifecycle.py",
    ROOT / "src/autotrade/paper_candidate_admission_final_verification.py",
    ROOT / "src/autotrade/paper_candidate_eligibility_final.py",
)

errors: list[str] = []
texts: dict[str, str] = {}
trees: dict[str, ast.AST] = {}
for source in SOURCES:
    if not source.is_file():
        errors.append(f"missing W85 source: {source.name}")
        continue
    text = source.read_text(encoding="utf-8")
    texts[source.name] = text
    trees[source.name] = ast.parse(text, filename=str(source))

combined = "\n".join(texts.values())
required_markers = (
    'ADMISSION_POLICY_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_POLICY_V2"',
    'POLICY_REGISTRATION_VERSION = "W85_PAPER_CANDIDATE_POLICY_REGISTRATION_V2"',
    'ADMISSION_RECEIPT_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_RECEIPT_V2"',
    'ADMISSION_SOURCE_PROOF_VERSION = "W85_W84_ADMISSION_SOURCE_PROOF_V1"',
    'LIFECYCLE_EVENT_VERSION = "W85_PAPER_CANDIDATE_LIFECYCLE_EVENT_V1"',
    'ELIGIBILITY_PROJECTION_VERSION = "W85_PAPER_CANDIDATE_ELIGIBILITY_PROJECTION_V1"',
    'FINAL_ADMISSION_VERIFICATION_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_FINAL_VERIFICATION_V1"',
    'FINAL_ELIGIBILITY_VERSION = "W85_PAPER_CANDIDATE_FINAL_ELIGIBILITY_V1"',
    "PromotionShadowForwardFinalVerification",
    "W84AdmissionSourcePackage",
    "W84AdmissionSourceProof",
    "verify_w84_sources_for_candidate_admission",
    "verify_promotion_shadow_forward_resolution_sources",
    "historical_finalization_timestamp_trusted_for_freshness",
    '"historical_finalization_timestamp_trusted_for_freshness": False',
    "W84_ADMISSION_SOURCE_PROOF_MISSING",
    "W84_DURABLE_SOURCE_STALE",
    "W84_DURABLE_SOURCE_NOT_VERIFIED",
    "source_truth_verified is not True",
    "process_clock_freshness_verified is not True",
    "strategy_version_execution_bound is not True",
    "shadow_forward_promotion_bound is not True",
    '"paper_candidate_authorized": status is PaperCandidateAdmissionStatus.PASS',
    '"paper_execution_authorized": False',
    '"external_execution_authorized": False',
    '"runtime_execution_authorized": False',
    '"capital_authority": "NONE"',
    '"live_trading": "BLOCKED"',
    '"probation_budget_is_execution_authority": False',
    "w84_admission_source_proof_hash",
    "w84_admission_source_verification_hash",
    "w84_admission_source_capture_at",
    "w84_admission_source_verified_at",
    "W84_FINAL_VERIFICATION_MISSING",
    "candidate already has an active W85 admission",
    "admission journal predecessor hash discontinuity",
    "admission decision must occur strictly after frozen policy registration",
    "PaperCandidateLifecycleAction.SUSPEND",
    "PaperCandidateLifecycleAction.REINSTATE",
    "PaperCandidateLifecycleAction.REVOKE",
    "PaperCandidateEligibilityState.EXPIRED",
    "revoked candidate admission is terminal",
    "candidate lifecycle predecessor hash discontinuity",
    "paper_candidate_currently_eligible",
    "admission_source_truth_verified",
    "w84_source_truth_verified",
    "w84_policy_hash",
    "w84_evidence_hash",
    "w84_measurement_runtime_hash",
    "w83_binding_hash",
    "final_admission_verification_hash",
    "lifecycle_registry.list_for_admission",
    "def _state_with_expiry_precedence(",
    "def _now_utc()",
)
for marker in required_markers:
    if marker not in combined:
        errors.append(f"missing W85 boundary marker: {marker}")

admission_text = texts.get("paper_candidate_admission.py", "")
for marker in (
    "w84_source_package: W84AdmissionSourcePackage | None = None",
    "source_proof = verify_w84_sources_for_candidate_admission(",
    "verified_at=now",
    "source_proof.source_age_seconds > policy.max_w84_finalization_age_seconds",
    "source_proof.historical_finalization_timestamp_trusted_for_freshness is not False",
    "PASS admission source verification must use exact admission process clock",
):
    if marker not in admission_text:
        errors.append(f"W85 admission missing durable-source marker: {marker}")
if "w84_finalization.process_verified_at" in admission_text:
    errors.append(
        "W85 admission may not use historical W84 process_verified_at as freshness authority"
    )

source_verification = texts.get(
    "paper_candidate_admission_source_verification.py", ""
)
for marker in (
    "finalization.process_verified_at` is retained as historical provenance",
    "source_capture_at = source.qualification_ended_at + timedelta(",
    "source_age_seconds = int(",
    "historical_finalization_timestamp_trusted_for_freshness\": False",
    "paper_candidate_authorized\": False",
    "paper_execution_authorized\": False",
    "capital_authority\": \"NONE\"",
    "live_trading\": \"BLOCKED\"",
):
    if marker not in source_verification:
        errors.append(f"W85 source reproof missing fail-closed marker: {marker}")

final_eligibility = texts.get("paper_candidate_eligibility_final.py", "")
expiry_marker = "if _utc(observed_at) > _utc(admission_valid_until):"
revocation_marker = "if last.action is PaperCandidateLifecycleAction.REVOKE:"
if expiry_marker not in final_eligibility or revocation_marker not in final_eligibility:
    errors.append("final eligibility must explicitly encode expiry and revocation state")
elif final_eligibility.index(expiry_marker) > final_eligibility.index(revocation_marker):
    errors.append("canonical W85 final eligibility must give expiry precedence over revocation")
if "current_projection(" in final_eligibility:
    errors.append("canonical W85 final eligibility may not consume intermediate lifecycle projection")

final_admission = texts.get("paper_candidate_admission_final_verification.py", "")
for marker in (
    "admission_registry.get(admission_id)",
    "admission_registry.get_policy(durable_receipt.policy_id)",
    "w84_finalization.w83_resolution_hash != w83_resolution.resolution_hash",
    "w84_finalization.w83_binding_hash != w83_resolution.binding_evidence_hash",
    "receipt.w84_finalization_hash != w84_finalization.finalization_hash",
    "receipt.w84_source_verification_hash != w84_finalization.source_verification_hash",
    "receipt.w84_measurement_plan_hash != w84_finalization.measurement_plan_hash",
):
    if marker not in final_admission:
        errors.append(f"final W85 admission verification missing source-authoritative marker: {marker}")

for source_name, text in texts.items():
    forbidden_text = (
        "OrderIntent(",
        "requests.",
        "httpx.",
        "urllib.request",
        "socket.",
        "websockets.",
        "api.alpaca",
        "paper-api.alpaca",
        "APCA_API_KEY",
        "APCA_API_SECRET",
        "submit_order",
        "place_order",
        "broker_post",
    )
    for marker in forbidden_text:
        if marker in text:
            errors.append(f"forbidden W85 authority marker in {source_name}: {marker}")

for source_name, tree in trees.items():
    forbidden_import_prefixes = (
        "autotrade.brokers",
        "autotrade.oms",
        "autotrade.safety",
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
                    errors.append(f"forbidden W85 import in {source_name}: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(forbidden_import_prefixes):
                errors.append(f"forbidden W85 import in {source_name}: {module}")

    call_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
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
    }
    used_forbidden = sorted(call_names & forbidden_calls)
    if used_forbidden:
        errors.append(
            f"forbidden W85 call surface in {source_name}: {used_forbidden}"
        )

source_tree = trees.get("paper_candidate_admission_source_verification.py")
if source_tree is not None:
    source_calls: set[str] = set()
    for node in ast.walk(source_tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            source_calls.add(func.id)
        elif isinstance(func, ast.Attribute):
            source_calls.add(func.attr)
    forbidden_source_mutations = {
        "register_config",
        "append_period",
        "register_policy",
        "append_shadow_record",
        "append",
        "assess_and_record",
    }
    used = sorted(source_calls & forbidden_source_mutations)
    if used:
        errors.append(f"W85 source reproof may not mutate durable authorities: {used}")

if "paper_candidate_authorized is not True" not in texts.get(
    "paper_candidate_admission_lifecycle.py", ""
):
    errors.append("lifecycle must require a PASS admitted candidate source")
if "paper_execution_authorized" not in texts.get(
    "paper_candidate_admission_lifecycle.py", ""
):
    errors.append("lifecycle must explicitly preserve no PAPER execution authority")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print(
    "W85 PAPER CANDIDATE ADMISSION BOUNDARY PASS — V2 admission requires admission-time durable W84 source reproof from existing R5 Shadow/Forward + measurement truth; historical W84 process_verified_at is not freshness authority; durable admission/lifecycle receipts remain non-execution governance artifacts; PAPER candidate eligibility is distinct from PAPER execution authorization; no broker/network/OMS/Safety/writer/OrderIntent authority; capital NONE; LIVE blocked"
)
