from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tests/test_r6_paper_canary_permit.py"
text = path.read_text(encoding="utf-8")
old = '''def approval(tmp_path, suffix: str = "001"):\n    current_order = order(suffix)\n'''
new = '''def approval(tmp_path, suffix: str = "001"):\n    tmp_path.mkdir(parents=True, exist_ok=True)\n    current_order = order(suffix)\n'''
if text.count(old) != 1:
    raise SystemExit(f"expected one approval helper marker, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
