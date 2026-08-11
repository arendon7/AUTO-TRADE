from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "AGENTS.md",
    "knowledge/HOME.md",
    "knowledge/00_CANON/SOURCE_OF_TRUTH.md",
    "knowledge/00_CANON/CONTEXTO_RAPIDO.md",
    "knowledge/00_CANON/ESTADO_ACTUAL.md",
    "knowledge/00_CANON/TAREA_ACTIVA.md",
    "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md",
    "knowledge/00_CANON/DEBT_REGISTER.md",
    "knowledge/00_CANON/debt_register.json",
    "knowledge/00_CANON/LEGACY_V028_RECOVERY.md",
    "knowledge/00_CANON/LEGACY_RELEASE_MATRIX.md",
    "knowledge/20_ARQUITECTURA/MAPA_PROYECTO.md",
    "knowledge/20_ARQUITECTURA/CONTRATOS_SEGURIDAD.md",
    "knowledge/30_DECISIONES/ADR-0001-architecture-baseline.md",
    "knowledge/30_DECISIONES/ADR-0006-reconstruct-v028-equivalent.md",
    "knowledge/40_HANDOFF/HANDOFF_ACTUAL.md",
    "knowledge/50_RUNBOOKS/GRAPHIFY_OBSIDIAN.md",
    "knowledge/50_RUNBOOKS/RECOVER_LEGACY_V028.md",
    "scripts/setup_graphify.sh",
    "scripts/refresh_graphify.sh",
]

errors: list[str] = []

for rel in REQUIRED:
    path = ROOT / rel
    if not path.is_file():
        errors.append(f"missing required canonical file: {rel}")

if not errors:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    source_of_truth = (ROOT / "knowledge/00_CANON/SOURCE_OF_TRUTH.md").read_text(
        encoding="utf-8"
    )
    state = (ROOT / "knowledge/00_CANON/ESTADO_ACTUAL.md").read_text(encoding="utf-8")
    task = (ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md").read_text(encoding="utf-8")
    matrix = (ROOT / "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md").read_text(
        encoding="utf-8"
    )
    debt = (ROOT / "knowledge/00_CANON/DEBT_REGISTER.md").read_text(encoding="utf-8")
    debt_json = json.loads(
        (ROOT / "knowledge/00_CANON/debt_register.json").read_text(encoding="utf-8")
    )
    safety = (ROOT / "knowledge/20_ARQUITECTURA/CONTRATOS_SEGURIDAD.md").read_text(
        encoding="utf-8"
    )
    handoff = (ROOT / "knowledge/40_HANDOFF/HANDOFF_ACTUAL.md").read_text(
        encoding="utf-8"
    )
    graphify_runbook = (
        ROOT / "knowledge/50_RUNBOOKS/GRAPHIFY_OBSIDIAN.md"
    ).read_text(encoding="utf-8")
    graphify_setup = (ROOT / "scripts/setup_graphify.sh").read_text(encoding="utf-8")
    graphify_refresh = (ROOT / "scripts/refresh_graphify.sh").read_text(encoding="utf-8")

    checks = [
        (
            "No AI-generated output is itself an executable trading authorization.",
            agents,
            "AI authority contract missing",
        ),
        (
            "v0.28R capability reconstruction",
            agents,
            "v0.28R reconstruction rule missing from AGENTS.md",
        ),
        (
            "DEBT_REGISTER.md",
            agents,
            "mandatory debt-register workflow missing from AGENTS.md",
        ),
        (
            "v0.28R",
            source_of_truth,
            "v0.28R source-of-truth rule missing",
        ),
        (
            "debt_register.json",
            debt,
            "human debt view does not identify machine-readable authority",
        ),
        (
            "P0/P1/P2",
            debt,
            "debt closing severity rule missing",
        ),
        (
            "LIVE TRADING: BLOQUEADO.",
            state,
            "live-trading block missing from canonical state",
        ),
        ("OrderIntent", safety, "OrderIntent safety contract missing"),
        ("Idempotency", safety, "idempotency contract missing"),
        ("FAIL CLOSED", safety, "fail-closed contract missing"),
        ("Negative tests", task, "negative safety tests missing from active task"),
        (
            "dentro del asistente",
            graphify_runbook,
            "Graphify runbook must state semantic builds run inside the assistant",
        ),
        (
            "SOURCE_SHA",
            graphify_runbook,
            "Graphify freshness stamping contract missing",
        ),
        (
            "graphify install",
            graphify_setup,
            "Graphify installer missing official install step",
        ),
        (
            "will not pretend to run it",
            graphify_refresh,
            "Graphify helper must not fake semantic execution from shell",
        ),
    ]

    for needle, haystack, message in checks:
        if needle.lower() not in haystack.lower():
            errors.append(message)

    certified_raw = debt_json.get("certified_tracks")
    if not isinstance(certified_raw, list) or not certified_raw:
        errors.append("machine-readable debt register has no certified_tracks")
        certified_tracks: list[str] = []
    else:
        certified_tracks = []
        for value in certified_raw:
            if not isinstance(value, str) or re.fullmatch(r"R\d+", value) is None:
                errors.append(f"invalid certified track identifier: {value!r}")
                continue
            certified_tracks.append(value)

    # Parse the capability table instead of pinning the checker to whichever
    # reconstruction track happened to be active when the checker was written.
    matrix_rows: dict[str, list[str]] = {}
    for raw_line in matrix.splitlines():
        line = raw_line.strip()
        if not line.startswith("| R"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 5 or re.fullmatch(r"R\d+", cells[0]) is None:
            continue
        matrix_rows.setdefault(cells[0], []).append(cells[3].upper())

    for track in certified_tracks:
        statuses = matrix_rows.get(track, [])
        if not statuses:
            errors.append(f"certified track {track} has no capability rows in matrix")
            continue
        non_pass = [status for status in statuses if status != "PASS"]
        if non_pass:
            errors.append(
                f"certified track {track} contains non-PASS matrix rows: {sorted(set(non_pass))}"
            )

    numeric_tracks = sorted(
        (int(track[1:]) for track in certified_tracks),
    )
    if numeric_tracks:
        expected_prefix = list(range(numeric_tracks[-1] + 1))
        if numeric_tracks != expected_prefix:
            errors.append(
                "certified tracks must be contiguous from R0 through the latest certified track"
            )
        latest = f"R{numeric_tracks[-1]}"
        if latest.lower() not in state.lower():
            errors.append(f"canonical state does not mention latest certified track {latest}")
        if latest.lower() not in handoff.lower():
            errors.append(f"handoff does not mention latest certified track {latest}")

        next_number = numeric_tracks[-1] + 1
        if next_number <= 6:
            next_track = f"R{next_number}"
            if next_track.lower() not in task.lower():
                errors.append(f"active task does not identify next track {next_track}")
            if next_track not in matrix_rows:
                errors.append(f"capability matrix has no rows for next track {next_track}")

    forbidden = {
        "scripts/setup_graphify.sh": ["--platform agents --project"],
        "scripts/refresh_graphify.sh": ["graphify . --update", "graphify . --mode deep"],
    }
    for rel, needles in forbidden.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                errors.append(f"forbidden obsolete Graphify shell command in {rel}: {needle}")

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print("AUTO-TRADE canonical knowledge contract: PASS")
