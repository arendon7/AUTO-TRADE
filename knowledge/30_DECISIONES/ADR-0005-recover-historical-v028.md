# ADR-0005 — Recover Historical v0.28 Before Further Reconstruction

Status: Accepted
Date: 2026-08-10

## Context
El repo GitHub `arendon7/AUTO-TRADE` fue creado recientemente y se reconstruyó hasta Foundation v0.3 / Research v0.4 fallback a partir de contexto parcial. Después se recuperaron reportes históricos que prueban que AUTO TRADING IA ya había sido certificado hasta v0.28.0 con una arquitectura significativamente más madura.

Continuar fusionando módulos reconstruidos equivaldría a un downgrade silencioso y podría perder controles ya desarrollados.

## Decision
1. Congelar PR #4 como fallback en draft.
2. Establecer v0.28 histórico como target de recuperación.
3. No inventar el source v0.28 desde reportes.
4. Preservar en GitHub la evidencia histórica y un runbook de importación/recertificación.
5. Cuando el source aparezca, importarlo limpio y comparar contra la certificación histórica antes de reconciliar mejoras nuevas.
6. Graphify + Obsidian pasan a ser parte obligatoria del proceso de recuperación y de cada handoff posterior.

## Evidencia que motiva la decisión
El reporte v0.28 registra:
- 302/302 tests PASS;
- 207 JSON Schemas;
- runtime SIMULATION;
- Event Ledger válido;
- clean ZIP extraction + compile + health PASS;
- External PAPER + broker-side bracket protection sandbox;
- LIVE authority NONE.

Reportes anteriores demuestran además capas de research/validation/HOLDOUT, Trial Ledger, PBO/DSR, portfolio robustness, health/drift, defensive bridge, shadow/forward evidence y PAPER canary.

## Consequences
+ Evita reescribir meses/fases de arquitectura ya certificadas.
+ Mantiene las reconstrucciones actuales como evidencia/fallback útil.
+ Fuerza una recuperación verificable en lugar de una memoria aproximada.
- El desarrollo nuevo queda temporalmente subordinado a recuperar/importar el source histórico.
- Si el ZIP no reaparece, será necesario un ADR específico para reconstrucción equivalente desde evidencia.

## Capital impact
Ninguno. La recuperación no habilita ejecución real.

**LIVE TRADING: BLOQUEADO.**
