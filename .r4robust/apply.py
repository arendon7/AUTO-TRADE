from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src" / "autotrade" / "research" / "allocation_robustness.py"
text = path.read_text(encoding="utf-8")
old = '''def require_robust_allocation(evidence: AllocationRobustnessEvidence) -> None:\n    if not isinstance(evidence, AllocationRobustnessEvidence):\n        raise TypeError("evidence must be AllocationRobustnessEvidence")\n    failed = tuple(scenario.scenario_id for scenario in evidence.scenarios if not scenario.passes_policy)\n    if failed:\n        raise FragileAllocation(f"allocation failed robustness scenarios: {failed}")\n'''
new = '''def require_robust_allocation(\n    dependence: DependenceEvidence,\n    budget_policy: DiversificationBudgetPolicy,\n    strategy_weights: Mapping[str, Decimal],\n    spec: AllocationRobustnessSpec,\n    policy: AllocationRobustnessPolicy,\n) -> AllocationRobustnessEvidence:\n    \"\"\"Recompute the complete robustness universe before granting a PASS.\n\n    A serialized or manually constructed AllocationRobustnessEvidence is audit\n    evidence, never a self-authorizing token. Consumers that need a gate must\n    provide the original self-validating dependence evidence and frozen policy\n    inputs so the scenarios are rebuilt deterministically.\n    \"\"\"\n\n    evidence = evaluate_allocation_robustness(\n        dependence,\n        budget_policy,\n        strategy_weights,\n        spec,\n        policy,\n    )\n    failed = tuple(\n        scenario.scenario_id for scenario in evidence.scenarios if not scenario.passes_policy\n    )\n    if failed:\n        raise FragileAllocation(f"allocation failed robustness scenarios: {failed}")\n    return evidence\n'''
if text.count(old) != 1:
    raise SystemExit(f"robustness gate marker mismatch: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

test_path = ROOT / "tests" / "test_r4_allocation_robustness.py"
test = test_path.read_text(encoding="utf-8")
test = test.replace(
    '''    require_robust_allocation(evidence)\n''',
    '''    recomputed = require_robust_allocation(\n        dependence(now), budget_policy(), weights(), robust_spec(), loose_policy()\n    )\n    assert recomputed.fingerprint == evidence.fingerprint\n''',
    1,
)
test = test.replace(
    '''        require_robust_allocation(evidence)\n''',
    '''        require_robust_allocation(\n            dep, budget_policy(), weights(), robust_spec(), policy\n        )\n''',
    1,
)
old_tail = '''def test_require_robust_allocation_requires_real_evidence():\n    with pytest.raises(TypeError, match="AllocationRobustnessEvidence"):\n        require_robust_allocation(object())  # type: ignore[arg-type]\n'''
new_tail = '''def test_require_robust_allocation_recomputes_from_source_inputs(now):\n    dep = dependence(now)\n    recomputed = require_robust_allocation(\n        dep, budget_policy(), weights(), robust_spec(), loose_policy()\n    )\n    direct = evaluate_allocation_robustness(\n        dep, budget_policy(), weights(), robust_spec(), loose_policy()\n    )\n    assert recomputed.fingerprint == direct.fingerprint\n\n\ndef test_require_robust_allocation_does_not_accept_a_serialized_evidence_object(now):\n    evidence = evaluate_allocation_robustness(\n        dependence(now), budget_policy(), weights(), robust_spec(), loose_policy()\n    )\n    with pytest.raises(TypeError):\n        require_robust_allocation(  # type: ignore[call-arg]\n            evidence,\n            budget_policy(),\n            weights(),\n            robust_spec(),\n            loose_policy(),\n        )\n'''
if test.count(old_tail) != 1:
    raise SystemExit("robustness test tail marker mismatch")
test_path.write_text(test.replace(old_tail, new_tail, 1), encoding="utf-8")

shutil.rmtree(ROOT / ".r4robust", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r4-robust-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R4 recomputing robustness gate hardening applied")
