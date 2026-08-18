# AUTO-TRADE R6 — Primer Canary BTC/USD REAL PAPER

Este paquete permite ensayar por primera vez el camino técnico completo contra **Alpaca PAPER**, sin habilitar LIVE y sin abrir el Control Center genérico a órdenes.

## Qué hace y qué no hace

- Entorno: **PAPER únicamente**.
- Instrumento: **BTC/USD**.
- Entrada: **BUY LIMIT IOC**.
- Tamaño máximo duro: **USD 5**; objetivo del primer ensayo: aproximadamente USD 2.
- LIVE: **BLOCKED**.
- Reintento de POST: **FORBIDDEN**.
- Si existe duda sobre el resultado de un POST: **solo recuperación GET**.
- Las credenciales PAPER se usan de forma efímera y no se guardan en los artefactos del intento.
- Instalar, preparar, aprobar, abrir dashboards o ejecutar los tests del paquete **no envía una orden por sí solo**.

## 1. Instalar

Desde el paquete descargado, ejecuta:

`INSTALAR_AUTO_TRADE.command`

La instalación se mueve a:

`~/Applications/AUTO-TRADE-R6`

Usa únicamente los launchers de la copia instalada.

## 2. Preparar y aprobar — todavía NO hay POST

Abre:

`ABRIR_PRIMER_CANARY_PREPARAR.command`

En la pantalla:

1. Crea un `Attempt ID` nuevo.
2. Introduce las credenciales de **Alpaca PAPER**, nunca LIVE.
3. Pulsa **1. Preparar canary**.
4. Revisa el resumen: BTC/USD, alrededor de USD 2 y siempre <= USD 5.
5. Copia exactamente el challenge de aprobación.
6. Pégalo en el campo de confirmación y pulsa **2. Aprobar este intento**.

La sección 3 de esta pantalla seguirá diciendo que el POST real está bloqueado. **Es correcto:** esta interfaz es deliberadamente no-POST. La preparación queda guardada con evidencia tipada restart-safe.

No necesitas copiar manualmente el `Attempt ID` a la pantalla REAL PAPER.

## 3. Ejecutar el único POST PAPER permitido

Abre por separado e inmediatamente después de aprobar:

`ABRIR_PRIMER_CANARY_REAL_PAPER.command`

La pantalla consulta el `Workspace` durable y aplica una regla fail-closed:

- si existe **exactamente un** intento aprobado, restart-safe, no iniciado y todavía vigente, lo carga automáticamente;
- si no existe ninguno vigente, no habilita ejecución y pide volver a PREPARAR;
- si existen dos o más intentos válidos simultáneamente, **no elige por ti** y bloquea por ambigüedad;
- intentos cuyo package o aprobación ya expiraron no son elegibles para autoselección.

Después:

1. Confirma que el `Attempt ID` completo apareció automáticamente.
2. El estado debe ser `READY_FOR_SECOND_EXACT_POST_CONFIRMATION`.
3. Verifica nuevamente BTC/USD, notional, cantidad, limit price, `client_order_id` y deadline.
4. Introduce de nuevo las credenciales PAPER.
5. Copia exactamente el **segundo challenge** mostrado en rojo.
6. Pégalo en el campo correspondiente.
7. Pulsa **EJECUTAR UNA VEZ EN PAPER** una sola vez.

Antes del POST el sistema vuelve a consultar por GET cuenta, asset, flatness y mercado. Esa latencia consume el TTL de frescura. Si la evidencia queda vieja, la ejecución falla cerrada.

## 4. Regla crítica después de consentir o iniciar

Una vez exista `external_post_consent.json` o evidencia de `execution_started`, **no intentes ejecutar de nuevo**, incluso si el navegador mostró timeout, error, se cerró la ventana o no viste respuesta.

Usa únicamente:

**RECUPERAR / RECONCILIAR GET-ONLY**

El `client_order_id` es determinista y el latch durable impide convertir una respuesta ambigua en un segundo POST.

## 5. Qué resultado esperar

Un resultado reconciliado puede terminar como final/flat/posición observada según la respuesta real de PAPER y el estado del broker. Un resultado incierto debe permanecer en estado de reconciliación; **nunca implica permiso para reintentar**.

Este canary certifica conectividad y control operativo. **No demuestra rentabilidad de una estrategia.**

## No hacer

- No usar credenciales LIVE.
- No habilitar `R6_EXTERNAL_PAPER_WRITE=ENABLED`.
- No reutilizar un `Attempt ID` consumido.
- No copiar a mano un ID parcial como `first-canary-…`.
- No repetir el botón de ejecución.
- No interpretar un 404 de orden como autorización para reenviar POST.
- No mover esta autoridad al Control Center genérico.

## Flujo resumido

`Instalar → Preparar restart-safe → Aprobar → Abrir REAL PAPER → Autodetectar único intento vigente → Revisar → Segundo challenge → Ejecutar una vez → Reconciliar por GET`
