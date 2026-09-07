# OSS-3D2N — Prediction precommit hardening

## Motivo

La primera implementación D2N verificaba que el `QlibPredictionArtifact` económico tuviera la misma identidad de modelo congelada en D2L y que su soporte coincidiera con el economic holdout D2M.

Eso no era suficiente.

Un artifact nuevo podía conservar:

- `model_family`;
- `model_config_hash`;
- `qlib_version`;
- `training_dataset_hash`;
- `feature_schema_hash`;
- `producer_code_hash`;
- universo y ventana correctos;

pero contener **scores distintos** y presentarse después de conocer el resultado D2K.

Aunque el evaluator siguiera siendo one-shot, esa sustitución habría convertido los predictions económicos en un grado de libertad post-hoc.

Por esa razón el head inicial de D2N no se considera certificable.

## Orden endurecido

La secuencia canónica pasa a ser:

```text
D2I DEVELOPMENT winner
  -> D2J predictive FINAL_HOLDOUT protocol
    -> D2L predictor-to-strategy preregistration
      -> D2M economic protocol preregistration
        -> D2N prediction precommit
          -> D2K predictive FINAL_HOLDOUT one-shot
            -> D2N economic holdout one-shot
```

El prediction precommit ocurre **después de D2M**, porque necesita conocer la identidad value-opaque del economic holdout, pero **antes de cualquier D2K start/permit consumption**.

## Qué congela

`OSS3EconomicPredictionPrecommitReceipt` liga de forma durable:

- D2M protocol id y receipt hash;
- D2L receipt y binding hash;
- D2L strategy semantic hash;
- D2J protocol id y receipt hash;
- model family;
- model config hash;
- Qlib version;
- training dataset hash;
- feature schema hash;
- producer/shared runner code hash;
- exact `prediction_artifact_hash`;
- exact `prediction_payload_hash`;
- exact support hash;
- registration timestamp.

Y congela:

```text
frozen_before_d2k_start = true
prediction_values_frozen = true
economic_market_values_used = false
economic_outcomes_used = false
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

Los valores de score no se leen para escoger política ni thresholds. Se congelan indirectamente mediante los hashes canónicos del artifact/payload.

## Verificación de support sin mirar outcomes

El precommit deriva el clock esperado exclusivamente desde el commitment D2M:

```text
partition_start
+ timeframe_seconds
+ bar_count
+ symbols
```

Para un holdout de `N` barras, exige predictions en cada close no terminal:

```text
bar 0 close
bar 1 close
...
bar N-2 close
```

para todos los símbolos comprometidos y en orden canónico.

También exige:

```text
prediction.inference_start == first signal close
prediction.inference_end   == economic partition_end
```

El checker AST prohíbe que el módulo acceda a `row.score`.

## Orden durable

Dentro de `BEGIN IMMEDIATE`, el registry de precommit exige:

```text
exact durable D2M receipt
  -> exact durable D2L receipt
    -> no D2K start
      -> no consumed predictive permit
        -> append-only prediction precommit
```

Una nueva preregistración después de D2K falla cerrada.

## Enforcement dentro de SQLite

No se confía únicamente en que el caller recuerde usar el precommit.

Al instalar el registry se crea el trigger:

```text
oss3_d2n_start_requires_prediction_precommit
```

antes de cualquier INSERT en:

```text
oss3_economic_holdout_evaluation_starts
```

El trigger exige una fila precommit que coincida exactamente en:

- economic protocol;
- D2M receipt;
- D2L receipt;
- strategy semantic hash;
- D2J protocol;
- D2J receipt;
- **economic prediction artifact hash**.

Si los scores cambian, cambia el artifact hash y el start D2N es rechazado antes del checkout.

Esto protege también contra un uso accidental directo del evaluator interno una vez que el schema canónico hardened fue instalado.

## Tests adversariales

La suite hardened prueba específicamente:

1. exact prediction precommit antes de D2K -> D2N puede avanzar;
2. mismo mercado, misma ventana, mismo modelo, **scores alterados** -> start rechazado por SQLite;
3. evaluator interno sin precommit -> start rechazado;
4. intentar crear precommit después de D2K -> rechazado;
5. model config drift -> rechazado;
6. support/keyset drift -> rechazado;
7. UPDATE/DELETE del precommit -> rechazado por triggers.

En los casos 2 y 3 se verifica además que el contador de starts D2N siga en cero.

## Frontera de autoridad

El prediction precommit no añade ejecución ni acceso a capital.

No importa broker, OMS, Safety ni `OrderIntent`; no recibe prices, barras, returns, labels u outcomes económicos; no tiene red ni subprocess.

Su única función es congelar identidad predictiva antes de D2K.

## Limitación explícita

Este hardening demuestra que el artifact exacto no puede ser sustituido **después** del precommit/D2K.

No constituye por sí solo una prueba criptográfica de que los scores fueron materialmente producidos por una ejecución real del modelo Qlib congelado. La identidad del manifest debe coincidir con D2L, pero un actor que deliberadamente fabricara un artifact antes del precommit aún estaría fuera del threat model cubierto por esta etapa.

Por tanto, incluso si D2N queda certificado, la siguiente mejora científica recomendable será una **provenance receipt de generación económica**: ejecutar el predictor congelado en un runner aislado/value-opaque y ligar su attestation + runner hash + input feature commitment al prediction precommit, sin observar outcomes económicos.

Hasta entonces no se debe describir el prediction precommit como una prueba criptográfica de model execution provenance.

## Criterio de certificación D2N endurecido

El head D2N sólo será certificable cuando, sobre el mismo commit, pasen:

```text
D2N evaluator boundary
D2N prediction precommit boundary
D2M/D2L/D2J/D2I/D2H regressions
Research Authority
W81/W82 boundaries
pyqlib == 0.9.7
prediction-precommit adversarial suite
D2N economic suite
D2K real one-shot suite
D2G six-candidate real runtime
D2E/D2D regressions
existing FINAL_HOLDOUT governance
Knowledge Contract
Core Safety
```

La PR debe permanecer DRAFT y no activa PAPER, capital ni LIVE.
