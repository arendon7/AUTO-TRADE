from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).with_name("r6_bind_writer_to_human_execution.py")


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    old = '''        "        # Execution Bridge uses a separate stable Safety store. This store\\n"\n        "        belongs only to the final writer guard: PRE_CONSUME is call 1 and\\n"\n        "        PRE_IO is call 2, where we deliberately flip fail-closed.\\n"\n'''
    new = '''        "        # Execution Bridge uses a separate stable Safety store. This store\\n"\n        "        # belongs only to the final writer guard: PRE_CONSUME is call 1 and\\n"\n        "        # PRE_IO is call 2, where we deliberately flip fail-closed.\\n"\n'''
    if text.count(old) != 1:
        raise SystemExit(f"writer binding generated-comment patch count={text.count(old)}")
    PATH.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("writer human-binding generated comments repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
