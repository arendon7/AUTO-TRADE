from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
BINDING = ROOT / "scripts/mac_crypto_cold_start_final_guard_binding.py"
DASHBOARD = ROOT / "scripts/mac_dashboard_cold_start_final_guard_binding.py"
LAUNCHER = ROOT / "ABRIR_AUTO_TRADE.command"


class BoundaryError(RuntimeError):
    pass


def _text(path: Path) -> str:
    if not path.is_file():
        raise BoundaryError(f"required file missing: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def main() -> int:
    binding = _text(BINDING)
    dashboard = _text(DASHBOARD)
    launcher = _text(LAUNCHER)
    combined = binding + "\n" + dashboard

    required = (
        "R6_CRYPTO_PAPER_COLD_START_FINAL_GUARD_BINDING_UAT",
        "FIRST_TECHNICAL_CANARY_ONLY",
        '"broker_reads": 15',
        '"health_override_authorized": False',
        '"kill_switch_reset": False',
        '"normal_final_guard_opened": False',
        '"cold_start_final_guard_opened": False',
        '"final_guard_pre_consume_authorized": False',
        '"operator_decision_consumed": False',
        '"oms_submitting": False',
        '"lifecycle_unknown": False',
        '"broker_write_performed": False',
        '"external_post_authorized": False',
        '"execution_authority": "NONE"',
        '"capital_authority": "NONE"',
        '"reusable_for_real_execution": False',
        '"new_execution_approval_required": True',
        '"live_trading": "BLOCKED"',
        "qualification_and_binding_packages_are_distinct",
        "human confirmation does not exactly match binding challenge",
        "no replay permitted",
    )
    for token in required:
        if token not in combined:
            raise BoundaryError(f"cold-start Final Guard binding contract missing: {token}")

    forbidden = (
        "alpaca_paper_crypto_final_guard import",
        "alpaca_paper_crypto_protection_final_guard import",
        "alpaca_paper_crypto_execution_attempt import",
        "alpaca_paper_crypto_protection_execution_attempt import",
        "alpaca_paper_crypto_execution_bridge import",
        "alpaca_paper_crypto_pre_io import",
        "FinalGuardedCryptoEntryTransport",
        "FinalGuardedCryptoProtectionTransport",
        ".consume(",
        "consume_operator",
        "stage_external",
        "OrderStatus.SUBMITTING",
        "CryptoLifecycleStatus.UNKNOWN",
        "HealthRiskMode.NORMAL",
        "synchronize_health_bridge",
        "reset_kill_switch",
        "kill_switch_active = false",
        "broker.submit",
        "requests.post",
        "httpx.post",
        "urllib.request.Request",
    )
    for token in forbidden:
        if token in combined:
            raise BoundaryError(f"forbidden execution/override surface in cold-start binding: {token}")

    if re.search(r"(?m)^\s*(?:export\s+)?R6_EXTERNAL_PAPER_WRITE\s*=\s*[\"']?ENABLED", combined):
        raise BoundaryError("cold-start binding may not enable R6_EXTERNAL_PAPER_WRITE")
    if "mac_dashboard_cold_start_final_guard_binding.py" not in launcher:
        raise BoundaryError("Finder launcher does not prioritize cold-start Final Guard binding dashboard")
    if "mac_dashboard_cold_start_attestation.py" not in launcher:
        raise BoundaryError("Finder launcher lost certified attestation fallback")

    print("R6 crypto cold-start Final Guard binding boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
