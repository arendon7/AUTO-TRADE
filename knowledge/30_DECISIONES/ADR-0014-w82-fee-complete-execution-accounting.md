# ADR-0014 — W82 Fee-Complete Execution Accounting

Fecha: 2026-08-24
Estado: **ACCEPTED / behavioral implementation CERTIFIED; cierre canónico sujeto a recertificación del exact head documental**

## Contexto

W81 cerró la continuidad **non-fee** entre el `ExecutionCostModel` de Research y el modelo determinista W78. Quedaba abierto `TD-R7D-002 / FEE_ACCOUNTING_INCOMPLETE`: Research tenía `fee_bps`, pero ni W78 ni W81 podían reinterpretar ese supuesto como una fee realizada, ni el `Fill` canónico contenía semántica suficiente para representar correctamente todos los productos.

El problema tiene cuatro capas distintas y no deben colapsarse:

1. **Research fee assumption** — hipótesis económica preregistrada dentro del cost model;
2. **simulated qualification accounting** — fee aplicada a los fills deterministas W78;
3. **product fee mechanics** — moneda y efecto contable real del esquema de cobro del producto;
4. **broker fee truth** — actividad realmente publicada por el broker para una orden/cuenta concreta.

Confundir esas capas produce dos tipos de error graves:
- falsos positivos de rentabilidad por usar una fee demasiado barata;
- atribución falsa de una fee realizada a partir de position deltas, rounding o ausencia temporal de activity.

W82 cierra la primera discontinuidad de qualification sin fabricar la cuarta.

## Decisión 1 — no modificar `domain.Fill`

El `Fill` canónico conserva exactamente:

- `fill_id`;
- `order_id`;
- `symbol`;
- `side`;
- `quantity`;
- `price`;
- `occurred_at`.

W82 no añade `fee`, `fee_currency` ni campos broker-specific al `Fill` compartido. La razón es arquitectónica: una ejecución y una comisión no tienen necesariamente el mismo momento de publicación, currency o semántica, y forzarlas dentro del mismo objeto produciría inferencias falsas o acoplamiento de producto.

El boundary permanente `check_w82_fee_accounting_boundary.py` verifica que la forma del `Fill` no cambie como side effect de W82.

## Decisión 2 — base fee accounting separado y hash-bound

`src/autotrade/fee_accounting.py` introduce `FeeAccountingContract` y `FeeAccountingEvidence` para deterministic qualification.

La fee simulada se calcula únicamente sobre el fill determinista que W78 ya produjo:

`fee_quote_equivalent = filled_quantity * modeled_execution_price * research_fee_bps / 10000`

El receipt vincula como mínimo:

- exact Research cost-model hash;
- exact `research_fee_bps`;
- W78 qualification contract;
- scenario matrix;
- W78 measurement/outcome hashes;
- W81 continuity evidence y observation hashes;
- exact intent fingerprint;
- exact market fingerprint;
- product id / asset class / venue;
- fee basis/currency;
- gross notional, fee y net quote-equivalent economics;
- assessment timestamp;
- canonical evidence hash.

Spread y slippage continúan siendo componentes non-fee de W81 y no se vuelven a sumar como fee.

`SIMULATED_MODEL` puede probar deterministic qualification economics. No puede declarar `BROKER_AUTHORITATIVE` ni realized profitability.

## Decisión 3 — product mechanics son una capa independiente

Una fee quote-equivalent no describe por sí sola el estado posterior de Portfolio.

`src/autotrade/fee_product_economics.py` modela la convención de cobro separadamente mediante `FeeProductPolicy` y `FeeProductEconomicsEvidence`.

Se soportan explícitamente:

- `QUOTE_NOTIONAL_PERCENT`;
- `RECEIVED_ASSET_PERCENT`.

Para received-asset crypto:

### BUY

El fill bruto no cambia, pero la cantidad neta acreditada sí:

`net_base_quantity = gross_filled_quantity - fee_in_base_asset`

El cash quote sale por el gross notional; no se inventa un segundo débito USD si la fee se cobró en el activo recibido.

### SELL

La posición base disminuye por el gross sold quantity y la fee se descuenta del quote/fiat recibido:

`net_quote_proceeds = gross_quote_proceeds - fee_in_quote`

La quote-equivalent fee de esta capa debe reconciliar exactamente con la fee W82 base. No se permiten economics favorables escondidos ni double-count.

## Decisión 4 — una policy configurable no es suficiente para definir el broker floor

La primera implementación W82 permitía que un caller construyera una `FeeProductPolicy` con `minimum_fee_bps=5`. El receipt podía ser internamente coherente y, sin embargo, estar económicamente subestimado frente al broker.

Se corrigió esta debilidad introduciendo una tercera evidencia independiente:

`src/autotrade/fee_schedule_attestation.py`

La attestation W82 actual fija el snapshot documental de Alpaca crypto verificado el 2026-08-24:

- source: `https://docs.alpaca.markets/us/docs/crypto-fees`;
- Tier 1 maker: **15 bps**;
- Tier 1 taker: **25 bps**;
- volume assumption: `UNKNOWN_OR_TIER1`;
- liquidity assumption: `WORST_CASE_TAKER`;
- conservative qualification floor: **25 bps**;
- fee charge basis: `CREDITED_ASSET_OR_FIAT`;
- posting semantics: `END_OF_DAY_MAY_BE_DELAYED`.

Mientras no exista evidencia separadamente certificada del 30-day volume tier y del rol de liquidez aplicable, un candidato Alpaca crypto no puede usar un floor inferior a 25 bps para quitar `FEE_ACCOUNTING_INCOMPLETE`.

Un caller puede usar una policy local más estricta, pero no puede usar una policy más barata para reducir el floor documental.

## Decisión 5 — el snapshot documental es versionado y expira

La factory del fee-schedule attestation no acepta un `source_checked_at` suministrado por el caller.

La versión W82 fija:

`ALPACA_CRYPTO_FEE_SOURCE_CHECKED_AT = 2026-08-24T01:55:00+00:00`

La attestation expira a los **30 días**. Después de ese plazo debe re-verificarse la documentación y versionarse el snapshot; no basta con pasar una fecha posterior para hacer parecer fresca una fuente antigua.

Esto evita que la provenance documental sea sólo decorativa.

## Decisión 6 — `PromotionFeeAccountingResolution` V3 es el único cierre del blocker

Un `FeeAccountingEvidence=COMPLETE` aislado no puede remover `FEE_ACCOUNTING_INCOMPLETE`.

La resolución V3 exige simultáneamente:

1. exact W81 candidate-bound resolution;
2. exact W82 base fee accounting evidence;
3. exact product-aware fee economics evidence;
4. exact fresh documented fee-schedule attestation;
5. exact execution intent fingerprint.

El resolver vuelve a validar de forma independiente:

- W82 parent evidence hash;
- fee contract hash;
- W81 continuity hash;
- Research cost-model hash;
- product id;
- asset class;
- venue;
- symbol;
- side;
- market observation time;
- schedule attestation product/venue/symbol/freshness;
- product fee schedule conservatism;
- product mechanics completeness;
- Research fee >= documented broker floor;
- local product minimum >= documented broker floor.

La defensa se repite en el punto que realmente elimina el blocker para que un receipt reconstruido/deserializado no gane autoridad conservando sólo un subconjunto de hashes válidos.

## Decisión 7 — economics imposibles fallan en el final gate

El resolver W82 rechaza además:

- percentage fee > 100%;
- BUY con `net_base_quantity_delta < 0`;
- BUY con quote cash direction favorable/imposible;
- SELL con net base direction favorable/imposible;
- SELL con quote proceeds negativos bajo el receipt presentado;
- received-asset BUY cuya fee currency no sea el base asset.

Estos checks sólo pueden bloquear; nunca relajan un threshold de trading ni crean una conversión automática hacia otro fee model.

## Decisión 8 — broker-observed fee activity permanece separada y fail-closed

`src/autotrade/paper_fee_activity_evidence.py` usa `W82_PAPER_FEE_ACTIVITY_V2`.

Cuando la fee activity todavía no puede probarse autoritativamente:

- status = `PENDING_PUBLICATION`;
- `fee_amount=None`;
- `zero_fee_inferred=false`;
- `broker_authoritative_fee_proven=false`;
- credentials persisted = false;
- broker network performed = false.

La ausencia de activity inmediata nunca significa fee cero.

`build_paper_fee_activity_evidence(...)` permanece deliberadamente deshabilitado y lanza `PaperFeeActivitySourceUnavailable` hasta que exista un adapter broker **read-only**, auditado y con identidad suficiente. Caller-supplied activity ids, amounts o gross-vs-net position deltas no son fee truth.

Por tanto:

`W82 deterministic fee completeness != broker-observed realized fee proof`.

## Decisión 9 — qué cierra exactamente `TD-R7D-002`

`TD-R7D-002` se considera CLOSED porque W82 ya puede demostrar, para un candidato exacto, una cadena de **fee-complete deterministic qualification accounting** que:

- conserva el Research fee assumption exacto;
- usa los fills W78 exactos;
- conserva W81 non-fee continuity;
- modela la fee por product semantics;
- impide que una policy local abarate el floor documental Alpaca;
- usa un snapshot externo versionado y expirable;
- remueve el blocker únicamente mediante un candidate-bound resolution receipt.

Este cierre NO demuestra:

- que una fee concreta fue efectivamente debitada por Alpaca;
- realized fee-complete broker P&L;
- realized profitability;
- que la estrategia tenga positive expectancy futura;
- Auto-Paper readiness;
- capital authority.

Cualquier future realized-P&L claim que dependa de broker fee truth debe esperar una fuente broker read-only explícita o un mecanismo equivalente certificado. Esa frontera no se oculta dentro de W82.

## Decisión 10 — otros blockers permanecen

W82 sólo puede remover:

`FEE_ACCOUNTING_INCOMPLETE`

para el candidato exacto.

Permanecen obligatoriamente:

- `EXECUTION_STRATEGY_VERSION_UNBOUND`;
- `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED`;
- `TD-R7D-003` partial-fill remaining-quantity reservation;
- la obligación operacional independiente R7 PAPER close de PR #49.

W82 fuerza:

- `broker_authoritative_fee_proven=false`;
- `realized_profitability_authorized=false`;
- `strategy_version_execution_bound=false`;
- `shadow_forward_promotion_bound=false`;
- `paper_candidate_authorized=false`;
- `external_execution_authorized=false`;
- `capital_authority=NONE`;
- `live_trading=BLOCKED`.

## Authority boundary

Los módulos W82 no pueden:

- importar brokers/writers;
- usar broker network;
- persistir credenciales;
- mutar SQLite científico para ejecutar;
- importar/usar OMS o Capital Safety como fuente de execution authority;
- crear `OrderIntent`;
- cancelar/reemplazar/subir órdenes;
- habilitar Auto-Paper;
- habilitar LIVE.

## Negative/adversarial evidence

La suite W82 cubre, entre otros:

- Research fee/model/matrix/intent/market drift;
- W81 continuity missing/BLOCKED;
- double-count spread/slippage;
- wrong fee currency/basis/source;
- gross/net economics inconsistentes;
- synthetic fee presentada como broker authoritative;
- missing fee activity reinterpretada como cero;
- fee activity fabricated from caller parameters;
- product/asset/venue/cost-model rebinding con receipt válidamente re-hasheado;
- market-time rebinding;
- symbol/side rebinding;
- >100% fee;
- impossible net direction;
- wrong received-asset fee currency;
- wrong/stale fee schedule attestation;
- tamper de documented 15/25/floor25 schedule;
- caller intentando hacer PASS Research 5 bps con local policy 5 bps;
- candidate/assessment mismatch;
- authority flag escalation.

## Behavioral certification antes del cierre documental

Behavioral head:

`78f3a1a7d454b0c096b0c6f1085942bb1c131452`

Dedicated W82 run `32682423352`:
- **47/47 W82 PASS**;
- fee-accounting boundary PASS;
- promotion fee-resolution boundary PASS;
- W81/W80/W79/W78/Research boundaries PASS.

Core Safety run `32682423322`:
- **2964/2964 PASS**;
- exact coverage `85.13062266745237%` >= 85%;
- `fee_accounting.py` 82%;
- `fee_product_economics.py` 97%;
- `fee_schedule_attestation.py` 91%;
- `paper_fee_activity_evidence.py` 91%;
- `promotion_fee_accounting.py` 78%;
- Contract Registry PASS: 10 contracts, SHA-256 `ddb94afa8916be37d0d956e6c32f775ea41c0fb79f4ea26d2d65dfa286c62785`;
- all inherited R5/R6/R7/W78/W79/W80/W81 boundaries PASS;
- both W82 boundaries PASS;
- Debt Register PASS;
- Canonical Knowledge PASS.

## Siguiente hito

W83 debe atacar primero `EXECUTION_STRATEGY_VERSION_UNBOUND` sin habilitar Auto-Paper: demostrar que la strategy version seleccionada/frozen por Promotion Governance es exactamente la deterministic strategy artifact/version que produciría futuros execution intents. Después podrá cerrarse `SHADOW_FORWARD_PROMOTION_BINDING_REQUIRED` sobre la misma identidad.

**PAPER candidate: FALSE.**
**CAPITAL AUTHORITY: NONE.**
**LIVE TRADING: BLOQUEADO.**
