from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    ROOT / "src/autotrade/paper_candidate_admission.py",
    ROOT / "src/autotrade/paper_candidate_admission_lifecycle.py",
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
    'ADMISSION_POLICY_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_POLICY_V1"',
    'POLICY_REGISTRATION_VERSION = "W85_PAPER_CANDIDATE_POLICY_REGISTRATION_V1"',
    'ADMISSION_RECEIPT_VERSION = "W85_PAPER_CANDIDATE_ADMISSION_RECEIPT_V1"',
    'LIFECYCLE_EVENT_VERSION = "W85_PAPER_CANDIDATE_LIFECYCLE_EVENT_V1"',
    'ELIGIBILITY_PROJECTION_VERSION = "W85_PAPER_CANDIDATE_ELIGIBILITY_PROJECTION_V1"',
    "PromotionShadowForwardFinalVerification",
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
    "W84_FINAL_VERIFICATION_MISSING",
    "W84_FINALIZATION_STALE",
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
    "def _now_utc()",
)
for marker in required_markers:
    if marker not in combined:
        errors.append(f"missing W85 boundary marker: {marker}")

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
    "W85 PAPER CANDIDATE ADMISSION BOUNDARY PASS — exact W79→W84 evidence may produce only a bounded durable candidate admission; append-only suspension/reinstatement/revocation and process-clock expiry determine current eligibility; candidate eligibility is distinct from PAPER execution authorization; probation descriptors grant no capital authority; no broker/network/OMS/Safety/writer/OrderIntent authority; LIVE blocked"
)
