# Coinbase Smart Copy Trader — 100 € · riesgo moderado

Aplicación de copy trading de criptomonedas spot para Coinbase Advanced Trade, pensada para una cartera de
**100 €** con control estricto de costes y riesgo. Arranca **siempre en simulación** con 100 € virtuales.

> **Lee primero [`docs/VIABILIDAD.md`](docs/VIABILIDAD.md).** Resumen: Coinbase no ofrece copy trading; la única
> fuente pública y verificable que encontramos con operaciones individuales es Hyperliquid (direcciones on-chain);
> y con las comisiones del nivel de entrada de Coinbase (0,4–1,2 % por lado) un ciclo compra+venta cuesta
> 0,8–2,4 %, por lo que **con el umbral por defecto del 1 % casi ninguna operación es viable**. El sistema lo
> detecta, lo muestra en el panel y no deja activar el modo real en esas condiciones.
> **No es asesoramiento financiero y no se afirma que sea rentable.**

## Qué incluye

| Componente | Ubicación |
|---|---|
| API (FastAPI) | `backend/app/api.py` |
| Servicio de monitorización y ejecución (independiente) | `backend/app/worker.py` |
| Conector Coinbase: JWT ES256, saldos, productos, comisiones reales, preview/creación/seguimiento de órdenes | `backend/app/coinbase/` |
| WebSocket de mercado con reconexión automática + respaldo REST | `backend/app/coinbase/ws.py`, `backend/app/market.py` |
| Fuentes de traders: Hyperliquid (API pública `info`) y señales propias por webhook HMAC | `backend/app/sources/`, `api.py` |
| Motor de selección (métricas y puntuación 0–100) | `backend/app/ranking.py` |
| Motor de copy trading (pipeline de 12 pasos, idempotente) | `backend/app/engine.py` |
| Módulo de costes para 100 € | `backend/app/costs.py` |
| Motor de riesgo | `backend/app/risk.py` |
| Simulador (paper trading) y adaptador de órdenes reales | `backend/app/brokers.py` |
| Activación del modo real | `backend/app/activation.py` |
| Backtest sin sesgo de anticipación | `backend/app/backtest.py` |
| Base de datos (SQLite → PostgreSQL cambiando `DATABASE_URL`) | `backend/app/models.py` |
| Panel (Next.js 16, React 19, Tailwind) en español | `frontend/` |
| Tests (55) | `backend/tests/` |
| Docker Compose | `docker-compose.yml` |

## Puesta en marcha

### Con Docker
```bash
cp .env.example .env                       # edita ADMIN_TOKEN y APP_SECRET
cp secrets/cdp_api_key.json.example secrets/cdp_api_key.json   # {} = sin claves (sólo datos públicos)
docker compose up -d --build
# Panel: http://localhost:3000   (sólo escucha en 127.0.0.1)
```

### Sin Docker (desarrollo)
```bash
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.api:app --port 8000            # terminal 1
python -m app.worker                       # terminal 2
cd ../frontend && npm install && BACKEND_URL=http://localhost:8000 npm run dev   # terminal 3
```
Tests: `cd backend && pytest -q`. Comprobación de tipos del panel: `cd frontend && npm run typecheck`.

### Claves de Coinbase (opcional; sin ellas todo funciona en simulación con datos públicos)
1. En https://portal.cdp.coinbase.com crea una **API key de Advanced Trade**, algoritmo **ECDSA** (las Ed25519 no
   sirven para Advanced Trade).
2. Permisos: **sólo `View` y `Trade`**. **Nunca `Transfer`**: el sistema lo comprueba con `GET /key_permissions` y
   bloquea el modo real si la clave permite transferencias.
3. Guarda el JSON descargado como `secrets/cdp_api_key.json` (Docker) o define `COINBASE_API_KEY_NAME` y
   `COINBASE_API_PRIVATE_KEY` en `.env`. Nunca en el código ni en el navegador. Los logs redactan claves y tokens.

Con claves, el sistema lee **tus comisiones reales** (`transaction_summary`), tus saldos y los mínimos de cada
producto. Sin claves usa la estimación conservadora `DEFAULT_MAKER_FEE=0.006` / `DEFAULT_TAKER_FEE=0.012`.

## Uso

1. **Traders → Añadir candidato**: una dirección pública de Hyperliquid (`0x…`) o un identificador de señales propias.
2. **Evaluar**: descarga su PnL histórico y sus operaciones de la API oficial y calcula rentabilidad, drawdown,
   Sharpe, consistencia mensual, nº de operaciones, antigüedad, apalancamiento, comisiones y **% replicable en
   Coinbase spot**. Sólo los que superan los filtros (≥ 90 días, ≥ 20 operaciones, drawdown ≤ 40 %, ≥ 30 % replicable,
   rentabilidad > 0, frecuencia compatible con las comisiones) quedan *verificados*.
3. **Activar la copia** (máx. 3). Desde ese momento se copian sólo operaciones nuevas, nunca las pasadas.
4. El worker sondea cada 30 s; cada señal pasa por el pipeline y queda registrada en **Operaciones → Registro de
   decisiones** con su motivo y su desglose de costes.

Si ningún trader cumple los requisitos, el sistema no hace nada (equivale al modo observación).

### Señales propias (webhook)
Define `WEBHOOK_SECRET`, añade un trader de fuente «Señales propias» con id `mio` y envía:
```bash
BODY='{"id":"42","trader":"mio","asset":"BTC","side":"BUY","fraction":0.15,"timestamp":'$(date +%s)'}'
SIG=$(printf %s "$BODY" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | cut -d' ' -f2)
curl -X POST localhost:8000/api/signals/webhook -H "X-Signature: $SIG" -d "$BODY"
```
`fraction` es la parte del patrimonio (compras) o de la posición (ventas). Sus resultados se miden con su propio
historial registrado antes de considerarlas verificadas.

### Backtest
```bash
cd backend && python -m app.backtest --address 0x... --cutoff 2026-05-01 --end 2026-11-01 --delay 60
```
Selecciona al trader **sólo con datos anteriores a `--cutoff`**, ejecuta cada señal al precio de apertura de la
primera vela de Coinbase posterior a *señal + retraso*, aplica comisiones, spread, deslizamiento y los mismos límites
de riesgo, y compara con mantener BTC y mantener efectivo.

## Pipeline de copia (por señal)

1. Identidad y fuente del trader · 2. Antigüedad de la señal (máx. 120 s para compras; las ventas se siguen aunque
lleguen tarde) · 3. El activo existe como `XXX-EUR`, negociable, permitido y no recién listado · 4. Precio actual con
control de antigüedad (≤ 15 s; si no, respaldo REST; si tampoco, se descarta) · 5. Spread · 6. Comisiones (reales
o estimadas) · 7. Importe proporcional (fracción de la cuenta del trader × tu patrimonio; **nunca se aumenta** para
alcanzar mínimos) · 8. Saldo · 9. Límites de riesgo (el importe se recorta a lo permitido) · 10. Coste ida y vuelta ≤
umbral · 11. Registro · 12. Ejecución sólo en simulación o en modo real autorizado.

El `client_order_id` es un UUIDv5 determinista de (señal, modo): la misma señal nunca genera dos órdenes, y tras un
error de red se reintenta con el mismo id (Coinbase no duplica). Las señales se deduplican por (fuente, id externo).

## Costes (optimización para 100 €)

```
entrada = comisión maker            (orden límite post-only al mejor bid: sin spread ni deslizamiento)
        | comisión taker + ½ spread + deslizamiento (estimado recorriendo el libro, mínimo 2 pb)
salida  = comisión taker + ½ spread + deslizamiento (supuesto conservador)
descartar si entrada + salida > MAX_ROUND_TRIP_COST_PCT (1 %)
rentabilidad mínima para cubrir costes = coste / (1 − coste)
```
Por defecto la entrada es **maker** (más barata). En simulación una orden maker sólo se considera ejecutada si el
mercado negocia **por debajo** de su precio límite, y caduca a los 5 min. Las ventas siempre son taker y nunca se
bloquean por coste. **Riesgo → Viabilidad de costes** muestra el cálculo con las comisiones vigentes.

## Riesgo

| Límite | Valor | Efecto |
|---|---|---|
| Reserva de liquidez | ≥ 30 € | no se compra por debajo |
| Exposición total | ≤ 70 € (70 %) | se recorta el importe |
| Exposición por activo | ≤ 20 € (20 %) | se recorta el importe |
| Posiciones abiertas | ≤ 4 | no se abren nuevas |
| Operaciones nuevas/día | ≤ 3 | (día natural, hora de Madrid) |
| Pérdida diaria | 2 € | bloquea compras hasta el día siguiente |
| Drawdown desde máximo | 10 € | suspende la estrategia hasta revisión manual |

Los límites en € escalan a la baja si el patrimonio cae por debajo de 100 €. El panel no permite superar estos topes.
Sin apalancamiento, futuros, derivados ni cortos. Los límites son mecanismos de control, **no garantías de precio**.
No hay liquidación automática de la cartera: la parada de emergencia cancela órdenes abiertas y bloquea todo, pero no vende.

## Modo real (desactivado por defecto)

Requisitos (pestaña Configuración): `LIVE_TRADING_ALLOWED=true` en el entorno · claves cargadas y conexión correcta ·
permiso `trade` y **sin** `transfer` · órdenes de prueba aceptadas por `POST /orders/preview` (no ejecutan) ·
comisiones reales leídas · al menos un activo económicamente viable · saldos reconciliados con el libro interno ·
sin errores críticos en 24 h · worker activo · frase de confirmación escrita a mano.

La autorización queda ligada al **arranque del worker**: si se reinicia, el sistema vuelve a simulación. Sólo con
`LIVE_PERSIST_AUTH=true` se reanuda tras un reinicio, con una autorización firmada (HMAC con `APP_SECRET`), ligada a la
huella de la clave API y con caducidad (`LIVE_PERSIST_AUTH_HOURS`). Nunca pasa a real por alcanzar una rentabilidad.
En modo real el capital gestionado es `min(saldo EUR, 100 €)`.

## Seguridad

Variables de entorno y secretos de Docker · redacción de claves/tokens en logs y auditoría · `ADMIN_TOKEN` para
cualquier cambio desde el panel · el panel sólo se publica en `127.0.0.1` y el backend no se expone · tabla
`audit_log` con cada decisión, orden y cambio de ajustes · notificación de errores críticos a un webhook gratuito
(`NOTIFY_WEBHOOK_URL`, p. ej. ntfy) · reintentos con backoff en REST y reconexión del WebSocket · rechazo de datos
de mercado incoherentes o antiguos · parada de emergencia.

## Estado de verificación (honesto)

* **55 tests pasan** (`pytest`): autenticación JWT, redacción de secretos, costes, riesgo, ranking, normalización de
  operaciones de Hyperliquid, motor completo en simulación, idempotencia, órdenes maker, activación/desactivación del
  modo real, flujo de orden real contra un **servidor de Coinbase simulado**, API y backtest sin anticipación.
* Las integraciones con Coinbase y Hyperliquid se implementaron según su documentación oficial, pero **no se han
  podido ejecutar contra los servicios reales** en el entorno donde se desarrolló (red restringida). Las respuestas
  usadas en los tests son simuladas (`tests/conftest.py`) y están etiquetadas como tales. Antes de usar dinero real,
  ejecuta varios días en simulación con tus claves y revisa el registro de decisiones.
* El panel se compiló (`next build`) y se revisó en navegador con datos de demostración; las imágenes Docker no se
  construyeron en ese entorno (`docker compose config` sí valida).
* Las cifras de comisiones de terceros son contradictorias; la autoritativa es la de tu cuenta.

## Fases

| Fase | Estado |
|---|---|
| 1. Documentación y compatibilidad España | `docs/VIABILIDAD.md` |
| 2. Fuentes de traders | `docs/VIABILIDAD.md`, `sources/` |
| 3. Arquitectura y esquema | este README, `models.py` |
| 4. Conexión de solo lectura | `coinbase/client.py`, `market.py` |
| 5. Ranking y señales | `ranking.py`, `sources/` |
| 6. Riesgo y costes | `risk.py`, `costs.py` |
| 7. Paper trading | `brokers.PaperBroker` |
| 8. Dashboard | `frontend/` |
| 9. Órdenes reales (desactivadas) | `brokers.LiveBroker`, `activation.py` |
| 10. Pruebas y despliegue | `backend/tests/`, `docker-compose.yml` |

## Fiscalidad (España)

Cada venta genera una ganancia o pérdida patrimonial a declarar en el IRPF. **Operaciones → Exportar CSV** descarga
el historial; el sistema no calcula impuestos. Consulta a un asesor.
