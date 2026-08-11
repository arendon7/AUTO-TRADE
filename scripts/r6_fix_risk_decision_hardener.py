from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
helper = ROOT / "scripts/r6_bind_risk_decision_fingerprint.py"
text = helper.read_text(encoding="utf-8")
start_marker = 'patch(\n    "scripts/check_r6_canary_coordinator_boundary.py",'
next_marker = '\n\npatch(\n    "scripts/check_r6_execution_bridge_boundary.py",'
start = text.find(start_marker)
end = text.find(next_marker, start)
if start < 0 or end < 0:
    raise RuntimeError("hardener coordinator-checker block markers not found exactly once")
text = text[:start] + text[end + 2 :]
helper.write_text(text, encoding="utf-8")

checker = ROOT / "scripts/check_r6_canary_coordinator_boundary.py"
checker_text = checker.read_text(encoding="utf-8")
old = '''    "deterministic_canary_attempt_id(",
)'''
new = '''    "deterministic_canary_attempt_id(",
    "risk_decision_fingerprint",
    '"risk_decision_fingerprint": risk_decision_fingerprint(decision)',
)'''
if checker_text.count(old) != 1:
    raise RuntimeError("coordinator checker REQUIRED anchor changed")
checker.write_text(checker_text.replace(old, new, 1), encoding="utf-8")
print("one-shot RiskDecision hardener syntax fixed")
