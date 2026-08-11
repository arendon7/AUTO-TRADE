from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src" / "autotrade" / "oms.py"
text = path.read_text(encoding="utf-8")
line = '                    "recovered": str(recovered).lower(),\n'
if line in text:
    text = text.replace(line, "", 1)
path.write_text(text, encoding="utf-8")

shutil.rmtree(ROOT / ".r2micro", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r2-micro-patch.yml"
if workflow.exists():
    workflow.unlink()
print("stable order-snapshot payload patch applied")
