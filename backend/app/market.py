"""Datos de mercado: catálogo de productos, cotizaciones con control de antigüedad, comisiones y libro."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .coinbase.auth import load_credentials
from .coinbase.client import CoinbaseClient, CoinbaseError
from .config import get_settings
from .db import session_scope
from .models import Product, Quote
from . import state

_client: CoinbaseClient | None = None


def get_client() -> CoinbaseClient:
    global _client
    if _client is None:
        cfg = get_settings()
        _client = CoinbaseClient(cfg.coinbase_rest_base, load_credentials(cfg))
    return _client


def set_client(client: CoinbaseClient | None) -> None:  # tests
    global _client
    _client = client


@dataclass
class QuoteView:
    product_id: str
    bid: Decimal
    ask: Decimal
    price: Decimal
    ts: datetime
    source: str

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2

    @property
    def spread_pct(self) -> Decimal:
        return (self.ask - self.bid) / self.mid * 100 if self.mid > 0 else Decimal("100")

    def age_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        ts = self.ts if self.ts.tzinfo else self.ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds()


def store_quote(product_id: str, bid: Decimal, ask: Decimal, price: Decimal, ts: datetime, source: str) -> None:
    if bid <= 0 or ask <= 0 or ask < bid:
        return  # dato incoherente: no se guarda
    with session_scope() as s:
        q = s.get(Quote, product_id)
        if q is None:
            s.add(Quote(product_id=product_id, bid=bid, ask=ask, price=price, ts=ts, source=source))
        else:
            q.bid, q.ask, q.price, q.ts, q.source = bid, ask, price, ts, source


def get_quote(product_id: str) -> QuoteView | None:
    with session_scope() as s:
        q = s.get(Quote, product_id)
        if q is None:
            return None
        return QuoteView(q.product_id, q.bid, q.ask, q.price, q.ts, q.source)


def fresh_quote(product_id: str) -> QuoteView | None:
    q = get_quote(product_id)
    if q is None or q.age_seconds() > get_settings().max_quote_age_seconds:
        return None
    return q


def refresh_quote_rest(product_id: str) -> QuoteView | None:
    """Fallback REST si el WebSocket no da datos recientes."""
    try:
        book = get_client().product_book(product_id, limit=1)
    except CoinbaseError:
        return None
    try:
        bid = Decimal(book["bids"][0]["price"])
        ask = Decimal(book["asks"][0]["price"])
    except (KeyError, IndexError):
        return None
    store_quote(product_id, bid, ask, (bid + ask) / 2, datetime.now(timezone.utc), "rest")
    return get_quote(product_id)


def estimate_slippage_pct(product_id: str, side: str, quote_eur: Decimal, min_bps: Decimal) -> Decimal:
    """Recorre el libro para el importe exacto. Devuelve % de deslizamiento vs. mejor precio."""
    floor = min_bps / 100  # bps → %
    try:
        book = get_client().product_book(product_id, limit=50)
    except CoinbaseError:
        return floor * 5  # sin libro: estimación prudente
    levels = book.get("asks" if side == "BUY" else "bids", [])
    if not levels:
        return floor * 5
    best = Decimal(levels[0]["price"])
    remaining, cost, qty = quote_eur, Decimal(0), Decimal(0)
    for lv in levels:
        p, sz = Decimal(lv["price"]), Decimal(lv["size"])
        take = min(remaining, p * sz)
        cost += take
        qty += take / p
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0 or qty == 0:
        return Decimal("100")  # el libro no cubre el importe
    avg = cost / qty
    slip = abs(avg - best) / best * 100
    return max(slip, floor)


# ------------------------------------------------------------------ catálogo
def sync_products() -> int:
    """Guarda productos SPOT en EUR con sus mínimos/incrementos reales."""
    prods = get_client().products()
    n = 0
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        for p in prods:
            if p.get("quote_currency_id") != "EUR" or p.get("product_type", "SPOT") != "SPOT":
                continue
            pid = p["product_id"]
            row = s.get(Product, pid) or Product(product_id=pid)
            row.base = p.get("base_currency_id", pid.split("-")[0])
            row.quote = "EUR"
            row.base_increment = Decimal(str(p.get("base_increment") or "0"))
            row.quote_increment = Decimal(str(p.get("quote_increment") or "0"))
            row.base_min_size = Decimal(str(p.get("base_min_size") or "0"))
            row.quote_min_size = Decimal(str(p.get("quote_min_size") or "0"))
            row.tradable = (
                p.get("status", "online") == "online"
                and not p.get("trading_disabled", False)
                and not p.get("is_disabled", False)
                and not p.get("cancel_only", False)
            )
            row.volume_24h = Decimal(str(p["volume_24h"])) if p.get("volume_24h") not in (None, "") else None
            row.new_listing = bool(p.get("new", False))
            row.updated_at = now
            s.merge(row)
            n += 1
    state.set_value("products_synced_at", now.isoformat())
    return n


def get_product(product_id: str) -> Product | None:
    with session_scope() as s:
        return s.get(Product, product_id)


# ------------------------------------------------------------------ comisiones
def fee_rates() -> tuple[Decimal, Decimal, str]:
    """Comisiones reales si hay claves; si no, la estimación conservadora configurada (etiquetada)."""
    client = get_client()
    cached = state.get_value("fee_rates")
    if client.authenticated and cached and cached.get("source") == "coinbase":
        ts = datetime.fromisoformat(cached["ts"])
        if datetime.now(timezone.utc) - ts < timedelta(hours=1):
            return Decimal(cached["maker"]), Decimal(cached["taker"]), "coinbase"
    if client.authenticated:
        try:
            maker, taker, tier = client.fee_rates()
            state.set_value("fee_rates", {"maker": str(maker), "taker": str(taker), "tier": tier, "source": "coinbase", "ts": datetime.now(timezone.utc).isoformat()})
            return maker, taker, "coinbase"
        except (CoinbaseError, KeyError):
            pass
    cfg = get_settings()
    return cfg.default_maker_fee, cfg.default_taker_fee, "estimacion_conservadora"
