"""Fixtures. Las respuestas de Coinbase/Hyperliquid son **simuladas** (MockTransport) con la forma
documentada de la API; no son datos reales de mercado."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    for k in ("ADMIN_TOKEN", "COINBASE_API_KEY_NAME", "COINBASE_API_PRIVATE_KEY", "LIVE_TRADING_ALLOWED", "WEBHOOK_SECRET", "NOTIFY_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    from app import config, db, market, services

    config.get_settings.cache_clear()
    db.init_engine()
    market.set_client(None)
    services.set_hl_client(None)
    yield
    market.set_client(None)
    config.get_settings.cache_clear()


def ec_pem() -> str:
    k = ec.generate_private_key(ec.SECP256R1())
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


PRODUCTS = [
    {"product_id": "BTC-EUR", "base_currency_id": "BTC", "quote_currency_id": "EUR", "product_type": "SPOT", "status": "online",
     "trading_disabled": False, "base_increment": "0.00000001", "quote_increment": "0.01", "base_min_size": "0.00000001",
     "quote_min_size": "1", "volume_24h": "1000", "new": False},
    {"product_id": "ETH-EUR", "base_currency_id": "ETH", "quote_currency_id": "EUR", "product_type": "SPOT", "status": "online",
     "trading_disabled": False, "base_increment": "0.00000001", "quote_increment": "0.01", "base_min_size": "0.00000001",
     "quote_min_size": "1", "volume_24h": "5000", "new": False},
    {"product_id": "BTC-USD", "base_currency_id": "BTC", "quote_currency_id": "USD", "product_type": "SPOT", "status": "online"},
]


class FakeCoinbase:
    """Estado mutable del 'servidor' simulado de Coinbase."""

    def __init__(self):
        self.book = {"BTC-EUR": (Decimal("60000"), Decimal("60010")), "ETH-EUR": (Decimal("3000"), Decimal("3000.5"))}
        self.perms = {"can_view": True, "can_trade": True, "can_transfer": False, "portfolio_type": "DEFAULT"}
        self.fees = {"maker_fee_rate": "0.0025", "taker_fee_rate": "0.0040", "pricing_tier": "Test"}
        self.balances = {"EUR": "100", "BTC": "0"}
        self.created: list[dict] = []
        self.requests: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        p = req.url.path.replace("/api/v3/brokerage", "")
        if p in ("/products", "/market/products"):
            return httpx.Response(200, json={"products": PRODUCTS})
        if p == "/market/product_book":
            pid = req.url.params["product_id"]
            bid, ask = self.book[pid]
            return httpx.Response(200, json={"pricebook": {"product_id": pid,
                                  "bids": [{"price": str(bid), "size": "5"}], "asks": [{"price": str(ask), "size": "5"}]}})
        if p == "/key_permissions":
            return httpx.Response(200, json=self.perms)
        if p == "/transaction_summary":
            return httpx.Response(200, json={"fee_tier": self.fees})
        if p == "/accounts":
            return httpx.Response(200, json={"accounts": [{"currency": c, "available_balance": {"value": v, "currency": c}} for c, v in self.balances.items()], "has_next": False})
        if p == "/orders/preview":
            return httpx.Response(200, json={"errs": []})
        if p == "/orders" and req.method == "POST":
            body = json.loads(req.content)
            self.created.append(body)
            return httpx.Response(200, json={"success": True, "success_response": {"order_id": f"ex-{len(self.created)}", "client_order_id": body["client_order_id"]}})
        if p.startswith("/orders/historical/"):
            return httpx.Response(200, json={"order": {"status": "FILLED", "filled_size": "0.0002", "average_filled_price": "60010", "total_fees": "0.05"}})
        return httpx.Response(404, json={"error": "not mocked", "path": p})


@pytest.fixture
def fake_cb():
    from app import market
    from app.coinbase.auth import CdpCredentials
    from app.coinbase.client import CoinbaseClient

    fake = FakeCoinbase()
    creds = CdpCredentials("organizations/test/apiKeys/testkey", ec_pem())
    client = CoinbaseClient("https://api.coinbase.com/api/v3/brokerage", creds, transport=httpx.MockTransport(fake.handler))
    market.set_client(client)
    market.sync_products()
    now = datetime.now(timezone.utc)
    for pid, (bid, ask) in fake.book.items():
        market.store_quote(pid, bid, ask, (bid + ask) / 2, now, "test")
    return fake


@pytest.fixture
def public_cb():
    """Cliente sin credenciales (sólo endpoints públicos) → comisiones estimadas conservadoras."""
    from app import market
    from app.coinbase.client import CoinbaseClient

    fake = FakeCoinbase()
    market.set_client(CoinbaseClient("https://api.coinbase.com/api/v3/brokerage", None, transport=httpx.MockTransport(fake.handler)))
    market.sync_products()
    now = datetime.now(timezone.utc)
    for pid, (bid, ask) in fake.book.items():
        market.store_quote(pid, bid, ask, (bid + ask) / 2, now, "test")
    return fake
