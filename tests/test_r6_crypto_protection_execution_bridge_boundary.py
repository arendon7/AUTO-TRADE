from __future__ import annotations

import ast
from pathlib import Path
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_crypto_protection_execution_bridge_boundary.py"
BRIDGE = ROOT / "src/autotrade/brokers/alpaca_paper_crypto_protection_execution_bridge.py"


def _namespace() -> dict[str, object]:
    return runpy.run_path(str(CHECKER))


def test_protection_execution_bridge_checker_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "crypto protection execution bridge boundary: PASS" in result.stdout
    assert "durable PRE_CONSUME checkpoint" in result.stdout
    assert "no credentials/network/writer" in result.stdout


def test_protection_execution_bridge_checker_forbidden_sets_cover_write_network_and_equity() -> None:
    ns = _namespace()
    forbidden_calls = set(ns["FORBIDDEN_CALLS"])
    forbidden_imports = tuple(ns["FORBIDDEN_IMPORT_FRAGMENTS"])
    network_roots = set(ns["NETWORK_ROOTS"])

    assert {"record_operator_approval", "submit_once", "post", "write", "send"} <= forbidden_calls
    assert "alpaca_paper_crypto_writer" in forbidden_imports
    assert "alpaca_paper_bracket" in forbidden_imports
    assert {"http", "urllib", "socket", "requests", "httpx"} <= network_roots


def test_protection_execution_bridge_checker_requires_checkpoint_human_and_risk_reducing_anchors() -> None:
    ns = _namespace()
    anchors = set(ns["REQUIRED_ANCHORS"])
    assert "checkpoint: CryptoProtectionExecutionAttemptCheckpoint" in anchors
    assert "CryptoProtectionOperatorDecisionContext.from_prepared_package(" in anchors
    assert "operator_registry.consume(" in anchors
    assert "risk_decision.risk_reducing is not True" in anchors
    assert "self._oms.stage_external_submission(" in anchors
    assert '"R6_CRYPTO_PROTECTION_EXECUTION_HANDOFF"' in anchors


def test_protection_execution_bridge_source_has_one_oms_stage_and_no_direct_network_or_credentials() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    ns = _namespace()
    call_name = ns["_call_name"]
    stage_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and call_name(node.func) == "stage_external_submission"
    ]
    assert len(stage_calls) == 1
    for token in (
        "AlpacaPaperCredentials",
        "AlpacaPaperCryptoWriter",
        "HttpsAlpacaPaperCryptoWriteTransport",
        "APCA-API-KEY-ID",
        "APCA-API-SECRET-KEY",
        "/v2/orders",
    ):
        assert token not in source
