from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import mac_crypto_cold_start_final_guard_binding_envelope as envelope


_BASE_PATH = Path(__file__).with_name("mac_dashboard_cold_start_final_guard_binding.py")
_SPEC = importlib.util.spec_from_file_location(
    "autotrade_mac_dashboard_cold_start_final_guard_binding_base",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load certified cold-start Final Guard binding dashboard")
base = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = base
_SPEC.loader.exec_module(base)

# The base dashboard's request handler resolves binding.seal_binding at request
# time. Patch only that local UAT seal adapter; preparation, issuer, broker reads,
# safety state and all execution boundaries remain unchanged.
base.binding.seal_binding = envelope.seal_binding


def main(argv: list[str] | None = None) -> int:
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
