from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src" / "autotrade" / "research" / "portfolio_dependence.py"
text = path.read_text(encoding="utf-8")
old = '''        if not self.strategy_fingerprints:\n            raise ValueError("strategy_fingerprints cannot be empty")\n        keys = tuple(key for key, _ in self.strategy_fingerprints)\n        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):\n            raise ValueError("strategy_fingerprints must use unique canonical sorted keys")\n'''
new = '''        if len(self.strategy_fingerprints) < 2:\n            raise InsufficientDependenceEvidence(\n                "dependence evidence requires at least two strategies"\n            )\n        keys = tuple(key for key, _ in self.strategy_fingerprints)\n        for key in keys:\n            _canonical_identity(key, "strategy key")\n            if "@" not in key or key.startswith("@") or key.endswith("@"):\n                raise ValueError("strategy key must be strategy_id@strategy_version")\n        if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):\n            raise ValueError("strategy_fingerprints must use unique canonical sorted keys")\n'''
if text.count(old) != 1:
    raise SystemExit(f"dependence universe marker mismatch: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

shutil.rmtree(ROOT / ".r4depminor", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r4-depminor-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R4 dependence-universe identity hardening applied")
