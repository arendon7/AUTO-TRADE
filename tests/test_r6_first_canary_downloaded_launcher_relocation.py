from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE_HEAD = "a" * 40


@pytest.mark.parametrize(
    ("launcher_name", "dashboard_name", "page_name"),
    (
        (
            "ABRIR_PRIMER_CANARY_PREPARAR.command",
            "mac_first_canary_restart_safe_dashboard.py",
            "mac_first_canary.html",
        ),
        (
            "ABRIR_PRIMER_CANARY_REAL_PAPER.command",
            "mac_first_canary_real_paper_dashboard.py",
            "mac_first_canary_real_paper.html",
        ),
    ),
)
def test_downloaded_standalone_launcher_installs_then_executes_from_exact_installed_copy(
    tmp_path: Path,
    launcher_name: str,
    dashboard_name: str,
    page_name: str,
) -> None:
    home = tmp_path / "home"
    source = tmp_path / "Downloads" / "AUTO-TRADE-R6-MAC-FIRST-CANARY-PAPER"
    install = home / "Applications" / "AUTO-TRADE-R6"
    source.mkdir(parents=True)
    home.mkdir(parents=True)

    launcher = source / launcher_name
    launcher.write_text((ROOT / launcher_name).read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    (source / "MAC_STANDALONE_MANIFEST.txt").write_text(
        "AUTO-TRADE R6 MAC FIRST CANARY PAPER/STANDALONE\n"
        f"source_head={SOURCE_HEAD}\n"
        "real_paper_surface=SEPARATE_EXACT_ONE_SHOT\n",
        encoding="utf-8",
    )
    (source / "MAC_BUILD_INFO.txt").write_text(f"source_head={SOURCE_HEAD}\n", encoding="utf-8")

    installer = source / "INSTALAR_AUTO_TRADE.command"
    installer.write_text(
        f"""#!/bin/bash
set -euo pipefail
INSTALL_ROOT="$HOME/Applications/AUTO-TRADE-R6"
mkdir -p "$INSTALL_ROOT/.venv/bin" "$INSTALL_ROOT/scripts" "$INSTALL_ROOT/web"
printf '%s\n' 'source_head={SOURCE_HEAD}' > "$INSTALL_ROOT/MAC_BUILD_INFO.txt"
touch "$INSTALL_ROOT/scripts/mac_first_canary_restart_safe_dashboard.py"
touch "$INSTALL_ROOT/scripts/mac_first_canary_real_paper_dashboard.py"
touch "$INSTALL_ROOT/web/mac_first_canary.html"
touch "$INSTALL_ROOT/web/mac_first_canary_real_paper.html"
cat > "$INSTALL_ROOT/.venv/bin/python" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" > "$HOME/python-invocation.txt"
exit 0
EOF
chmod +x "$INSTALL_ROOT/.venv/bin/python"
COUNT_FILE="$HOME/installer-count.txt"
COUNT=0
[[ -f "$COUNT_FILE" ]] && COUNT="$(cat "$COUNT_FILE")"
printf '%s\n' "$((COUNT + 1))" > "$COUNT_FILE"
""",
        encoding="utf-8",
    )
    installer.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "R6_EXTERNAL_PAPER_WRITE": "DISABLED",
            "APCA_API_KEY_ID": "must-be-unset-by-launcher",
            "APCA_API_SECRET_KEY": "must-be-unset-by-launcher",
        }
    )

    first = subprocess.run(
        ["bash", str(launcher)],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr or first.stdout
    assert (home / "installer-count.txt").read_text(encoding="utf-8").strip() == "1"
    invocation = (home / "python-invocation.txt").read_text(encoding="utf-8").strip()
    assert invocation == str(install / "scripts" / dashboard_name)
    assert not (source / ".venv").exists()
    assert (install / "web" / page_name).is_file()

    second = subprocess.run(
        ["bash", str(launcher)],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr or second.stdout
    assert (home / "installer-count.txt").read_text(encoding="utf-8").strip() == "1"
    assert (home / "python-invocation.txt").read_text(encoding="utf-8").strip() == str(
        install / "scripts" / dashboard_name
    )


def test_downloaded_launcher_rejects_non_dedicated_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    launcher = source / "ABRIR_PRIMER_CANARY_PREPARAR.command"
    launcher.write_text(
        (ROOT / "ABRIR_PRIMER_CANARY_PREPARAR.command").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (source / "MAC_STANDALONE_MANIFEST.txt").write_text(
        f"source_head={SOURCE_HEAD}\nreal_paper_surface=DISABLED\n",
        encoding="utf-8",
    )
    (source / "MAC_BUILD_INFO.txt").write_text(f"source_head={SOURCE_HEAD}\n", encoding="utf-8")

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["R6_EXTERNAL_PAPER_WRITE"] = "DISABLED"
    result = subprocess.run(
        ["bash", str(launcher)],
        cwd=source,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    # The production launcher is a zsh Finder script. Under Linux CI we invoke it
    # with bash only to exercise the pre-install routing. bash rejects zsh's
    # interactive `read -r "?prompt"` after the fail-closed message, so the exact
    # non-zero code is intentionally not part of this cross-shell test.
    assert result.returncode != 0
    assert "no declara el gate dedicado FIRST-CANARY PAPER" in result.stdout
