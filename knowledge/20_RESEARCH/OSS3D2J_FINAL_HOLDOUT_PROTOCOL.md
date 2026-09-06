# OSS-3D2J — Preregistered Predictive FINAL_HOLDOUT Protocol

## Status

Research-only protocol frontier. Base exacta certificada:

- branch: `research/oss3d2i-development-winner-selection-seal`
- head: `d824b0c67cbfab9c5dca7cabac66d709f1472d72`
- upstream D2I Dedicated: SUCCESS
- upstream Knowledge Contract: SUCCESS
- upstream Core Safety: SUCCESS

D2J no evalúa FINAL_HOLDOUT. No emite ni consume un permit. No autoriza promoción, PAPER, capital ni LIVE.

## Objetivo

Congelar ex ante, de forma durable y append-only, tres objetos antes de cualquier observación de FINAL_HOLDOUT:

1. el winner exacto seleccionado por D2I/D2E sobre DEVELOPMENT;
2. la identidad exacta del FINAL_HOLDOUT protegido mediante un commitment sin valores;
3. la regla predictiva exacta que una etapa posterior podrá aplicar una sola vez.

La topología es deliberadamente:

```text
D2H complete frozen six-model DEVELOPMENT family
  -> D2E preregistered DEVELOPMENT tournament
  -> D2I immutable DEVELOPMENT ranking-winner seal
  -> D2J exact winner + exact protected holdout + frozen decision protocol
  -> D2K future one-shot evaluator only
```

## D2I/D2H winner rebinding

D2J no confía únicamente en que un objeto tenga forma de `DevelopmentWinnerSelectionSeal`.

Antes de registrar el protocolo ejecuta `verify_development_winner_seal(...)` y reconstruye el seal desde:

- `FamilyEvaluationPreregistration` D2H;
- `FamilyEvaluationBatchEvidence` D2H;
- plan D2E;
- tournament evidence D2E;
- output D2G exacto del winner;
- evaluation D2D exacta del winner.

Luego proyecta esa evidencia a un `OSS3D2JWinnerLineageBinding` frozen que conserva:

- D2I seal fingerprint;
- D2H preregistration fingerprint;
- D2H batch-evidence fingerprint;
- D2E plan fingerprint;
- D2E tournament-evidence fingerprint;
- trial/hypothesis seleccionado;
- model family/config;
- request hash;
- prediction artifact hash;
- prediction receipt hash;
- environment attestation hash;
- D2G run-evidence hash;
- D2D evaluation-artifact hash;
- shared runner hash;
- runtime-environment hash;
- DEVELOPMENT primary metric;
- raw exact-sign-test p-value;
- Holm-adjusted p-value.

Un seal fabricado o un cross-wire D2H falla cerrado.

## Protected FINAL_HOLDOUT commitment

D2J introduce `OSS3ProtectedFinalHoldoutCommitment`. Es una identidad estructural del holdout futuro y no contiene outcomes ni label values.

El commitment congela:

- source campaign;
- research split hash;
- source universe hash;
- label definition hash;
- protected feature artifact hash;
- protected label artifact hash;
- evaluation keyset hash;
- cross-section timestamp key hash;
- partition start/end;
- row count;
- cross-section count;
- minimum observations per cross-section.

Invariantes:

```text
label_values_exposed = false
final_holdout_observed = false
```

Además, el holdout debe usar exactamente la misma identidad científica que DEVELOPMENT para campaña, split, universo y definición de label; debe comenzar cronológicamente al finalizar DEVELOPMENT o después; no puede reutilizar el artifact de labels DEVELOPMENT ni su keyset.

El commitment es único en el registro D2J. No puede sustituirse por otro holdout después de ver resultados.

## Sample adequacy preregistration

Antes de evaluar, el commitment debe demostrar como mínimo:

```text
cross_sections >= 30
total_observations >= 90
observations_per_cross_section >= 3
```

La evaluación futura deberá producir al menos:

```text
nonzero_rank_ic_cross_sections >= 20
```

Los Rank IC exactamente cero se excluyen del denominador del exact sign test, manteniendo la misma semántica estadística utilizada en D2E.

## Frozen final predictive decision policy

D2J fija:

```text
primary_metric = mean_cross_sectional_rank_ic
mean_cross_sectional_rank_ic >= 0.02
one_sided_exact_sign_test_p_value <= 0.05
nonzero_rank_ic_cross_sections >= 20
max_evaluations = 1
```

La decisión futura deberá derivarse mecánicamente de los tres gates. No existe tuning del threshold después de observar FINAL_HOLDOUT.

El umbral Rank IC `0.02` representa una exigencia modesta de skill predictivo positivo. No es una afirmación de PnL ni una garantía económica. D2J mantiene deliberadamente separadas calidad predictiva y rentabilidad de trading.

## Multiple-testing semantics

D2E ya aplicó exact sign test + Holm sobre la familia DEVELOPMENT congelada de seis modelos. D2J no vuelve a seleccionar entre modelos y no crea una nueva familia de hipótesis:

```text
single_candidate_policy = ONE_FROZEN_WINNER_NO_RESELECTION_V1
```

FINAL_HOLDOUT contiene exactamente un candidato preseleccionado. No hay fallback al segundo, tercer u otro modelo si el winner falla.

## No second chance

El policy congela:

```text
retuning_allowed = false
reselection_allowed = false
fallback_candidate_allowed = false
second_attempt_allowed = false
failure_is_terminal = true
max_evaluations = 1
```

Un FAIL futuro no habilita un nuevo modelo ni una segunda evaluación sobre el mismo holdout.

## Expected authorization identity is not a permit

D2J deriva un identificador determinístico:

```text
oss3d2j:<digest>
```

Está ligado a:

- protocol id;
- winner-lineage fingerprint;
- protected-holdout commitment fingerprint;
- policy fingerprint;
- purpose `final_validation`;
- `max_evaluations=1`.

Este valor es únicamente la identidad esperada que D2K deberá exigir. D2J no crea `HoldoutPermit`, no llama `consume_holdout_permit` y no realiza checkout.

## Durable registry

`SQLiteOSS3FinalHoldoutProtocolRegistry` impone:

- un protocolo por D2I seal;
- un D2I seal por protocolo;
- un protected holdout commitment por protocolo;
- authorization identity única;
- receipt hash único;
- idempotencia para la misma identidad exacta;
- triggers `BEFORE UPDATE` y `BEFORE DELETE` que vuelven el registro append-only;
- reconstrucción read-only con revalidación de JSON, hashes y columnas durables.

Las identidades anidadas winner/holdout/policy son dataclasses frozen, evitando mutación profunda posterior al registro.

## Authority boundary

D2J fija permanentemente:

```text
final_holdout_observed = false
final_holdout_consumed = false
holdout_permit_issued = false
holdout_permit_consumed = false
final_holdout_checkout_authorized = false
predictive_validation_passed = false
profitability_claim_authorized = false
promotion_authorized = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

El módulo tampoco importa Qlib, pandas, numpy, scipy, sklearn, network clients, broker, OMS, Safety, execution engine ni el registry de holdout permits.

## D2K boundary

La única frontera científica posterior admisible es un evaluador one-shot separado. D2K deberá, como mínimo:

1. recibir un D2J receipt durable e íntegro;
2. revalidar winner binding, commitment y policy antes de checkout;
3. exigir una autorización `final_validation` exacta ligada al `expected_holdout_authorization_id`;
4. consumir esa autorización una sola vez antes de exponer outcomes;
5. demostrar que features, labels, support y ventana reales coinciden exactamente con el D2J commitment;
6. ejecutar únicamente el winner congelado con el mismo model config/runner/runtime contract;
7. calcular Rank IC y exact sign test exactamente como preregistrado;
8. producir un terminal PASS/FAIL append-only;
9. prohibir retuning, reselection, fallback y segundo intento tanto tras PASS como tras FAIL;
10. seguir sin otorgar por sí mismo PAPER, capital o LIVE.

D2K no debe existir dentro de D2J y no puede debilitar esta separación de autoridad.
