# Informe de viabilidad — Copy trading verificable en Coinbase con 100 €

Fecha de la investigación: 14/11/2026. Este informe se escribió **antes** de implementar el sistema
y condiciona su diseño. Nada de lo que sigue es asesoramiento financiero.

## Resumen ejecutivo (lo importante primero)

1. **Coinbase no ofrece copy trading.** No hay API de Coinbase que publique operaciones de otros
   usuarios. Cualquier "trader" a copiar tiene que venir de una fuente externa, y la orden se replica
   después, con retraso, en Coinbase Advanced Trade.
2. **Con 100 € las comisiones son el problema principal.** Las cifras publicadas del nivel de entrada
   de Coinbase Advanced (volumen 30 días < 10 000 $) son contradictorias entre fuentes:
   0,40 %/0,60 % (maker/taker), 0,50 %/0,90 % (reestructuración de septiembre de 2026, cuentas EE. UU.)
   y 0,60 %/1,20 % (nivel "Intro 1"). Un ciclo completo compra+venta cuesta, por tanto,
   **entre 0,8 % (maker+maker, en el mejor caso) y 2,4 % (taker+taker)**, más spread y deslizamiento.
   Con el umbral por defecto pedido (coste total ≤ 1 %), **sólo es potencialmente viable operar con
   órdenes limitadas *post-only* (maker) en la entrada y en el mejor nivel de comisiones**, y aun así
   al límite. Con órdenes a mercado (taker) **no es viable**.
3. **El sistema lee la comisión real de tu cuenta** (`GET /api/v3/brokerage/transaction_summary`)
   en cuanto conectas las claves. Hasta entonces usa por defecto la estimación más conservadora
   publicada (0,60 % maker / 1,20 % taker), **por lo que en simulación sin claves casi todas las señales
   se descartarán por coste**. Es el comportamiento correcto, no un fallo: el panel lo muestra en
   "Control de riesgo → Viabilidad de costes".
4. **Fuentes de traders verificables y legales son escasas.** La única fuente pública, gratuita,
   documentada y verificable que hemos encontrado con operaciones individuales en tiempo casi real es
   la API pública `info` de **Hyperliquid** (operaciones on-chain por dirección). Tiene limitaciones
   serias (ver abajo). Por eso el sistema **arranca sin traders activos y en modo simulación**, y si
   ningún trader supera los filtros permanece en **observación**.
5. **No se puede prometer rentabilidad.** Copiar con retraso a un trader que opera con apalancamiento,
   replicándolo en spot sin apalancamiento y pagando 1–2 % por ciclo, tiene una expectativa negativa
   salvo que el trader tenga una ventaja grande y operaciones poco frecuentes. El simulador está para
   comprobarlo con datos, no para confirmarlo.

## Fase 1 — Coinbase Advanced Trade en España

| Aspecto | Hallazgo | Fuente |
|---|---|---|
| Autorización | Coinbase obtuvo licencia MiCA de la CSSF (Luxemburgo) en junio de 2025, con pasaporte a los 27 Estados de la UE, incluida España. Antes estaba registrada en el Banco de España como VASP. | Decrypt, The Paypers, Blockworks (enlaces al final) |
| Disponibilidad de Advanced Trade en España | **No hemos encontrado una página oficial que lo confirme expresamente.** Debes verificarlo en tu cuenta (si ves `coinbase.com/advanced-trade` y puedes crear claves CDP con permiso *trade*, está disponible). | — |
| Pares en EUR | BTC-EUR, ETH-EUR y otros existen históricamente. **El sistema no asume nada**: lee `GET /products` y descarta lo que no esté `online` o tenga `trading_disabled`. | Documentación CDP |
| Mínimos e incrementos | `quote_min_size`, `base_min_size`, `base_increment`, `quote_increment` por producto. **No se asume que 2, 5 o 10 € sean aceptados**: se leen de la API y, en modo real, cada orden se valida antes con `POST /orders/preview`. | Documentación CDP — List/Get Product |
| Comisiones | Modelo maker/taker por volumen 30 días. Cifras de terceros contradictorias (ver arriba). La cifra autoritativa es la de tu cuenta vía `transaction_summary`. | Coinbase, Altrady, Diario Bitcoin, Skrumble |
| Autenticación | Claves CDP; JWT firmado ES256 (clave ECDSA), cabecera `kid` = nombre de la clave, `iss: "cdp"`, `uri` = "MÉTODO host/ruta", expiración 120 s. Las claves Ed25519 **no** son aceptadas por el SDK de Advanced Trade: crea la clave como **ECDSA**. | docs.cdp.coinbase.com — JWT authentication |
| Permisos | `view`, `trade`, `transfer`. **Crea la clave sólo con `view` + `trade`.** El sistema llama a `GET /key_permissions` y **bloquea el modo real si `can_transfer` es `true`**. | docs.cdp.coinbase.com — REST scopes / Get API key permissions |
| Fiscalidad España | Cada venta (incluida cripto→EUR) es una ganancia/pérdida patrimonial tributable en el IRPF (base del ahorro), y existe la obligación del modelo 721 sólo por encima de 50 000 € en el extranjero. El sistema exporta el historial de operaciones para tu declaración; **no** calcula impuestos. | Normativa general — consulta a un asesor |

## Fase 2 — Fuentes de traders

| Fuente | ¿Legal/autorizada? | ¿Verificable? | ¿Copiable en Coinbase spot? | Decisión |
|---|---|---|---|---|
| **Hyperliquid — API pública `info`** (`userFillsByTime`, `portfolio`, `spotMeta`) | Sí: API pública y documentada, sin clave. | Sí: las operaciones están on-chain y asociadas a una dirección. | **Parcial.** La mayoría de la actividad es de perpetuos con apalancamiento y posiciones cortas. Sólo se pueden replicar compras spot y aperturas/cierres de **largos**, como compra/venta spot **sin apalancamiento**, y sólo en activos que existan como `XXX-EUR` en Coinbase. | **Implementada** (adaptador principal). Las señales de perpetuos se etiquetan como "derivadas" y se pueden desactivar. |
| eToro — API pública (rankings, estadísticas de Popular Investors) | Sí, con clave de eToro y sujeta a sus condiciones. | Estadísticas agregadas calculadas por eToro (no auditables operación a operación). | No encontramos un endpoint público documentado con las operaciones individuales de terceros en tiempo real. | **No implementada.** Documentada como posible ampliación. |
| Leaderboards de copy trading de Binance/Bybit/OKX | No hay API pública oficial para leer posiciones de terceros; el scraping vulnera sus condiciones. | No independientemente. | — | **Excluida.** |
| Plataformas de analítica on-chain (Arkham, Nansen…) | Sí, pero de pago. | Parcial. | — | **Excluida** (coste desproporcionado para 100 €). |
| Señales propias (webhook firmado HMAC, p. ej. alertas de TradingView) | Sí: son tuyas. | Sólo tú respondes por ellas; el sistema mide su historial a partir de su registro. | Sí. | **Implementada** como fuente "manual". |

### Limitaciones de Hyperliquid que el sistema tiene en cuenta

* **Una dirección no es un inversor.** Un trader puede tener muchas direcciones; vemos una. El panel
  dice siempre "historial de la dirección", nunca "historial del trader".
* **Depósitos y retiradas distorsionan el valor de la cuenta.** Las métricas se calculan con el
  `pnlHistory` (PnL acumulado) sobre el valor de cuenta previo, no con el valor de cuenta bruto.
* **Sesgo de supervivencia.** Los rankings públicos muestran a quien ha ganado. El backtest selecciona
  traders sólo con datos anteriores a la fecha de corte y simula después de ella.
* **Retraso.** Consultamos por sondeo (por defecto cada 30 s). Las señales con más de 120 s se descartan.
* **El leaderboard de Hyperliquid no está en la documentación oficial.** No se usa: las direcciones
  candidatas se añaden a mano desde el panel y se evalúan con la API oficial.

## Fase 7 — Algoritmo de costes para 100 € (resumen)

Para cada señal de compra:

```
coste_entrada   = comisión_entrada (maker o taker) + [taker: ½ spread + deslizamiento]
coste_salida    = comisión_taker (supuesto conservador) + ½ spread + deslizamiento
coste_total_%   = coste_entrada + coste_salida
rentabilidad mínima para cubrir costes ≈ coste_total_% / (1 − coste_total_%)
descartar si coste_total_% > MAX_ROUND_TRIP_COST_PCT (1 % por defecto, configurable)
```

El deslizamiento se estima recorriendo el libro de órdenes para el importe exacto, con un mínimo de
2 pb. Las ventas (cierres) **nunca** se bloquean por coste: salir no debe depender de que sea barato.

Ejemplo con las comisiones conservadoras por defecto (0,60 % maker / 1,20 % taker), 15 € en BTC-EUR
con spread de 0,02 %:

| Estilo | Entrada | Salida | Total | ¿Pasa el 1 %? |
|---|---|---|---|---|
| Taker/taker | 1,20 % + 0,01 % + 0,02 % | 1,20 % + 0,01 % + 0,02 % | ≈ 2,46 % | No |
| Maker entrada / taker salida | 0,60 % | 1,23 % | ≈ 1,83 % | No |

Con 0,40 %/0,60 %: maker/taker ≈ 1,03 % (no pasa por poco); maker/maker ≈ 0,8 % (pasaría, pero la
salida maker puede no ejecutarse). **Conclusión: con 100 € y el nivel de comisiones de entrada, el
copy trading es probablemente inviable económicamente.** El sistema lo detecta, lo informa y no
permite activar el modo real mientras ningún activo permitido pase el umbral.

## Decisiones de diseño derivadas

* Arranque obligatorio en **simulación** con 100 € virtuales; el modo real está desactivado por
  defecto, requiere una lista de comprobaciones y una frase de confirmación, y **no se reactiva tras
  reiniciar** salvo autorización persistente firmada y con caducidad, desactivada por defecto.
* **Ningún trader activado por defecto** y ningún dato inventado: sin conexión, el sistema no opera
  ("datos incompletos").
* Arquitectura mínima: SQLite, un proceso API, un proceso *worker* y el panel. Sin servicios de pago.

## Fuentes

* Coinbase Developer Platform — JWT authentication: https://docs.cdp.coinbase.com/get-started/authentication/jwt-authentication
* Advanced Trade REST API: https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/rest-api
* Permisos (scopes): https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/rest-scopes
* Get API key permissions: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/data-api/get-api-key-permissions
* List/Get product: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/products/get-product
* WebSocket: https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-authentication
* Coinbase Advanced (comisiones): https://www.coinbase.com/ADVANCED-TRADE
* Altrady — Coinbase Advanced Trade fees: https://www.altrady.com/blog/crypto-trading-platforms/coinbase-advanced-trade
* Diario Bitcoin — reducción de comisiones (16/09/2026): https://www.diariobitcoin.com/exchanges/coinbase-reduce-sus-comisiones-de-advanced-y-baja-el-umbral-inicial-a-usd-10-000/
* Skrumble — Coinbase fees: https://skrumble.com/learn/coinbase-fees/
* Decrypt — licencia MiCA: https://decrypt.co/326438/coinbase-mica-license-crypto-services-eu
* The Paypers — MiCA Luxemburgo: https://thepaypers.com/crypto-web3-and-cbdc/news/coinbase-secures-mica-licence-in-luxembourg-to-expand-across-all-27-eu-countries
* Blockworks — Banco de España: https://blockworks.co/news/coinbase-bank-of-spain-vasp-crypto-exchange
* Hyperliquid — Info endpoint: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
* eToro API portal: https://api-portal.etoro.com/
