# ADR-0001 — Architecture Baseline 1.0

Status: Accepted
Date: 2026-08-10

## Context
AUTO-TRADE debe buscar rentabilidad neta sostenible sin permitir que una IA o un único componente tenga autoridad ilimitada sobre capital. La conversación inicial definió una arquitectura por agentes y posteriormente un baseline con Safety Kernel, OMS, reconciliation, ledger y holdout protegido.

## Decision
Adoptar una separación estricta entre:

### Intelligence plane
Market Data, Strategy, Research/Backtest, Portfolio analytics, Monitoring y Orchestrator pueden usar IA cuando aporte valor.

### Control plane
Capital Safety Kernel, OMS, reconciliation, permisos, límites, idempotencia, kill switch y promotion gates serán deterministas, auditables y testeables.

La IA nunca puede bypassar el control plane.

## Research policy
- Backtest realista con costos.
- Holdout protegido.
- Evitar leakage/look-ahead.
- Promover un conjunto de edges robustos, no perseguir una sola curva histórica.

## Operational policy
Fail closed ante incertidumbre material. Estado ambiguo del broker o reconciliación inconsistente bloquea nuevo riesgo.

## Consequences
+ Reduce riesgo catastrófico por errores de modelo/software.
+ Mejora reproducibilidad y auditoría.
+ Permite evolucionar agentes sin reabrir la frontera de seguridad.
- Añade complejidad y latencia operativa.
- Exige tests y reconciliación desde temprano.

## Supersession
Cualquier cambio que permita a IA modificar o omitir límites duros requiere un ADR nuevo y no puede aprobarse solo por performance histórico.