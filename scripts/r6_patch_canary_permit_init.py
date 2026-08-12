from pathlib import Path

path = Path(__file__).resolve().parents[1] / "src/autotrade/brokers/alpaca_paper_canary_permit.py"
text = path.read_text(encoding="utf-8")
old = '''                conn.execute(
                    """
                    INSERT INTO alpaca_paper_canary_permit_control(
                        singleton, event_sequence, event_head_hash, control_hash
                    ) VALUES (1, 0, ?, ?)
                    """,
                    (_GENESIS_HASH, control_hash),
                )
        finally:
'''
new = '''                conn.execute(
                    """
                    INSERT INTO alpaca_paper_canary_permit_control(
                        singleton, event_sequence, event_head_hash, control_hash
                    ) VALUES (1, 0, ?, ?)
                    """,
                    (_GENESIS_HASH, control_hash),
                )
            conn.commit()
        finally:
'''
if text.count(old) != 1:
    raise SystemExit(f"expected one init marker, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
