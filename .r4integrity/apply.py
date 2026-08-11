from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src" / "autotrade" / "instrument_master.py"
text = path.read_text(encoding="utf-8")
old = '''        record = cls(\n'''
new = '''        supplied_fingerprint = payload.get("fingerprint")\n        if supplied_fingerprint is not None:\n            if (\n                not isinstance(supplied_fingerprint, str)\n                or not _SHA256_RE.fullmatch(supplied_fingerprint)\n            ):\n                raise InstrumentRuleConflict("instrument-rule fingerprint mismatch")\n            raw_payload = dict(payload)\n            raw_payload.pop("fingerprint", None)\n            raw_fingerprint = sha256(\n                _canonical_json(raw_payload).encode("utf-8")\n            ).hexdigest()\n            if supplied_fingerprint != raw_fingerprint:\n                raise InstrumentRuleConflict("instrument-rule fingerprint mismatch")\n\n        record = cls(\n'''
if text.count(old) != 1:
    raise SystemExit(f"unexpected AuthoritativeInstrumentRules construction marker count: {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

shutil.rmtree(ROOT / ".r4integrity", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r4-integrity-one-shot.yml"
if workflow.exists():
    workflow.unlink()
print("R4 pre-parse fingerprint hardening applied")
