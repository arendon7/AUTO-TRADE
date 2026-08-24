# ESTADO ACTUAL — AUTO-TRADE

Fecha: 2026-08-24
Estado canónico: **R0–R5 CERTIFIED; R6 FIRST PAPER CANARY BROKER-TRUTH CLOSED; R7 PAPER OPERATIONS ACTIVE; W78/W79/W80/W81/W82 TECHNICALLY CERTIFIED; TD-R7D-002 CLOSED; W83 EXECUTION STRATEGY-VERSION BINDING NEXT.**

## Tracks formales
El machine debt register principal mantiene R0–R5 como tracks certificados contiguos; **R5 sigue siendo el último track formalmente certificado** bajo ese registro.

R6 y W78–W82 tienen certificaciones técnicas específicas y no se reinterpretan como promoción automática del track registry.

## R6 / R7B
El first canary real PAPER alcanzó broker truth y recovery GET-only. PR #49 mantiene separada cualquier obligación operacional del lifecycle real PAPER close, con one-shot writer, Safety/OMS y reconciliation propios.

Nada en W78–W82 modifica ese writer ni convierte Strategy Lab en broker authority.

## W78 — Execution Qualification
W78 certificó ejecución determinista no-network sobre el control plane existente:

`OrderIntent -> Capital Safety -> OMS -> deterministic PAPER broker -> Fill/EventLedger -> Portfolio/Reconciliation`

No predice fills Alpaca futuros ni prueba rentabilidad.

## W79 — Promotion Governance
W79 congela thresholds antes de DEVELOPMENT y candidato antes de FINAL_HOLDOUT, con campaign/strategy id/version/fingerprints explícitos.

Conserva:

`EVIDENCE_QUALIFIED != PAPER_CANDIDATE`.

Strategy Lab permanece GET-only, SQLite `mode=ro/query_only`, sin credenciales, broker POST ni capital authority.

## W80 — Durable Promotion Assessment
W80 persiste assessments científicos append-only/hash-chained y permite lectura durable con verificación independiente read-only. No concede PAPER candidate.

## W81 — Execution Cost Continuity — CERTIFIED
W81 demuestra continuidad conservadora del componente **non-fee** Research -> W78:

`effective_non_fee_impact_bps >= research_half_spread_bps + research_slippage_bps`

Una observación favorable no compensada queda BLOCKED. El resolution receipt sólo puede remover `TOTAL_EXECUTION_COST_CONTINUITY_UNPROVEN` para el exact W80 candidate/assessment y conserva los blockers posteriores.

`TD-R7D-001` está **CLOSED**.

## W82 — Fee-Complete Execution Accounting — CERTIFIED BEHAVIORAL IMPLEMENTATION
PR #54 / `work/w82-fee-complete-execution-accounting`, apilado sobre W81.

Behavioral head certificado antes del cierre documental:

`78f3a1a7d454b0c096b0c6f1085942bb1c131452`

ADR canónico: `knowledge/30_DECISIONES/ADR-0014-w82-fee-complete-execution-accounting.md`.

### Qué demuestra W82
W82 separa cuatro capas que no pueden confundirse:
1. Research fee assumption;
2. deterministic simulated qualification fee;
3. product-specific fee mechanics;
4. broker-observed fee truth.

La fee simulada se liga al exact Research cost-model, W78 qualification/scenario/outcome, W81 continuity, intent, market, product, venue y temporal provenance.

W82 no modifica el `domain.Fill` compartido y no convierte una Research assumption en una fee realizada.

### Product-aware economics
`fee_product_economics.py` distingue explícitamente fee sobre quote notional y fee sobre received asset. Para crypto BUY/SELL, gross fill, position delta y quote cash economics se validan separadamente; double-count de spread/slippage como fee está prohibido.

Fees porcentuales >100%, currency incorrecta y net economics de dirección imposible fallan closed.

### Alpaca crypto documented fee floor
La attestation W82 versionada fija, según la fuente oficial verificada el 2026-08-24:
- Tier 1 maker: **15 bps**;
- Tier 1 taker: **25 bps**;
- conservative qualification floor sin evidencia de tier/rol más favorable: **25 bps**.

Una caller policy puede ser más estricta, pero no puede abaratar ese floor. El snapshot documental expira a los 30 días y debe re-verificarse/versionarse; el caller no puede refrescarlo pasando una fecha nueva.

### Broker fee truth permanece separada
W82 conserva deliberadamente:
- `broker_authoritative_fee_proven=false`;
- fee activity no publicada => `PENDING_PUBLICATION`;
- `fee_amount=None` cuando no existe fuente autoritativa;
- `zero_fee_inferred=false`;
- `realized_profitability_authorized=false`.

La ausencia de activity no significa fee cero. Gross-vs-net position, rounding o residual quantity no se usan como sustituto de broker fee truth.

### Candidate resolution
`PromotionFeeAccountingResolution` V3 es el único punto que puede remover `FEE_ACCOUNTING_INCOMPLETE` para el candidato exacto. Revalida independientemente cost model, W81 resolution, product/asset/venue, symbol/side, market-time, product economics, schedule attestation y floor conservador.

Sólo remueve ese blocker. Mantiene:
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- PAPER candidate FALSE;
- external execution FALSE;
- capital authority NONE;
- LIVE BLOCKED.

### Evidencia behavioral exact-head
Dedicated W82 run `32682423352`:
- **47/47 W82 PASS**;
- fee-accounting boundary PASS;
- promotion fee-resolution boundary PASS;
- W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32682423322`:
- **2964/2964 PASS**;
- exact coverage `85.13062266745237%` >= 85%;
- Contract Registry: 10 PASS, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- all inherited R5/R6/R7/W78/W79/W80/W81 boundaries PASS;
- both W82 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

La recertificación del exact head documental final sigue siendo obligatoria antes de declarar cierre integral de PR #54.

## Debt R7D
- `TD-R7D-001` **CLOSED** — non-fee Research -> W78 execution-cost continuity;
- `TD-R7D-002` **CLOSED** — fee-complete deterministic qualification accounting con product semantics + conservative documented fee floor;
- `TD-R7D-003` **OPEN P2** — safe remaining-quantity reservation after partial fills.

El cierre de `TD-R7D-002` **no** equivale a broker-observed fee proof ni realized profitability.

Blockers de promoción aún abiertos:
- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`.

## Próximo hito — W83
W83 debe cerrar primero `EXECUTION_STRATEGY_VERSION_UNBOUND` sin habilitar Auto-Paper.

El objetivo es demostrar que la strategy id/version seleccionada y congelada por Promotion Governance corresponde exactamente al deterministic strategy artifact/runtime definition que produciría futuros execution intents. Debe existir binding reproducible y hash-bound entre candidate assessment, strategy artifact/version, runtime derivation y resulting intent identity, sin broker/network/writer/Safety/OMS authority en la capa científica.

Sólo después de ese binding podrá abordarse `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` sobre la misma identidad.

## Authority
Research/W78/W79/W80/W81/W82/W83 científico no tiene camino directo al writer.

Cualquier futura automatización continúa obligada a:

`Strategy Runtime -> OrderIntent -> Portfolio -> Capital Safety -> OMS -> one-shot PAPER writer -> reconciliation -> Health`

IA/model output no puede saltarse esa cadena.

## No-claims
- W82 deterministic fee completeness != broker-observed realized fee;
- fee-complete qualification != realized profitability;
- simulation != future Alpaca fill;
- PAPER qualification != LIVE qualification;
- evidence qualification != capital authority.

**PAPER CANDIDATE: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
