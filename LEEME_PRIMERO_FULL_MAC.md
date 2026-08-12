# AUTO-TRADE R6 — FULL/OFFLINE para Mac

Este paquete es la distribución recomendada para uso local en Mac. A diferencia del bundle técnico liviano, incluye dentro del ZIP el runtime necesario para una instalación sin descargas: Python 3.12.10 universal2 oficial y el wheel `websockets==16.1.1`.

## 1. Instalar una sola vez

1. Descomprime `AUTO-TRADE-R6-MAC-FULL.zip`.
2. Mueve la carpeta a una ubicación estable, por ejemplo `~/Applications/AUTO-TRADE-R6/`.
3. Haz doble clic en `INSTALAR_AUTO_TRADE.command`.
4. Si Python 3.12 no existe en el Mac, macOS puede pedir la contraseña de administrador para instalar el runtime incluido y firmado.
5. La instalación crea `.venv`, instala dependencias desde `runtime/wheels` sin Internet y ejecuta los boundaries de seguridad.
6. El cierre correcto dice `AUTO-TRADE R6 FULL/OFFLINE INSTALL: OK`.

## 2. Operar desde la plataforma

Después de instalar, usa `ABRIR_AUTO_TRADE.command`.

Se abrirá **AUTO-TRADE R6 Control Center** en el navegador, servido únicamente desde `127.0.0.1` en tu propio Mac. La terminal pequeña debe permanecer abierta mientras usas el dashboard.

Desde la plataforma puedes, sin escribir comandos:

- crear el workspace privado;
- correr Doctor, rehearsal y readiness;
- ver el estado pre-canary y el siguiente gate;
- cargar temporalmente credenciales Alpaca PAPER para los cuatro preflights GET-only;
- ejecutar Account, Asset, Flat Account y Market IEX;
- construir la candidata `CONNECTIVITY_CANARY` local;
- preparar el bracket determinista;
- congelar el review receipt después de la primera decisión humana;
- ver el progreso completo de la cadena de gates y el log de acciones.

Las credenciales PAPER **no se guardan** en archivos, localStorage ni sessionStorage y se eliminan del proceso padre. Sólo se pasan al proceso hijo del GET explícito que pulses.

## 3. Frontera de seguridad

El dashboard NO contiene acciones para:

- emitir la primera o segunda autoridad humana;
- reviewed Final Freshness;
- staging OMS;
- enviar una orden;
- habilitar LIVE.

`READY` significa únicamente que el siguiente gate indicado puede considerarse; nunca significa `POST authorized`.

Estado estructural del bundle:

- `R6_EXTERNAL_PAPER_WRITE=DISABLED`;
- `capital_authority=NONE`;
- `LIVE=BLOCKED`;
- `UNKNOWN => RECONCILIATION_ONLY`;
- primer canary limitado a US equity bracket;
- conectividad PAPER no demuestra rentabilidad.

## 4. Verificar el paquete

Antes o después de moverlo puedes ejecutar `VERIFICAR_PAQUETE.command`. El bundle FULL incluye `PACKAGE_MANIFEST.sha256`, que verifica todos los archivos publicados, incluido el runtime offline.

El Doctor de un ZIP publicado usa `MAC_BUILD_INFO.txt` y mostrará `provenance_mode=CERTIFIED_PACKAGE`; no necesita que la carpeta contenga `.git`.
