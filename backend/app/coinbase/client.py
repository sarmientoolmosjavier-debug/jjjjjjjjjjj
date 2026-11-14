"""Cliente REST de Coinbase Advanced Trade.

Endpoints usados (base ``https://api.coinbase.com/api/v3/brokerage``):
  GET  /key_permissions                → can_view / can_trade / can_transfer
  GET  /accounts                       → saldos
  GET  /products?product_type=SPOT     → productos, mínimos e incrementos
  GET  /products/{id}
  GET  /market/products/{id}           → (público) producto
  GET  /market/product_book            → (público) libro de órdenes
  GET  /market/products/{id}/candles   → (público) velas históricas
  GET  /transaction_summary            → nivel y comisiones reales (maker/taker)
  POST /orders/preview                 → validación sin ejecutar
  POST /orders                         → creación (con client_order_id para idempotencia)
  GET  /orders/historical/{order_id}
  GET  /orders/historical/fills?order_ids=
  POST /orders/batch_cancel
"""
from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx

from .auth import CdpCredentials, build_jwt

log = logging.getLogger("copytrader.coinbase")


class CoinbaseError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class CoinbaseClient:
    def __init__(self, base_url: str, creds: CdpCredentials | None, transport: httpx.BaseTransport | None = None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        u = urlparse(self.base_url)
        self.host, self.base_path = u.netloc, u.path
        self.creds = creds
        self._http = httpx.Client(timeout=timeout, transport=transport)

    @property
    def authenticated(self) -> bool:
        return self.creds is not None

    # ---------------------------------------------------------------- transporte
    def _request(self, method: str, path: str, *, params: dict | None = None, json: dict | None = None, auth: bool = True, retries: int = 3) -> dict:
        url = f"{self.base_url}{path}"
        last: Exception | None = None
        for attempt in range(retries):
            headers = {"Content-Type": "application/json"}
            if auth:
                if not self.creds:
                    raise CoinbaseError("Sin credenciales de Coinbase configuradas")
                # La uri del JWT va sin query string
                headers["Authorization"] = f"Bearer {build_jwt(self.creds, method, self.host, self.base_path + path)}"
            try:
                r = self._http.request(method, url, params=params, json=json, headers=headers)
            except httpx.TransportError as e:
                last = e
                time.sleep(min(2**attempt, 8))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = CoinbaseError(f"HTTP {r.status_code}", r.status_code, _safe_body(r))
                time.sleep(min(2**attempt, 8))
                continue
            if r.status_code >= 400:
                raise CoinbaseError(f"HTTP {r.status_code} en {method} {path}", r.status_code, _safe_body(r))
            return r.json() if r.content else {}
        raise CoinbaseError(f"Coinbase no responde tras {retries} intentos: {last}")

    # ---------------------------------------------------------------- lectura
    def key_permissions(self) -> dict:
        return self._request("GET", "/key_permissions")

    def accounts(self) -> list[dict]:
        out, cursor = [], None
        while True:
            params = {"limit": 250}
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", "/accounts", params=params)
            out.extend(data.get("accounts", []))
            if not data.get("has_next"):
                return out
            cursor = data.get("cursor")

    def balances(self) -> dict[str, Decimal]:
        bal: dict[str, Decimal] = {}
        for a in self.accounts():
            cur = a.get("currency")
            v = Decimal(str((a.get("available_balance") or {}).get("value", "0")))
            bal[cur] = bal.get(cur, Decimal(0)) + v
        return bal

    def products(self) -> list[dict]:
        if self.authenticated:
            data = self._request("GET", "/products", params={"product_type": "SPOT"})
        else:
            data = self._request("GET", "/market/products", params={"product_type": "SPOT"}, auth=False)
        return data.get("products", [])

    def product(self, product_id: str) -> dict:
        if self.authenticated:
            return self._request("GET", f"/products/{product_id}")
        return self._request("GET", f"/market/products/{product_id}", auth=False)

    def product_book(self, product_id: str, limit: int = 50) -> dict:
        data = self._request("GET", "/market/product_book", params={"product_id": product_id, "limit": limit}, auth=False)
        return data.get("pricebook", data)

    def candles(self, product_id: str, start: int, end: int, granularity: str = "ONE_MINUTE") -> list[dict]:
        data = self._request(
            "GET", f"/market/products/{product_id}/candles",
            params={"start": str(start), "end": str(end), "granularity": granularity}, auth=False,
        )
        return data.get("candles", [])

    def fee_rates(self) -> tuple[Decimal, Decimal, str]:
        """(maker, taker, nombre del nivel) reales de la cuenta."""
        data = self._request("GET", "/transaction_summary", params={"product_type": "SPOT"})
        tier = data.get("fee_tier") or {}
        return Decimal(str(tier["maker_fee_rate"])), Decimal(str(tier["taker_fee_rate"])), str(tier.get("pricing_tier", ""))

    # ---------------------------------------------------------------- órdenes
    @staticmethod
    def order_configuration(style: str, side: str, quote_size: Decimal | None, base_size: Decimal | None, limit_price: Decimal | None) -> dict:
        if style == "taker":
            if side == "BUY":
                return {"market_market_ioc": {"quote_size": _s(quote_size)}}
            return {"market_market_ioc": {"base_size": _s(base_size)}}
        return {"limit_limit_gtc": {"base_size": _s(base_size), "limit_price": _s(limit_price), "post_only": True}}

    def preview_order(self, product_id: str, side: str, configuration: dict) -> dict:
        return self._request("POST", "/orders/preview", json={"product_id": product_id, "side": side, "order_configuration": configuration})

    def create_order(self, client_order_id: str, product_id: str, side: str, configuration: dict) -> dict:
        body = {"client_order_id": client_order_id, "product_id": product_id, "side": side, "order_configuration": configuration}
        # Sin reintentos automáticos en POST: el reintento lo gobierna el motor con el mismo client_order_id.
        return self._request("POST", "/orders", json=body, retries=1)

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/orders/historical/{order_id}").get("order", {})

    def fills(self, order_id: str) -> list[dict]:
        return self._request("GET", "/orders/historical/fills", params={"order_ids": order_id}).get("fills", [])

    def cancel_orders(self, order_ids: list[str]) -> dict:
        return self._request("POST", "/orders/batch_cancel", json={"order_ids": order_ids})


def _s(v: Decimal | None) -> str:
    if v is None:
        raise ValueError("Tamaño/precio requerido")
    return format(v.normalize(), "f")


def _safe_body(r: httpx.Response) -> Any:
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return r.text[:500]
