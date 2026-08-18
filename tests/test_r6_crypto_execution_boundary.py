from pathlib import Path
import ast
import runpy
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_r6_crypto_execution_boundary.py"


def test_crypto_execution_authority_checker_passes_repository() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "crypto execution authority boundary: PASS" in result.stdout
    assert "nominal role-bound normal ENTRY/PROTECTION Final Guards" in result.stdout
    assert "isolated first-canary ENTRY guard" in result.stdout
    assert "UNKNOWN-before-I/O" in result.stdout
    assert "GET-only client-id + position reconciliation" in result.stdout


def test_checker_rejects_direct_network_stack_outside_writer(tmp_path) -> None:
    namespace = runpy.run_path(str(CHECKER))
    fake_root = tmp_path / "root"
    broker_dir = fake_root / "src/autotrade/brokers"
    broker_dir.mkdir(parents=True)
    writer = broker_dir / "alpaca_paper_crypto_writer.py"
    reconciliation = broker_dir / "alpaca_paper_crypto_reconciliation.py"
    order = broker_dir / "alpaca_paper_crypto_order.py"
    lifecycle = broker_dir / "alpaca_paper_crypto_lifecycle.py"
    writer.write_text("import http.client\n", encoding="utf-8")
    reconciliation.write_text("import socket\n", encoding="utf-8")
    order.write_text("VALUE = 1\n", encoding="utf-8")
    lifecycle.write_text("VALUE = 1\n", encoding="utf-8")

    imports = namespace["_imports"]
    tree = ast.parse(reconciliation.read_text())
    modules = imports(tree)
    roots = {module.split(".", 1)[0] for module in modules if module}
    assert roots & namespace["NETWORK_IMPORT_ROOTS"] == {"socket"}


def test_checker_forbidden_cross_product_list_covers_equity_execution_authority() -> None:
    namespace = runpy.run_path(str(CHECKER))
    forbidden = set(namespace["FORBIDDEN_CROSS_PRODUCT"])
    assert "alpaca_paper_bracket" in forbidden
    assert "alpaca_paper_writer" in forbidden
    assert "alpaca_paper_final_guard" in forbidden
    assert "connectivity_final_freshness" in forbidden
    assert "connectivity_workspace_post" in forbidden


def test_checker_allows_exactly_three_production_guarded_transport_subclasses() -> None:
    namespace = runpy.run_path(str(CHECKER))
    expected = namespace["EXPECTED_GUARDED_SUBCLASSES"]
    assert expected == {
        ("alpaca_paper_crypto_pre_io.py", "FinalGuardedCryptoEntryTransport"),
        ("alpaca_paper_crypto_pre_io.py", "FinalGuardedCryptoProtectionTransport"),
        (
            "alpaca_paper_crypto_cold_start_pre_io.py",
            "ColdStartFinalGuardedCryptoEntryTransport",
        ),
    }


def test_guarded_subclass_scanner_detects_direct_and_qualified_subclasses() -> None:
    namespace = runpy.run_path(str(CHECKER))
    scanner = namespace["_subclasses_of"]
    tree = ast.parse(
        "\n".join(
            [
                "class Good(GuardedAlpacaPaperCryptoWriteTransport): pass",
                "class AlsoGood(writer.GuardedAlpacaPaperCryptoWriteTransport): pass",
                "class Unrelated(object): pass",
            ]
        )
    )
    assert scanner(tree, "GuardedAlpacaPaperCryptoWriteTransport") == {"Good", "AlsoGood"}
