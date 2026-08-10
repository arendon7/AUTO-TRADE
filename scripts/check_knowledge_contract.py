from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "AGENTS.md",
    "knowledge/HOME.md",
    "knowledge/00_CANON/CONTEXTO_RAPIDO.md",
    "knowledge/00_CANON/ESTADO_ACTUAL.md",
    "knowledge/00_CANON/TAREA_ACTIVA.md",
    "knowledge/20_ARQUITECTURA/MAPA_PROYECTO.md",
    "knowledge/20_ARQUITECTURA/CONTRATOS_SEGURIDAD.md",
    "knowledge/30_DECISIONES/ADR-0001-architecture-baseline.md",
    "knowledge/40_HANDOFF/HANDOFF_ACTUAL.md",
    "knowledge/50_RUNBOOKS/GRAPHIFY_OBSIDIAN.md",
]

errors: list[str] = []

for rel in REQUIRED:
    path = ROOT / rel
    if not path.is_file():
        errors.append(f"missing required canonical file: {rel}")

if not errors:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    state = (ROOT / "knowledge/00_CANON/ESTADO_ACTUAL.md").read_text(encoding="utf-8")
    safety = (ROOT / "knowledge/20_ARQUITECTURA/CONTRATOS_SEGURIDAD.md").read_text(encoding="utf-8")
    task = (ROOT / "knowledge/00_CANON/TAREA_ACTIVA.md").read_text(encoding="utf-8")

    checks = [
        ("No AI-generated output is itself an executable trading authorization.", agents, "AI authority contract missing"),
        ("LIVE TRADING: BLOQUEADO.", state, "live-trading block missing for Foundation phase"),
        ("OrderIntent", safety, "OrderIntent safety contract missing"),
        ("Idempotency", safety, "idempotency contract missing"),
        ("FAIL CLOSED", safety, "fail-closed contract missing"),
        ("tests negativos", task, "negative safety tests missing from active task"),
    ]

    for needle, haystack, message in checks:
        if needle.lower() not in haystack.lower():
            errors.append(message)

if errors:
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print("AUTO-TRADE canonical knowledge contract: PASS")
