from __future__ import annotations

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts/mac_crypto_cold_start_portfolio_bootstrap.py"
DASHBOARD = ROOT / "scripts/mac_dashboard_cold_start.py"
LAUNCHER = ROOT / "ABRIR_AUTO_TRADE.command"


class BoundaryFailure(RuntimeError):
    pass


def _source(path: Path) -> str:
    if not path.is_file():
        raise BoundaryFailure(f"missing required cold-start surface: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def _require(source: str, needle: str, label: str) -> None:
    if needle not in source:
        raise BoundaryFailure(f"cold-start boundary missing {label}: {needle}")


def _forbid_imports(source: str, *, label: str) -> None:
    tree = ast.parse(source)
    forbidden = (
        "alpaca_paper_crypto_final_guard",
        "alpaca_paper_crypto_execution_attempt",
        "alpaca_paper_crypto_execution_bridge",
        "alpaca_paper_crypto_writer",
        "alpaca_paper_crypto_pre_io",
        "alpaca_paper_crypto_operator_decision_issuer",
    )
    for node in ast.walk(tree):
        module = ""
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
        elif isinstance(node, ast.Import):
            module = " ".join(alias.name for alias in node.names)
        if any(token in module for token in forbidden):
            raise BoundaryFailure(f"{label} imports forbidden execution authority: {module}")


def main() -> int:
    bootstrap = _source(BOOTSTRAP)
    dashboard = _source(DASHBOARD)
    launcher = _source(LAUNCHER)

    for source, label in ((bootstrap, "bootstrap"), (dashboard, "dashboard")):
        _forbid_imports(source, label=label)

    for needle, label in (
        ("AlpacaPaperAccountGateway", "fresh PAPER account GET"),
        ("AlpacaPaperFlatAccountGateway", "positions/open-orders GET"),
        ("flat.clean_for_first_canary", "exact flat-account gate"),
        ("SQLitePortfolioStore", "durable Portfolio State store"),
        ("current.version != 1", "version-1 cold-start restriction"),
        ("COMMISSIONING_KILL_REASON", "commissioning kill-switch binding"),
        ("health_count != 0 or bridge_count != 0", "Health absence requirement"),
        ("kill_switch_reset\": False", "kill-switch reset deny"),
        ("credentials_persisted\": False", "credential persistence deny"),
        ("broker_write_performed\": False", "broker-write deny"),
        ("external_post_authorized\": False", "external POST deny"),
        ("approval_consumed\": False", "approval-consumption deny"),
        ("oms_submitting\": False", "OMS staging deny"),
        ("lifecycle_unknown\": False", "UNKNOWN deny"),
        ("capital_authority\": \"NONE\"", "capital authority deny"),
        ("live_trading\": \"BLOCKED\"", "LIVE deny"),
    ):
        _require(bootstrap, needle, label)

    for needle, label in (
        ("7 · Cold-Start Core Bootstrap", "operator-facing section 7"),
        ("/api/cold-start-portfolio-bootstrap", "dedicated localhost endpoint"),
        ("paper_key", "ephemeral key request field"),
        ("paper_secret", "ephemeral secret request field"),
        ("credentials_persisted = ", "credential persistence display"),
        ("PORTFOLIO V1 BOOTSTRAPPED · HEALTH STILL BLOCKED", "correct non-authority status"),
        ("Final Guard: UNAVAILABLE", "Final Guard deny"),
        ("External PAPER write: DISABLED", "broker POST deny"),
        ("LIVE trading: BLOCKED", "LIVE deny"),
    ):
        _require(dashboard, needle, label)

    _require(launcher, 'SERVER="$ROOT/scripts/mac_dashboard_cold_start.py"', "launcher selects cold-start wrapper")
    surfaces = bootstrap + "\n" + dashboard + "\n" + launcher
    if re.search(
        r"(?m)^\s*(?:export\s+)?R6_EXTERNAL_PAPER_WRITE\s*=\s*ENABLED\s*$",
        surfaces,
    ):
        raise BoundaryFailure("cold-start surface must never enable external PAPER writes")

    forbidden_calls = (
        "record_operator_approval(",
        ".consume(",
        "authorize_pre_consume(",
        "authorize_pre_io(",
        "stage_for_submission(",
        "mark_unknown(",
        ".reset(",
        "apply_assessment(",
        "sync_from_health(",
    )
    for token in forbidden_calls:
        if token in bootstrap or token in dashboard:
            raise BoundaryFailure(f"cold-start surface contains forbidden authority call: {token}")

    print("Mac crypto cold-start durable Portfolio bootstrap boundary: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
