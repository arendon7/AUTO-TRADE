from pathlib import Path

path = Path(__file__).resolve().parents[1] / "src/autotrade/brokers/alpaca_paper_writer.py"
text = path.read_text(encoding="utf-8")
old = '''        # Crash-safety order is deliberate:\n        # 1) consume permit; a crash now leaves PREPARED + consumed permit, so only\n        #    the SAME attempt may resume and no external request has happened.\n        # 2) persist UNKNOWN; any crash after this point makes all future writer\n        #    calls refuse POST and route to reconciliation only.\n'''
new = '''        # Crash-safety order is deliberate:\n        # 1) consume permit; a crash now leaves PREPARED + consumed permit. The\n        #    automatic writer refuses to resume from that state; explicit recovery\n        #    is required, and no external request has happened yet.\n        # 2) persist UNKNOWN; any crash after this point makes all future writer\n        #    calls refuse POST and route to reconciliation only.\n'''
if text.count(old) != 1:
    raise SystemExit(f"expected exactly one writer crash-safety comment, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
