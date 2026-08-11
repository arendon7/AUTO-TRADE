from __future__ import annotations

import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
evidence_path = ROOT / "knowledge" / "60_EVIDENCE" / "R2_CERTIFICATION.json"
evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
if evidence["track"] != "R2":
    raise SystemExit("wrong certification track")
if evidence["tests"]["failures"] or evidence["tests"]["errors"]:
    raise SystemExit("R2 evidence contains failing tests")
if float(evidence["coverage_percent"]) < 85.0:
    raise SystemExit("R2 evidence below coverage gate")
if evidence["live_trading"] != "BLOCKED":
    raise SystemExit("R2 evidence must keep live trading blocked")

source_sha = evidence["source_sha"]
passed = evidence["tests"]["passed"]
total = evidence["tests"]["total"]
coverage = float(evidence["coverage_percent"])
contract = evidence["contract_registry"]

# Machine-readable debt registry: no R2 functional debt remains hidden.
debt = {
    "registry_version": 1,
    "certified_tracks": ["R0", "R1", "R2"],
    "items": [
        {
            "id": "TD-R2-001",
            "severity": "P1",
            "track": "R2",
            "status": "CLOSED",
            "area": "OMS lifecycle",
            "resolution": "Partial-fill, cancel, replace, ambiguity and restart lifecycle certified with cancel-first fresh-risk replace semantics.",
            "evidence": ["tests/test_r2_fill_lifecycle.py", "tests/test_r2_partial_cancel.py", "tests/test_r2_replace.py", "tests/test_r2_replace_evidence.py", "knowledge/20_ARQUITECTURA/R2_FAILURE_PATH_REVIEW.md"],
        },
        {
            "id": "TD-R2-002",
            "severity": "P1",
            "track": "R2",
            "status": "CLOSED",
            "area": "Contracts",
            "resolution": "Strict versioned machine-readable registry, explicit runtime serializers, compatibility policy and CI gate.",
            "evidence": ["src/autotrade/contracts/registry.json", "tests/test_contract_registry.py", "tests/test_contract_payloads.py", "scripts/check_contract_registry.py"],
        },
        {
            "id": "TD-R2-003",
            "severity": "P1",
            "track": "R2",
            "status": "CLOSED",
            "area": "Risk policy",
            "resolution": "Exact-boundary/+epsilon capital-limit matrix and reservation-aware exposure tests certified.",
            "evidence": ["tests/test_r2_risk_matrix.py", "tests/test_r2_reservation_risk_matrix.py"],
        },
        {
            "id": "TD-R2-004",
            "severity": "P1",
            "track": "R2",
            "status": "CLOSED",
            "area": "Daily risk state",
            "resolution": "Durable UTC-session loss/drawdown telemetry atomically activates persistent circuit state; recovery requires explicit acknowledgement.",
            "evidence": ["src/autotrade/risk_state.py", "tests/test_r2_risk_telemetry.py"],
        },
        {
            "id": "TD-R2-005",
            "severity": "P2",
            "track": "R2",
            "status": "CLOSED",
            "area": "Control-plane coverage",
            "resolution": f"Critical R2 failure paths have targeted adversarial tests; total branch coverage certified at {coverage:.2f}% with an enforced 85% minimum.",
            "evidence": ["knowledge/60_EVIDENCE/R2_CERTIFICATION.json", "knowledge/20_ARQUITECTURA/R2_FAILURE_PATH_REVIEW.md"],
        },
        {
            "id": "TD-R2-006",
            "severity": "P1",
            "track": "R2",
            "status": "CLOSED",
            "area": "Crash recovery",
            "resolution": "Startup reconciliation repairs terminal OMS/fill commit that crashed before portfolio projection/reservation release, exactly once.",
            "evidence": ["tests/test_r2_crash_projection_recovery.py", "src/autotrade/reconciliation.py"],
        },
        {
            "id": "TD-R2-007",
            "severity": "P1",
            "track": "R2",
            "status": "CLOSED",
            "area": "Portfolio snapshot integrity",
            "resolution": "Safety input validates finite values plus exact gross/net and per-strategy gross consistency against position maps.",
            "evidence": ["tests/test_r2_portfolio_snapshot_integrity.py", "tests/test_r2_risk_matrix.py", "src/autotrade/safety.py"],
        },
        {
            "id": "TD-R2-008",
            "severity": "P2",
            "track": "R2",
            "status": "CLOSED",
            "area": "Fill projection integrity",
            "resolution": "Both durable and in-memory portfolio projection bind fill_id to immutable fill content; conflicting reuse fails closed.",
            "evidence": ["tests/test_r2_projection_integrity.py", "src/autotrade/execution_state.py", "src/autotrade/state.py"],
        },
        {
            "id": "TD-R2-009",
            "severity": "P2",
            "track": "R2",
            "status": "CLOSED",
            "area": "Replace evidence",
            "resolution": "ORDER_REPLACE_REQUESTED/REPLACE_PENDING is durable and retry-safe across crash between cancel and replacement submission.",
            "evidence": ["tests/test_r2_replace_evidence.py", "tests/test_r2_idempotent_events.py", "src/autotrade/oms.py", "src/autotrade/engine.py"],
        },
        {
            "id": "TD-CI-001",
            "severity": "P3",
            "track": "OPS",
            "status": "CLOSED",
            "area": "GitHub Actions runtime",
            "resolution": "Core/knowledge workflows moved to Node-24-generation official actions (checkout@v5, setup-python@v6).",
            "evidence": [".github/workflows/core-tests.yml", ".github/workflows/knowledge-contract.yml"],
        },
        {
            "id": "TD-OPS-001",
            "severity": "P3",
            "track": "OPS",
            "status": "OPEN",
            "area": "Graphify",
            "resolution": "",
            "next_action": "Generate a real semantic/deep graph from a supported coding assistant when that runtime is available; never fake graphify-out artifacts.",
            "evidence": ["knowledge/50_RUNBOOKS/GRAPHIFY_OBSIDIAN.md"],
        },
    ],
}
(ROOT / "knowledge" / "00_CANON" / "debt_register.json").write_text(
    json.dumps(debt, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

(ROOT / "knowledge" / "00_CANON" / "DEBT_REGISTER.md").write_text(
    f"""# DEBT REGISTER — AUTO-TRADE\n\nDate: 2026-08-10\nMachine-readable authority: `knowledge/00_CANON/debt_register.json`\n\n## R2 closure\nR2 has **zero open P0/P1/P2 debt** after gated certification of source `{source_sha}`.\n\nEvidence: {passed}/{total} tests PASS, coverage {coverage:.2f}%, contract registry PASS, Knowledge Contract PASS.\n\nClosed R2 debt: `TD-R2-001` through `TD-R2-009`.\n\n## Remaining explicit debt\n- `TD-OPS-001` — P3 — Graphify semantic/deep graph not generated in this ChatGPT runtime. Integration/runbook/freshness control exists; no fake graph is accepted.\n\n`TD-CI-001` is CLOSED by moving repository workflows to current Node-24-generation official actions.\n\n## Gate\n`scripts/check_debt_register.py` fails CI if a certified track contains any OPEN P0/P1 debt, if IDs are duplicated, or if debt records are malformed.\n\n**LIVE TRADING: BLOQUEADO.**\n""",
    encoding="utf-8",
)

# Permanent machine gate for debt honesty.
(ROOT / "scripts" / "check_debt_register.py").write_text(
    '''from __future__ import annotations\n\nimport json\nfrom pathlib import Path\nimport sys\n\nROOT = Path(__file__).resolve().parents[1]\nPATH = ROOT / "knowledge" / "00_CANON" / "debt_register.json"\nSEVERITIES = {"P0", "P1", "P2", "P3"}\nSTATUSES = {"OPEN", "CLOSED"}\n\n\ndef main() -> int:\n    document = json.loads(PATH.read_text(encoding="utf-8"))\n    certified = set(document.get("certified_tracks", []))\n    items = document.get("items")\n    if not isinstance(items, list):\n        raise ValueError("items must be an array")\n    seen: set[str] = set()\n    blocking: list[str] = []\n    for item in items:\n        debt_id = item.get("id", "")\n        if not debt_id or debt_id in seen:\n            raise ValueError(f"invalid/duplicate debt id: {debt_id}")\n        seen.add(debt_id)\n        severity = item.get("severity")\n        status = item.get("status")\n        track = item.get("track")\n        if severity not in SEVERITIES or status not in STATUSES or not track:\n            raise ValueError(f"malformed debt item: {debt_id}")\n        if status == "OPEN" and not item.get("next_action", "").strip():\n            raise ValueError(f"open debt missing next_action: {debt_id}")\n        if status == "CLOSED" and not item.get("evidence"):\n            raise ValueError(f"closed debt missing evidence: {debt_id}")\n        if track in certified and status == "OPEN" and severity in {"P0", "P1"}:\n            blocking.append(debt_id)\n    if blocking:\n        raise ValueError(f"certified tracks contain blocking debt: {sorted(blocking)}")\n    print(\n        f"AUTO-TRADE debt register: PASS ({len(items)} items; certified={sorted(certified)}; open_blocking=0)"\n    )\n    return 0\n\n\nif __name__ == "__main__":\n    try:\n        raise SystemExit(main())\n    except Exception as exc:\n        print(f"ERROR: debt register: {type(exc).__name__}: {exc}", file=sys.stderr)\n        raise\n''',
    encoding="utf-8",
)

# Track matrix: R2 is now evidenced; R3 becomes active next.
(ROOT / "knowledge" / "00_CANON" / "RECONSTRUCTION_V028R_MATRIX.md").write_text(
    f"""# RECONSTRUCTION v0.28R — CERTIFICATION MATRIX\n\nDate: 2026-08-10\n\n| Track | Scope | Status | Blocking debt | Evidence |\n|---|---|---|---|---|\n| R0 | Durable Foundation / ledger / reservations / reconciliation | PASS | none P0/P1 | Foundation certification in current repo |\n| R1 | Market Data + Strategy DSL + reproducible Research/HOLDOUT | PASS | none P0/P1 | merged R1; 161 tests / 90.34% at R1 close |\n| R2 | Capital Safety + OMS lifecycle + contracts + circuits | **PASS** | **none P0/P1/P2** | `{source_sha}`; {passed}/{total} PASS; {coverage:.2f}% coverage; `R2_CERTIFICATION.json` |\n| R3 | Real read-only data + research governance/trial accounting | NEXT | not yet assessed | pending |\n| R4 | Portfolio robustness + health/drift + defensive bridge | PENDING | not yet assessed | pending |\n| R5 | Stream + synchronized shadow + forward evidence | PENDING | not yet assessed | pending |\n| R6 | External PAPER canary/evidence + broker-side protection | PENDING | not yet assessed | pending |\n\n## Promotion rule\nA track cannot be PASS while its machine Debt Register has OPEN P0/P1. R2 additionally closed all known P2 discovered during implementation.\n\nThe historical v0.28 source remains unavailable; this matrix certifies the reconstruction, not recovery of that package.\n\n**LIVE TRADING: BLOQUEADO.**\n""",
    encoding="utf-8",
)

(ROOT / "knowledge" / "00_CANON" / "ESTADO_ACTUAL.md").write_text(
    f"""# ESTADO ACTUAL\n\nFecha: 2026-08-10\nFase: v0.28R Reconstruction — R2 certified / R3 next\n\n## Certificado en el árbol actual\n- R0 Durable Foundation: PASS.\n- R1 Market Data + Strategy DSL + Research/HOLDOUT: PASS.\n- **R2 Capital Safety + OMS maturity: PASS.**\n\n### Evidencia R2\n- Tested source SHA: `{source_sha}`.\n- Tests: {passed}/{total} PASS.\n- Coverage: {coverage:.2f}% con gate obligatorio >=85%.\n- Machine-readable Contract Registry: PASS.\n- Knowledge Contract: PASS.\n- Debt Register: cero P0/P1/P2 abiertos en R2.\n- Failure-path review: `knowledge/20_ARQUITECTURA/R2_FAILURE_PATH_REVIEW.md`.\n\n### Capacidades R2\n- fill-level exact-once accounting + fingerprint conflict detection;\n- partial fill -> full/cancel recovery;\n- cancel ambiguity -> UNKNOWN/fail-closed;\n- replace durable, cancel-first, retry-safe y con reevaluación completa de riesgo;\n- terminal-fill crash recovery antes de portfolio projection;\n- snapshot de portfolio internamente consistente;\n- límites exactos order/position/strategy/portfolio/net/leverage + reservations;\n- daily-loss/drawdown durable circuit con acknowledgement humano;\n- contratos versionados y validados en CI;\n- Event Ledger e idempotency invariants preservados.\n\n## Deuda visible\nSolo queda `TD-OPS-001` P3: generar Graphify semantic/deep real cuando exista un runtime compatible. No afecta la certificación funcional R2 y no se falsifican artifacts.\n\n## Próximo hito\nR3: real market-data read-only + trial accounting/preregistration + multiple-testing governance + bounded real-data campaign, preservando HOLDOUT y sin broker externo.\n\n## Capital\n**LIVE TRADING: BLOQUEADO.**\nExternal broker/network execution no está certificado por R2.\n""",
    encoding="utf-8",
)

(ROOT / "knowledge" / "00_CANON" / "TAREA_ACTIVA.md").write_text(
    '''# TAREA ACTIVA\n\n## Objetivo\nConstruir y certificar **R3 — Real Data + Research Governance** sobre R0/R1/R2 ya certificados.\n\n## Secuencia\n1. Definir provider contract read-only y deny-by-default para market data externo.\n2. Implementar Binance Spot historical intake GET-only con host fijo/allowlist, límites y timeouts; sin claves privadas ni trading endpoints.\n3. Validar filas, orden temporal, duplicados, gaps, símbolo/timeframe, checksums y provenance antes de crear dataset canónico.\n4. Persistir dataset manifest y checksum; resultados ambiguos/incompletos fallan cerrados, sin imputación silenciosa.\n5. Implementar Trial Ledger / preregistration antes de evaluar parámetros.\n6. Contabilizar todas las pruebas de hipótesis relevantes y multiple-testing evidence; PBO/Deflated Sharpe solo cuando sus precondiciones estadísticas estén satisfechas.\n7. Mantener Final HOLDOUT protegido fuera del tuning y fuera de campañas exploratorias.\n8. Añadir Strategy Tournament/Research Control Center **read-only**, sin autoridad de capital.\n9. Ejecutar una campaña real-data pequeña y acotada para validar pipeline, no para declarar rentabilidad prematuramente.\n10. Crear failure-path review, debt registry entries y gated certification R3.\n\n## Tests negativos obligatorios\n- método no GET / host no permitido / endpoint de trading => rechazo antes de red;\n- timeout/respuesta parcial/malformed JSON => dataset no certificado;\n- fila duplicada/conflictiva/out-of-order/gap => fail closed según contrato;\n- checksum/provenance mismatch => rechazo;\n- trial no preregistrado => no puede entrar a evidencia de promoción;\n- omitir trials fallidos para mejorar PBO/DSR => contract failure;\n- HOLDOUT usado por tuning => hard failure;\n- research UI/ChatGPT output => jamás OrderIntent autorizado.\n\n## Definition of Done\n- datos externos son exclusivamente read-only y reproducibles a dataset canónico;\n- trial accounting completo y auditable;\n- governance estadístico no se aplica cuando faltan precondiciones;\n- HOLDOUT sigue aislado;\n- no existe broker/execution networking;\n- Core Safety, contracts, debt and Knowledge gates green;\n- cero P0/P1 abiertos en R3 antes de PASS;\n- **LIVE TRADING permanece bloqueado.**\n''',
    encoding="utf-8",
)

(ROOT / "knowledge" / "40_HANDOFF" / "HANDOFF_ACTUAL.md").write_text(
    f"""# HANDOFF ACTUAL\n\nFecha: 2026-08-10\nBranch: `reconstruction/r2-capital-oms`\nTrack cerrado: R2\nSiguiente track: R3\n\n## R2 cerrado\nGated evidence: `knowledge/60_EVIDENCE/R2_CERTIFICATION.json`.\n\n- source `{source_sha}` certificado;\n- {passed}/{total} tests PASS;\n- coverage {coverage:.2f}%;\n- contratos/Knowledge Contract PASS;\n- cero R2 P0/P1/P2 abiertos;\n- lifecycle partial/cancel/replace/recovery certificado;\n- durable circuit/daily loss/drawdown certificado;\n- fill projection y PortfolioSnapshot integrity endurecidos;\n- temporary patch/cert tooling autoeliminado antes de cierre.\n\n## Deuda no oculta\n`TD-OPS-001` P3 Graphify semantic graph sigue OPEN por limitación del runtime actual. No afirmar que `graphify-out/` existe hasta generarlo realmente.\n\n## Próximo trabajo exacto\nR3 read-only real market data + Research Governance / Trial Ledger / preregistration / multiple-testing controls + bounded campaign. No broker externo todavía.\n\n## Startup\n`AGENTS.md -> SOURCE_OF_TRUTH -> ESTADO_ACTUAL -> TAREA_ACTIVA -> RECONSTRUCTION_V028R_MATRIX -> debt_register.json -> HANDOFF_ACTUAL -> Graphify si existe y está fresco -> implementación/tests`.\n\n## Capital\n**LIVE TRADING: BLOQUEADO.**\n""",
    encoding="utf-8",
)

(ROOT / "knowledge" / "00_CANON" / "R2_DISCOVERED_DEBT.md").write_text(
    f"""# R2 DISCOVERED DEBT — RECONCILED\n\nDate: 2026-08-10\nStatus: CLOSED / reconciled into `debt_register.json`.\n\n- TD-R2-006 terminal projection crash recovery: CLOSED.\n- TD-R2-007 PortfolioSnapshot internal consistency: CLOSED.\n- TD-R2-008 applied fill fingerprint: CLOSED.\n- TD-R2-009 durable/retry-safe replace evidence: CLOSED.\n\nCertification source: `{source_sha}`; evidence: `knowledge/60_EVIDENCE/R2_CERTIFICATION.json`.\n\nNo discovered R2 P0/P1/P2 remains open.\n\n**LIVE TRADING: BLOQUEADO.**\n""",
    encoding="utf-8",
)

# Update official GitHub actions to current Node-24-generation majors.
for workflow_name in ("core-tests.yml", "knowledge-contract.yml"):
    workflow = ROOT / ".github" / "workflows" / workflow_name
    text = workflow.read_text(encoding="utf-8")
    text = text.replace("actions/checkout@v4", "actions/checkout@v5")
    text = text.replace("actions/setup-python@v5", "actions/setup-python@v6")
    if workflow_name == "core-tests.yml" and "Debt register contract" not in text:
        marker = "      - name: Canonical knowledge contract\n        run: python scripts/check_knowledge_contract.py\n"
        addition = "      - name: Debt register contract\n        run: python scripts/check_debt_register.py\n\n"
        if marker not in text:
            raise SystemExit("core workflow canonical step marker not found")
        text = text.replace(marker, addition + marker, 1)
    workflow.write_text(text, encoding="utf-8")

# Self-clean closure machinery.
shutil.rmtree(ROOT / ".r2close", ignore_errors=True)
workflow = ROOT / ".github" / "workflows" / "r2-close-one-shot.yml"
if workflow.exists():
    workflow.unlink()

print(f"R2 canon finalized from tested source {source_sha}")
