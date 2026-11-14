"""Ejecución de órdenes: broker simulado (paper trading realista) y adaptador real de Coinbase."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import market, portfolio
from .audit import audit
from .coinbase.client import CoinbaseClient, CoinbaseError
from .config import get_settings
from .costs import floor_to
from .models import Order, Product

log = logging.getLogger("copytrader.brokers")
ZERO = Decimal(0)


class PaperBroker:
    """Simula Coinbase: comisiones reales/estimadas, spread (bid/ask), deslizamiento, mínimos e incrementos,
    y órdenes maker que sólo se ejecutan si el mercado cotiza *por debajo* del límite (sin ejecución optimista)."""

    mode = "simulation"

    def __init__(self, maker_fee: Decimal, taker_fee: Decimal):
        self.maker_fee, self.taker_fee = maker_fee, taker_fee

    def submit(self, s: Session, o: Order, product: Product, quote: market.QuoteView, slippage_pct: Decimal) -> None:
        if o.side == "BUY" and o.style == "taker":
            price = quote.ask * (1 + slippage_pct / 100)
            fee = o.quote_size * self.taker_fee
            base = floor_to((o.quote_size - fee) / price, product.base_increment)
            if base < product.base_min_size or base <= 0:
                o.status, o.error = "REJECTED", "Cantidad por debajo del mínimo del producto"
                return
            portfolio.apply_buy_fill(s, o, base, price, base * price * self.taker_fee)
        elif o.side == "BUY":  # maker post-only al mejor bid
            limit = floor_to(quote.bid, product.quote_increment) if product.quote_increment > 0 else quote.bid
            base = floor_to(o.quote_size / (limit * (1 + self.maker_fee)), product.base_increment)
            if base < product.base_min_size or base <= 0:
                o.status, o.error = "REJECTED", "Cantidad por debajo del mínimo del producto"
                return
            reserve = base * limit * (1 + self.maker_fee)
            w = portfolio.get_wallet(s, o.mode)
            w.cash_eur -= reserve
            w.reserved_eur += reserve
            o.limit_price, o.base_size, o.quote_size = limit, base, reserve
            o.status = "OPEN"
            o.expires_at = datetime.now(timezone.utc) + timedelta(seconds=get_settings().maker_order_ttl_seconds)
        else:  # SELL siempre taker
            price = quote.bid * (1 - slippage_pct / 100)
            base = floor_to(o.base_size, product.base_increment)
            if base <= 0 or base < product.base_min_size:
                o.status, o.error = "REJECTED", "Cantidad por debajo del mínimo del producto"
                return
            portfolio.apply_sell_fill(s, o, base, price, base * price * self.taker_fee)

    def poll_open(self, s: Session) -> None:
        now = datetime.now(timezone.utc)
        for o in s.scalars(select(Order).where(Order.mode == self.mode, Order.status == "OPEN")):
            q = market.fresh_quote(o.product_id)
            if q is not None and q.price < o.limit_price:  # el mercado negoció por debajo de nuestro bid
                fee = o.base_size * o.limit_price * self.maker_fee
                portfolio.apply_buy_fill(s, o, o.base_size, o.limit_price, fee, from_reserve=o.quote_size)
                audit("order_filled", mode=self.mode, client_order_id=o.client_order_id, price=o.limit_price)
            elif o.expires_at and (o.expires_at if o.expires_at.tzinfo else o.expires_at.replace(tzinfo=timezone.utc)) < now:
                w = portfolio.get_wallet(s, o.mode)
                w.reserved_eur -= o.quote_size
                w.cash_eur += o.quote_size
                o.status = "CANCELLED"
                o.error = "Orden maker no ejecutada dentro del plazo"
                audit("order_expired", mode=self.mode, client_order_id=o.client_order_id)


class LiveBroker:
    """Adaptador de órdenes reales. Sólo se instancia si el modo real está autorizado (ver activation.py)."""

    mode = "live"

    def __init__(self, client: CoinbaseClient, maker_fee: Decimal, taker_fee: Decimal):
        self.client, self.maker_fee, self.taker_fee = client, maker_fee, taker_fee

    def _config(self, o: Order, product: Product, quote: market.QuoteView) -> dict:
        if o.side == "BUY" and o.style == "maker":
            limit = floor_to(quote.bid, product.quote_increment)
            base = floor_to(o.quote_size / (limit * (1 + self.maker_fee)), product.base_increment)
            o.limit_price, o.base_size = limit, base
            o.expires_at = datetime.now(timezone.utc) + timedelta(seconds=get_settings().maker_order_ttl_seconds)
            return CoinbaseClient.order_configuration("maker", "BUY", None, base, limit)
        if o.side == "BUY":
            q = floor_to(o.quote_size, product.quote_increment)
            o.quote_size = q
            return CoinbaseClient.order_configuration("taker", "BUY", q, None, None)
        o.base_size = floor_to(o.base_size, product.base_increment)
        return CoinbaseClient.order_configuration("taker", "SELL", None, o.base_size, None)

    def submit(self, s: Session, o: Order, product: Product, quote: market.QuoteView, slippage_pct: Decimal) -> None:
        cfg = self._config(o, product, quote)
        try:
            preview = self.client.preview_order(o.product_id, o.side, cfg)
            errs = preview.get("errs") or []
            if errs:
                o.status, o.error = "REJECTED", f"Preview rechazado: {errs}"
                audit("order_preview_rejected", level="WARN", client_order_id=o.client_order_id, errs=errs)
                return
            resp = self.client.create_order(o.client_order_id, o.product_id, o.side, cfg)
        except CoinbaseError as e:
            if e.status is None:  # error de red: la orden podría existir → se reintenta con el mismo client_order_id
                o.status, o.error = "UNKNOWN", str(e)
            else:
                o.status, o.error = "FAILED", str(e)
            audit("order_submit_error", level="CRITICAL", client_order_id=o.client_order_id, error=str(e), status=e.status)
            return
        if not resp.get("success", False):
            o.status, o.error = "REJECTED", str(resp.get("error_response") or resp.get("failure_reason"))
            audit("order_rejected", level="WARN", client_order_id=o.client_order_id, error=o.error)
            return
        o.exchange_order_id = (resp.get("success_response") or {}).get("order_id") or resp.get("order_id")
        o.status = "OPEN"
        if o.side == "BUY" and o.style == "maker":
            w = portfolio.get_wallet(s, o.mode)
            reserve = o.base_size * o.limit_price * (1 + self.maker_fee)
            w.cash_eur -= reserve
            w.reserved_eur += reserve
            o.quote_size = reserve
        audit("order_submitted", mode="live", client_order_id=o.client_order_id, exchange_order_id=o.exchange_order_id)

    def poll_open(self, s: Session) -> None:
        now = datetime.now(timezone.utc)
        for o in s.scalars(select(Order).where(Order.mode == self.mode, Order.status.in_(("OPEN", "UNKNOWN")))):
            try:
                if o.status == "UNKNOWN":
                    product = s.get(Product, o.product_id)
                    q = market.fresh_quote(o.product_id)
                    if product and q:
                        self.submit(s, o, product, q, ZERO)  # mismo client_order_id → Coinbase no duplica
                    continue
                info = self.client.get_order(o.exchange_order_id)
            except CoinbaseError as e:
                log.warning("No se pudo consultar la orden %s: %s", o.client_order_id, e)
                continue
            st = info.get("status")
            if st == "FILLED":
                base = Decimal(str(info.get("filled_size", "0")))
                price = Decimal(str(info.get("average_filled_price", "0")))
                fee = Decimal(str(info.get("total_fees", "0")))
                if o.side == "BUY":
                    portfolio.apply_buy_fill(s, o, base, price, fee, from_reserve=o.quote_size if o.style == "maker" else ZERO)
                else:
                    portfolio.apply_sell_fill(s, o, base, price, fee)
                audit("order_filled", mode="live", client_order_id=o.client_order_id, price=price, fee=fee)
            elif st in ("CANCELLED", "EXPIRED", "FAILED"):
                if o.style == "maker" and o.side == "BUY":
                    w = portfolio.get_wallet(s, o.mode)
                    w.reserved_eur -= o.quote_size
                    w.cash_eur += o.quote_size
                o.status = "CANCELLED"
                audit("order_cancelled", mode="live", client_order_id=o.client_order_id, exchange_status=st)
            elif o.expires_at and (o.expires_at if o.expires_at.tzinfo else o.expires_at.replace(tzinfo=timezone.utc)) < now:
                try:
                    self.client.cancel_orders([o.exchange_order_id])
                except CoinbaseError as e:
                    log.warning("No se pudo cancelar %s: %s", o.client_order_id, e)
