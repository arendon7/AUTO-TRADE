from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "INSTALAR_AUTO_TRADE.command"
OPENER = ROOT / "ABRIR_AUTO_TRADE.command"
SAFE_CONSOLE = ROOT / "AUTO_TRADE_MAC.command"
BOOTSTRAP = ROOT / "scripts/mac_bootstrap.sh"
WORKFLOW = ROOT / ".github/workflows/mac-standalone-full.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_full_installer_relocates_verified_bundle_without_quarantine() -> None:
    text = _text(INSTALLER)
    assert 'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"' in text
    source_verify = text.index('verify_standalone_assets "$SOURCE_ROOT"')
    clean_copy = text.index('ditto --norsrc --noqtn "$SOURCE_ROOT" "$STAGE_ROOT"')
    stage_verify = text.index('verify_standalone_assets "$STAGE_ROOT"')
    promote = text.index('mv "$STAGE_ROOT" "$INSTALL_ROOT"')
    assert source_verify < clean_copy < stage_verify < promote
    assert 'if [[ -L "$INSTALL_ROOT" ]]' in text
    assert '"$STAGE_ROOT/.runtime"' in text
    assert '"$STAGE_ROOT/.venv"' in text
    assert '"$STAGE_ROOT/.env"' in text
    assert "*.sqlite3" in text
    assert 'standalone_install_relocated=' in text


def test_downloaded_opener_is_only_a_bridge_to_matching_installed_head() -> None:
    text = _text(OPENER)
    assert 'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"' in text
    assert 'EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"' in text
    assert 'INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"' in text
    assert 'EXPECTED_HEAD" != "$INSTALLED_HEAD' in text
    assert 'bash "$SOURCE_ROOT/INSTALAR_AUTO_TRADE.command"' in text
    assert 'ROOT="$INSTALL_ROOT"' in text
    assert 'scripts/mac_dashboard.py' in text


def test_safe_console_also_prefers_installed_full_copy() -> None:
    text = _text(SAFE_CONSOLE)
    assert 'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"' in text
    assert 'EXPECTED_HEAD="$(read_source_head "$SOURCE_ROOT")"' in text
    assert 'INSTALLED_HEAD="$(read_source_head "$INSTALL_ROOT")"' in text
    assert 'bash "$SOURCE_ROOT/INSTALAR_AUTO_TRADE.command"' in text
    assert 'ROOT="$INSTALL_ROOT"' in text


def test_full_bootstrap_refuses_runtime_execution_from_downloaded_folder() -> None:
    text = _text(BOOTSTRAP)
    assert 'EXPECTED_INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"' in text
    guard = text.index('if [[ "$ROOT" != "$EXPECTED_INSTALL_ROOT" ]]')
    runtime = text.index('PYTHON_BIN="$RUNTIME_ROOT/python/bin/python3"')
    assert guard < runtime
    assert 'FULL/STANDALONE runtime execution is allowed only from:' in text
    assert 'installation remains fail-closed' in text


def test_dual_arch_ci_simulates_finder_quarantine_before_install() -> None:
    text = _text(WORKFLOW)
    assert "Simulate Safari/Finder quarantine" in text
    assert "com.apple.quarantine" in text
    assert 'INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"' in text
    assert "standalone_install_relocated=YES" in text
    assert "installed embedded runtime retained Finder quarantine" in text
    assert "Delete downloaded source and prove installed self-heal" in text
    assert "installed_copy_self_heal=PASS" in text


def test_install_surfaces_remain_write_disabled() -> None:
    for path in (INSTALLER, OPENER, SAFE_CONSOLE, BOOTSTRAP):
        text = _text(path)
        assert "R6_EXTERNAL_PAPER_WRITE=DISABLED" in text or 'R6_EXTERNAL_PAPER_WRITE="DISABLED"' in text
        assert "r6_execute_paper_canary.py" not in text
        assert "alpaca_paper_writer" not in text
        assert "stage_external_submission" not in text
