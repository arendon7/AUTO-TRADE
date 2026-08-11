from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src" / "autotrade" / "research" / "multiple_testing.py"
text = path.read_text(encoding="utf-8")
text = text.replace("from statistics import NormalDist, pvariance\n", "from statistics import NormalDist, variance\n")
old = '''    trial_ids = tuple(sorted(expected))
    series = {
'''
new = '''    if len(expected) < 2:
        raise TrialGovernanceError("PBO requires at least two trials")
    trial_ids = tuple(sorted(expected))
    series = {
'''
if old not in text:
    raise SystemExit("PBO trial-id marker missing")
text = text.replace(old, new, 1)
old = '''    all_partitions = set(range(partitions))
    for train_parts in combinations(range(partitions), half):
        complement = tuple(sorted(all_partitions - set(train_parts)))
        if train_parts > complement:
            continue
        train_idx = tuple(i for part in train_parts for i in partition_indices[part])
'''
new = '''    all_partitions = set(range(partitions))
    for train_parts in combinations(range(partitions), half):
        complement = tuple(sorted(all_partitions - set(train_parts)))
        # CSCV treats every choice of S/2 partitions as an in-sample set;
        # its complement is the corresponding out-of-sample set. The swapped
        # orientation is a distinct CSCV combination and must not be dropped.
        train_idx = tuple(i for part in train_parts for i in partition_indices[part])
'''
if old not in text:
    raise SystemExit("PBO combination marker missing")
text = text.replace(old, new, 1)
text = text.replace("    variance = pvariance(sharpes.values())\n    if variance <= 0:\n", "    sharpe_variance = variance(sharpes.values())\n    if sharpe_variance <= 0:\n", 1)
text = text.replace("    expected_max = sqrt(variance) * (\n", "    selected_best = max(sharpes.values())\n    if sharpes[selected_trial_id] != selected_best:\n        raise TrialGovernanceError(\n            \"Deflated Sharpe selected_trial_id must be a maximum-Sharpe trial\"\n        )\n    expected_max = sqrt(sharpe_variance) * (\n", 1)
old_sharpe = '''    if variance == 0:
        if mean > 0:
            return float("inf")
        if mean < 0:
            return float("-inf")
        return 0.0
    return mean / sqrt(variance)
'''
new_sharpe = '''    if variance == 0:
        raise ValueError("Sharpe is undefined for a zero-variance return segment")
    return mean / sqrt(variance)
'''
if old_sharpe not in text:
    raise SystemExit("Sharpe zero-variance marker missing")
text = text.replace(old_sharpe, new_sharpe, 1)
path.write_text(text, encoding="utf-8")

# Update existing PBO combination expectation.
test_path = ROOT / "tests" / "test_r3_multiple_testing.py"
test = test_path.read_text(encoding="utf-8")
test = test.replace("assert evidence.combinations_evaluated == 3", "assert evidence.combinations_evaluated == 6")
# Add deeper statistical guard tests once.
if "test_pbo_counts_all_cscv_orientations" not in test:
    test += '''\n\ndef test_pbo_counts_all_cscv_orientations(tmp_path, now):\n    ledger = setup_campaign(tmp_path, now, ids=("a", "b"))\n    a = [0.02, 0.01, 0.03, 0.015] * 4\n    b = [0.001, -0.002, 0.003, -0.001] * 4\n    evidence = campaign_pbo(\n        ledger, "campaign", {"a": a, "b": b}, partitions=4\n    )\n    # C(4,2)=6; complement-swapped orientations are distinct CSCV splits.\n    assert evidence.combinations_evaluated == 6\n    assert len(evidence.logits) == 6\n\n\ndef test_pbo_zero_variance_segment_is_not_assigned_infinite_sharpe(tmp_path, now):\n    ledger = setup_campaign(tmp_path, now, ids=("a", "b"))\n    with pytest.raises(ValueError, match="zero-variance"):\n        campaign_pbo(\n            ledger,\n            "campaign",\n            {"a": [0.01] * 16, "b": [0.0, 0.01] * 8},\n            partitions=4,\n        )\n\n\ndef test_deflated_sharpe_requires_selected_trial_to_be_family_best(tmp_path, now):\n    ledger = setup_campaign(tmp_path, now, ids=("a", "b", "c"))\n    with pytest.raises(TrialGovernanceError, match="maximum-Sharpe"):\n        campaign_deflated_sharpe(\n            ledger,\n            "campaign",\n            selected_trial_id="a",\n            sample_size=250,\n            skewness=0.0,\n            kurtosis=3.0,\n        )\n'''
test_path.write_text(test, encoding="utf-8")

shutil.rmtree(ROOT / ".r3stats", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r3-stats-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R3 statistical governance patch applied")
