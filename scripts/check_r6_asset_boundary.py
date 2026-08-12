from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "src/autotrade/brokers/alpaca_paper_asset.py"
EVIDENCE = ROOT / "src/autotrade/brokers/alpaca_paper_asset_evidence.py"
CLI = ROOT / "scripts/r6_external_paper_asset_preflight.py"
FLAT_CLI = ROOT / "scripts/r6_external_paper_flat_account_preflight.py"
MARKET_CLI = ROOT / "scripts/r6_external_paper_market_preflight.py"
MAC_START = ROOT / "scripts/mac_start.sh"
MAC_CONSOLE = ROOT / "scripts/mac_safe_console.py"
CORE = ROOT / ".github/workflows/core-tests.yml"
R6 = ROOT / ".github/workflows/r6-authority.yml"
SELF_COMMAND = "python scripts/check_r6_asset_boundary.py"
FUNCTIONAL = (
    "tests/test_r6_paper_asset.py",
    "tests/test_r6_asset_evidence.py",
    "tests/test_r6_asset_preflight_cli.py",
)

_FORBIDDEN_IMPORTS = ("openai", "anthropic", "autotrade.research")


def main() -> int:
    errors: list[str] = []
    for path in (ASSET, EVIDENCE, CLI, FLAT_CLI, MARKET_CLI):
        if not path.is_file():
            errors.append(f"missing R6 asset surface: {path.relative_to(ROOT)}")
            continue
        errors.extend(_scan_imports(path))

    if ASSET.is_file():
        source = ASSET.read_text(encoding="utf-8")
        for anchor in (
            'ASSET_PATH_PREFIX = "/v2/assets/"',
            'asset_class != "us_equity"',
            'self.status != "active"',
            'self.tradable is not True',
            '"ipo", "ptp_no_exception", "ptp_with_exception"',
            'Decimal("1") % self.min_trade_increment != 0',
            'method="GET"',
            "self._transport.read(request)",
            "credentials.credential_reference != expected_credential_reference",
            "ALPACA_LIVE_TRADING_HOST",
        ):
            if anchor not in source:
                errors.append(f"asset preflight contract missing: {anchor}")
        for forbidden in (
            'method="POST"',
            "submit_once",
            "stage_external_submission",
            "AlpacaPaperSingleShotWriter",
            "PaperCanaryExecutionBridge",
        ):
            if forbidden in source:
                errors.append(f"asset gateway contains forbidden execution surface: {forbidden}")
        if source.count("self._transport.read(request)") != 1:
            errors.append("asset gateway must have exactly one controlled transport read call site")

    if EVIDENCE.is_file():
        source = EVIDENCE.read_text(encoding="utf-8")
        for anchor in (
            'ARTIFACT_NAME = "asset_attestation.json"',
            '"network_method": "GET"',
            '"credentials_persisted": False',
            '"broker_mutation_performed": False',
            '"execution_authorized": False',
            '"capital_authority": "NONE"',
            '"profitability_claim": False',
            '"live_trading": "BLOCKED"',
        ):
            if anchor not in source:
                errors.append(f"asset evidence safety anchor missing: {anchor}")

    if CLI.is_file():
        source = CLI.read_text(encoding="utf-8")
        ordering = (
            "if not args.allow_paper_asset_read:",
            'os.environ.get(_WRITE_ENV) == "ENABLED"',
            "workspace.account_attestation_path.is_file()",
            "key_id = os.environ.get(_KEY_ENV)",
            "gateway.attest_asset(",
            "PaperAssetEvidenceStore(workspace).write(attestation)",
        )
        positions = [source.find(anchor) for anchor in ordering]
        if any(position < 0 for position in positions) or positions != sorted(positions):
            errors.append("asset CLI explicit safety gates are missing or out of order")
        for forbidden in ("--key-id", "--secret", "submit_once", "POST /v2/orders"):
            if forbidden in source:
                errors.append(f"asset CLI contains forbidden surface: {forbidden}")

    if FLAT_CLI.is_file():
        source = FLAT_CLI.read_text(encoding="utf-8")
        if "PaperAssetEvidenceStore(workspace).read()" not in source:
            errors.append("flat-account preflight must require validated asset evidence first")
        if source.find("PaperAssetEvidenceStore(workspace).read()") > source.find(
            "gateway.attest_flatness("
        ):
            errors.append("asset evidence must be checked before flat-account network GETs")

    if MARKET_CLI.is_file():
        source = MARKET_CLI.read_text(encoding="utf-8")
        if "PaperAssetEvidenceStore(workspace).read()" not in source:
            errors.append("market preflight must require validated asset evidence")
        if "asset.symbol != symbol" not in source:
            errors.append("market preflight must bind market symbol to attested asset symbol")

    for path, anchors in (
        (MAC_START, ("asset-preflight", "account -> asset -> flat account -> market")),
        (MAC_CONSOLE, ("asset-preflight", "--allow-paper-asset-read")),
    ):
        if not path.is_file():
            errors.append(f"missing Mac safe surface: {path.relative_to(ROOT)}")
            continue
        source = path.read_text(encoding="utf-8")
        for anchor in anchors:
            if anchor not in source:
                errors.append(f"Mac safe asset anchor missing in {path.name}: {anchor}")

    for workflow, label in ((CORE, "Core Safety"), (R6, "R6 Authority")):
        if not workflow.is_file() or SELF_COMMAND not in workflow.read_text(encoding="utf-8"):
            errors.append(f"{label}: R6 asset boundary is not wired into permanent CI")
    if R6.is_file():
        source = R6.read_text(encoding="utf-8")
        for test in FUNCTIONAL:
            if test not in source:
                errors.append(f"R6 Authority: asset functional test not wired into CI: {test}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "AUTO-TRADE R6 asset boundary: PASS "
        "(one GET-only PAPER asset read; exact us_equity/tradable whole-share gate; "
        "account-bound sanitized evidence; asset -> flat -> market ordering; no execution authority)"
    )
    return 0


def _scan_imports(path: Path) -> list[str]:
    errors: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
        for module in modules:
            if any(module == prefix or module.startswith(prefix + ".") for prefix in _FORBIDDEN_IMPORTS):
                errors.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: forbidden authority import {module}"
                )
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
