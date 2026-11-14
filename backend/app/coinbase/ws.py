"""WebSocket de datos de mercado (canal ``ticker`` + ``heartbeats``) con reconexión automática.

Los canales de mercado de Advanced Trade se pueden suscribir sin autenticación; si hay credenciales
se envía JWT igualmente. Cada tick se guarda en la tabla ``quotes`` para que la API y el motor
compartan el mismo dato y su antigüedad.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import websockets

from .auth import CdpCredentials, build_jwt

log = logging.getLogger("copytrader.ws")


def parse_ticker_message(msg: dict) -> list[tuple[str, Decimal, Decimal, Decimal]]:
    out = []
    if msg.get("channel") != "ticker":
        return out
    for ev in msg.get("events", []):
        for t in ev.get("tickers", []):
            try:
                out.append((t["product_id"], Decimal(t["best_bid"]), Decimal(t["best_ask"]), Decimal(t["price"])))
            except (KeyError, ArithmeticError, TypeError):
                continue
    return out


async def run_ticker(url: str, product_ids: Callable[[], list[str]], on_quote: Callable, creds: CdpCredentials | None = None, stop: asyncio.Event | None = None) -> None:
    backoff = 1
    stop = stop or asyncio.Event()
    while not stop.is_set():
        products = product_ids()
        if not products:
            await asyncio.sleep(5)
            continue
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
                for channel in ("ticker", "heartbeats"):
                    sub = {"type": "subscribe", "product_ids": products, "channel": channel}
                    if creds:
                        sub["jwt"] = build_jwt(creds)
                    await ws.send(json.dumps(sub))
                log.info("WebSocket conectado: %s", products)
                backoff = 1
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    msg = json.loads(raw)
                    if msg.get("type") == "error":
                        raise RuntimeError(msg.get("message"))
                    for pid, bid, ask, price in parse_ticker_message(msg):
                        on_quote(pid, bid, ask, price, datetime.now(timezone.utc), "ws")
                    if set(product_ids()) != set(products):
                        break  # cambio en la lista de activos → resuscribir
        except Exception as e:  # noqa: BLE001
            log.warning("WebSocket desconectado (%s); reintento en %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
