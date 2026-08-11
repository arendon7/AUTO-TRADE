from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "AGENTS.md",
    "knowledge/HOME.md",
    "knowledge/00_CANON/SOURCE_OF_TRUTH.md",
    "knowledge/00_CANON/ESTADO_ACTUAL.md",
    "knowledge/00_CANON/TAREA_ACTIVA.md",
    "knowledge/00_CANON/RECONSTRUCTION_V028R_MATRIX.md",
    "knowledge/00_CANON/DEBT_REGISTER.md",
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
    safety = (ROOT / "knowledge/20_ARQUITECTURA/CONTRATOS_SEGURIDAD.md").read_text(
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
            "R2 active",
            matrix,
            "capability matrix does not identify R2 as active",
        ),
        (
            "TD-R2-001",
            debt,
            "R2 lifecycle debt is not explicitly tracked",
        ),
        (
            "P0/P1",
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
