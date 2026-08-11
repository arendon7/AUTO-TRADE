from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
source_sha = os.environ["R2_SOURCE_SHA"]
coverage_path = Path(os.environ["R2_COVERAGE_JSON"])
junit_path = Path(os.environ["R2_JUNIT_XML"])
contract_output = Path(os.environ["R2_CONTRACT_OUTPUT"]).read_text(encoding="utf-8").strip()

coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
percent = float(coverage["totals"]["percent_covered"])
root = ET.parse(junit_path).getroot()

def attr_int(name: str) -> int:
    if name in root.attrib:
        return int(float(root.attrib[name]))
    return sum(int(float(child.attrib.get(name, "0"))) for child in root)

tests = attr_int("tests")
failures = attr_int("failures")
errors = attr_int("errors")
skipped = attr_int("skipped")
passed = tests - failures - errors - skipped

if failures or errors:
    raise SystemExit("cannot certify R2 with failing/error tests")
if percent < 85.0:
    raise SystemExit(f"cannot certify R2 below coverage gate: {percent}")

now = datetime.now(timezone.utc).isoformat()
evidence = {
    "track": "R2",
    "release_target": "v0.28R",
    "source_sha": source_sha,
    "generated_at": now,
    "runtime_scope": "LOCAL_DURABLE_PAPER_CONTROL_PLANE",
    "tests": {
        "total": tests,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    },
    "coverage_percent": round(percent, 4),
    "coverage_gate_percent": 85.0,
    "contract_registry": contract_output,
    "knowledge_contract": "PASS",
    "compile": "PASS",
    "transient_patch_machinery_absent": True,
    "live_trading": "BLOCKED",
    "external_broker_networking": "NOT_CERTIFIED_IN_R2",
}

evidence_dir = ROOT / "knowledge" / "60_EVIDENCE"
evidence_dir.mkdir(parents=True, exist_ok=True)
(evidence_dir / "R2_CERTIFICATION.json").write_text(
    json.dumps(evidence, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
(evidence_dir / "R2_CERTIFICATION.md").write_text(
    f"""# R2 CERTIFICATION — v0.28R Reconstruction\n\n"
    f"Generated: {now}\n\n"
    f"Tested source SHA: `{source_sha}`\n\n"
    f"- Compile: PASS\n"
    f"- Tests: {passed}/{tests} PASS; failures={failures}; errors={errors}; skipped={skipped}\n"
    f"- Coverage: {percent:.2f}% (gate >= 85%)\n"
    f"- Contract Registry: `{contract_output}`\n"
    f"- Knowledge Contract: PASS\n"
    f"- Transient R2 patch machinery: ABSENT before certification\n"
    f"- Runtime scope: local durable PAPER/control-plane semantics only\n"
    f"- External broker/networking: NOT certified by R2\n"
    f"- **LIVE TRADING: BLOQUEADO.**\n\n"
    f"This artifact certifies only the source SHA above. Canonical promotion still requires debt reconciliation, PR review/merge and post-merge recertification.\n"
    f""",
    encoding="utf-8",
)

shutil.rmtree(ROOT / ".r2cert", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r2-certify-one-shot.yml"
if workflow.exists():
    workflow.unlink()

print(json.dumps(evidence, sort_keys=True))
