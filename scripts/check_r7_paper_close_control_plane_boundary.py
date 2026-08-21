from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/autotrade/paper_close_control_plane.py"
FORBIDDEN = (
    "AlpacaPaperCredentials",
    ".post(",
    "submit_once(",
    "api.alpaca.markets",
    "OpenAI",
    "Anthropic",
)
REQUIRED = (
    "CapitalSafetyKernel",
    "safety.evaluate(",
    "decision.risk_reducing is not True",
    "oms.validate_for_external_submission(",
    "class R7RiskReducingOrderManagementSystem",
    "stage_risk_reducing_external_submission",
    "RISK_REDUCING_EXTERNAL_ORDER_HANDOFF_AUTHORIZED",
    "first R7 close requires exactly one broker position and zero open orders",
    "daily_pnl=-broker_portfolio.account.portfolio_value",
    'drawdown=Decimal("1")',
    "source lifecycle is not derived from supplied OMS order",
)


def fail(message: str) -> None:
    print(f"R7 close Safety/OMS boundary: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    if not MODULE.is_file():
        fail("missing R7 close control-plane module")
    text = MODULE.read_text(encoding="utf-8")
    ast.parse(text, filename=str(MODULE))
    for token in FORBIDDEN:
        if token in text:
            fail(f"control plane contains forbidden broker/AI authority: {token}")
    for anchor in REQUIRED:
        if anchor not in text:
            fail(f"missing Safety/OMS/provenance anchor: {anchor}")
    print(
        "R7 close Safety/OMS boundary: PASS — source OMS/lifecycle attribution, conservative broker projection, "
        "real CapitalSafetyKernel risk-reduction decision and OMS-owned risk-reducing handoff; no broker writer authority"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
