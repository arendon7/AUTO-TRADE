# CONTEXTO RÁPIDO — AUTO-TRADE

Estado: **R0–R5 formalmente certified; R6 broker-truth cerrado; W78 execution qualification, W79 promotion governance, W80 durable assessments, W81 non-fee cost continuity y W82 fee-complete deterministic qualification técnicamente certificados. `TD-R7D-001/002` CLOSED. W83 Execution Strategy-Version Binding ACTIVE.**

## Leer primero
1. `knowledge/00_CANON/SOURCE_OF_TRUTH.md`
2. `knowledge/00_CANON/ESTADO_ACTUAL.md`
3. `knowledge/00_CANON/TAREA_ACTIVA.md`
4. `knowledge/00_CANON/debt_register.json`
5. `knowledge/00_CANON/debt_register_r7d_auto_paper.json`
6. `knowledge/30_DECISIONES/ADR-0010-w78-deterministic-paper-execution-model.md`
7. `knowledge/30_DECISIONES/ADR-0011-w79-strategy-promotion-governance.md`
8. `knowledge/30_DECISIONES/ADR-0012-w80-durable-promotion-assessment.md`
9. `knowledge/30_DECISIONES/ADR-0013-w81-execution-cost-continuity.md`
10. `knowledge/30_DECISIONES/ADR-0014-w82-fee-complete-execution-accounting.md`
11. `knowledge/40_HANDOFF/HANDOFF_ACTUAL.md`

## Heads clave
- R6 first canary: `0cbb782015eeed200b9851b53764ac6389c3d9ff`;
- W78: `2924456e33c2cc9e6579301b176267513a90861f`;
- W79 canonical: `b96da018641ddbe6e4bdf8ba9c26642a5174f465`;
- W80 final branch head/base W81: `fb6cc382b4cfc36cb68e1612e7c29b040332ba2e`;
- W81 canonical: `0d042bfb80c9ae0f89de035b6638938f831a3cba`;
- W82 behavioral exact head: `66dbc63941cb2d6552ff1dfadc292dc020e1ecb2`.

## W82 en una frase
W82 prueba fee-complete **deterministic qualification accounting** sin inventar broker fees: Research/W78/W81 identity + product mechanics + fresh documented Alpaca crypto fee schedule deben coincidir para el candidate exacto antes de retirar `FEE_ACCOUNTING_INCOMPLETE`.

Alpaca qualification boundary W82:
- venue `alpaca-paper-model`;
- maker 15 bps / taker 25 bps;
- conservative floor 25 bps;
- `RECEIVED_ASSET_PERCENT`;
- `WORST_CASE`;
- source snapshot con expiry de 30 días.

Certificación behavioral W82:
- dedicated run `32684230790`: **49/49 PASS**;
- Core `32684230698`: **2966/2966 PASS**;
- exact coverage `85.12870855148343%`;
- W78–W82/Research boundaries PASS;
- Debt Register + Canonical Knowledge PASS.

`TD-R7D-001=CLOSED`.
`TD-R7D-002=CLOSED`.

## Lo que sigue abierto
- `TD-R7D-003` partial-fill reservation — P2;
- `EXECUTION_STRATEGY_VERSION_UNBOUND` — objetivo W83;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` — posterior a W83;
- R7B real PAPER close operativo separado.

## W83
Probar que:

`selected candidate strategy identity == frozen deterministic artifact identity == runtime strategy identity used for intent derivation`.

No basta un `strategy_version` string. W83 debe utilizar las identidades ya disponibles (`TrialSpec.fingerprint`, W79 selected trial/strategy identity, W80/W81/W82 candidate chain) y preferir un sidecar provenance receipt ligado a `intent_fingerprint` antes de alterar `OrderIntent`.

Si falta artefacto/runtime canónico o la derivación no es reproducible, el blocker permanece.

## Authority
- PAPER candidate: FALSE;
- capital authority desde capas científicas: NONE;
- broker write desde Research/W78–W83: NO;
- broker-authoritative fee proof desde W82: FALSE;
- realized profitability authorized: FALSE;
- LIVE: BLOCKED.

**LIVE TRADING: BLOQUEADO.**
