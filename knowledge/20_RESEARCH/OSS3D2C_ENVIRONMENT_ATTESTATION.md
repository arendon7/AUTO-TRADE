# OSS-3D2C — Qlib Environment Attestation

Estado objetivo: **RESEARCH ONLY / REPRODUCIBILITY EVIDENCE / NO EXECUTION AUTHORITY**.

## 1. Problema

OSS-3D2B fijó `pyqlib==0.9.7` y demostró un `LinearModel` Ridge real, pero la instalación de Qlib resuelve un árbol transitivo amplio. Un pin primario no basta para afirmar que dos ejecuciones realizadas en fechas distintas usaron el mismo entorno científico.

OSS-3D2C agrega una atestación canónica del entorno **observado**. No cambia el runner D2B, no entrena un modelo, no hace inferencia y no abre DEVELOPMENT labels.

## 2. Separación deliberada

D2C parte del head D2B certificado `3717c36c539edbaeccd78db15f1bfa5c67765c44`.

El módulo nuevo vive en:

```text
labs/oss3_qlib/environment_attestation.py
```

No se añade ninguna dependencia al `pyproject.toml` del core y no se modifica ninguno de los cinco archivos que forman `runner_code_hash()` D2B.

## 3. Artifact V1

Versión:

```text
OSS3D2C_ENVIRONMENT_ATTESTATION_V1
```

Política:

```text
SANITIZED_INSTALLED_DISTRIBUTIONS_V1
```

El artifact contiene únicamente:

- implementación Python;
- versión Python;
- sistema operativo genérico (`linux`, `darwin`, etc.);
- arquitectura de máquina;
- identidad/version de libc cuando esté disponible;
- distribución Qlib esperada (`pyqlib`);
- versión Qlib exacta (`0.9.7`);
- familia de modelo D2B;
- `model_config_hash`;
- `runner_code_hash`;
- lista canónica ordenada de `{name, version}` para distribuciones Python instaladas;
- `distribution_set_hash`;
- `artifact_hash`;
- campos de autoridad permanentemente no operativos.

## 4. Lo que NO se captura

La atestación no lee ni serializa:

- variables de entorno;
- secretos o tokens;
- claves API;
- credenciales de broker;
- hostname/FQDN;
- username;
- home directory;
- current working directory;
- rutas de instalación de paquetes;
- IP/MAC;
- node/processor identifiers;
- timestamps;
- estado de red.

La ausencia de timestamp es deliberada: inputs idénticos en el mismo entorno deben producir exactamente el mismo artifact hash.

## 5. Distribuciones canónicas

Los nombres se normalizan con semántica tipo PEP 503:

```text
Scikit_Learn -> scikit-learn
zope.interface -> zope-interface
```

La lista se ordena por nombre/version y no permite nombres canónicos duplicados. Si dos distribuciones instaladas colisionan bajo el mismo nombre canónico pero reportan versiones distintas, la recolección falla cerrado.

La lista debe contener exactamente `pyqlib==0.9.7`; ausencia o drift de versión invalida el artifact.

## 6. Identidad del experimento

D2C no describe un entorno abstracto. Cada manifest queda ligado a:

- `qlib_version`;
- `MODEL_FAMILY`;
- `model_config_hash()`;
- `runner_code_hash()`.

Por tanto una comparación longitudinal puede distinguir entre:

1. cambio de entorno con runner/modelo iguales;
2. cambio de runner/modelo;
3. ambas cosas.

## 7. Hashing

`distribution_set_hash` se calcula sobre la lista canónica completa de nombres/versiones.

`artifact_hash` liga:

- artifact version;
- manifest completo;
- lista completa de distribuciones.

El JSON persistido usa representación canónica y el reader rechaza:

- claves JSON duplicadas;
- schema extra/faltante;
- orden no canónico;
- distribución duplicada;
- hash alterado;
- autoridad operativa;
- Qlib incorrecto.

## 8. Authority boundary

El manifest fija:

```text
research_only = true
execution_authorized = false
paper_execution_authorized = false
capital_authority = NONE
live_trading = BLOCKED
```

D2C no importa ni llama brokers, OMS, Safety, engine, red o subprocess.

## 9. CI propuesto

El Dedicated D2C debe:

1. instalar sólo el core AUTO-TRADE;
2. compilar artifact/tests/checker;
3. probar el checker estático antes de Qlib;
4. ejecutar tests sintéticos sin depender de Qlib instalado;
5. instalar `pyqlib==0.9.7` usando el requirements D2B certificado;
6. recolectar una atestación del entorno real;
7. verificar round-trip canónico y determinismo;
8. ejecutar regresión D2B real Ridge;
9. re-probar D2B boundary;
10. ejecutar Research Authority, W83 y OSS-2H;
11. mantener Core Safety completamente independiente de Qlib.

## 10. Alcance científico

D2C mejora **reproducibilidad y auditabilidad**. No convierte el árbol transitivo observado en un lock reproducible por sí mismo.

Una etapa posterior podrá usar una atestación certificada para generar/validar constraints o wheel hashes. Esa decisión debe probarse por plataforma y no debe alterar silenciosamente el runtime certificado D2B.

La evaluación de predictions contra DEVELOPMENT labels también queda para una frontera posterior separada, para mantener la separación entre **producción de predictions**, **identidad del entorno** y **evaluación científica**.

**Environment-attested ≠ profitable.** Esta evidencia demuestra qué software produjo una inferencia; no demuestra que la inferencia tenga alpha.