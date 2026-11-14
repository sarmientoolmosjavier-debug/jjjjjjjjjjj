"""Motor de copy trading: convierte señales en decisiones registradas y, si procede, en órdenes."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import costs, market, portfolio, state
from .audit import audit
from .models import Decision, Order, Position, Product, Signal, Trader
from .risk import capacity_for_buy

ORDER_NS = uuid.UUID("6f1c3a52-8f0e-4d55-9a57-6b2f0f6d2c11")
ZERO = Decimal(0)


def client_order_id(signal_id: int, mode: str) -> str:
    """Determinista: la misma señal en el mismo modo produce siempre el mismo id → sin duplicados."""
    return str(uuid.uuid5(ORDER_NS, f"{signal_id}:{mode}"))


def current_prices(s: Session, mode: str) -> dict[str, Decimal]:
    prices = {}
    for p in portfolio.positions(s, mode):
        q = market.fresh_quote(p.product_id) or market.refresh_quote_rest(p.product_id)
        if q:
            prices[p.product_id] = q.bid
    return prices


class Rejected(Exception):
    def __init__(self, reason: str, message: str):
        self.reason, self.message = reason, message


def process_signal(s: Session, sig: Signal, mode: str, broker, now: datetime | None = None) -> Decision:
    now = now or datetime.now(timezone.utc)
    lim = state.get_limits()
    details: dict = {"kind": sig.kind, "trader_price": str(sig.trader_price)}
    product_id = f"{sig.asset}-EUR"
    try:
        if state.emergency_stopped():
            raise Rejected("emergency_stop", "Parada de emergencia activa")
        # 1. Identidad y fuente del trader
        trader = s.get(Trader, sig.trader_id)
        if trader is None or trader.source != sig.source:
            raise Rejected("unknown_trader", "Trader o fuente no reconocidos")
        details["trader"] = trader.label or trader.external_id
        pos = s.scalar(select(Position).where(Position.mode == mode, Position.trader_id == trader.id, Position.product_id == product_id))
        if sig.side == "BUY":
            if not trader.enabled:
                raise Rejected("trader_disabled", "Copia desactivada para este trader")
            if not trader.verified or (trader.score or ZERO) < lim.min_trader_score:
                raise Rejected("trader_not_verified", f"Trader no verificado o puntuación < {lim.min_trader_score}")
        elif pos is None or pos.base_qty <= 0:
            raise Rejected("no_position", "Venta del trader sin posición copiada que cerrar")
        # 2. Antigüedad de la señal (las ventas se siguen aunque lleguen tarde: reducen riesgo)
        st = sig.signal_time if sig.signal_time.tzinfo else sig.signal_time.replace(tzinfo=timezone.utc)
        age = (now - st).total_seconds()
        details["signal_age_s"] = round(age, 1)
        if sig.side == "BUY" and age > lim.max_signal_age_seconds:
            raise Rejected("stale_signal", f"Señal con {age:.0f}s de antigüedad (máx. {lim.max_signal_age_seconds}s)")
        # 3. El activo existe y es negociable en Coinbase
        product: Product | None = s.get(Product, product_id)
        if sig.side == "BUY" and sig.asset not in lim.assets:
            raise Rejected("asset_not_allowed", f"{sig.asset} no está en la lista de activos permitidos")
        if product is None or not product.tradable:
            raise Rejected("product_unavailable", f"{product_id} no disponible/negociable en Coinbase")
        if sig.side == "BUY" and product.new_listing:
            raise Rejected("new_listing", "Activo recién listado sin historial suficiente")
        # 4. Precio actual (con control de antigüedad)
        quote = market.fresh_quote(product_id) or market.refresh_quote_rest(product_id)
        if quote is None:
            raise Rejected("stale_market_data", "Sin precio reciente: no se opera con datos incompletos o retrasados")
        details.update(bid=str(quote.bid), ask=str(quote.ask), quote_age_s=round(quote.age_seconds(now), 1))
        # 5. Spread
        spread = quote.spread_pct
        details["spread_pct"] = str(spread.quantize(Decimal("0.0001")))
        if sig.side == "BUY" and spread > lim.max_spread_pct:
            raise Rejected("spread_too_wide", f"Spread {spread:.3f}% > {lim.max_spread_pct}%")
        # 6. Comisiones
        maker, taker, fee_src = market.fee_rates()
        details.update(maker_fee=str(maker), taker_fee=str(taker), fee_source=fee_src)
        prices = current_prices(s, mode)
        prices[product_id] = quote.bid
        pstate = portfolio.portfolio_state(s, mode, prices)
        if sig.side == "SELL":
            return _sell(s, sig, mode, broker, pos, product, quote, taker, fee_src, lim, details, now)
        if state.drawdown_suspended(mode):
            raise Rejected("strategy_suspended", "Estrategia suspendida por drawdown: requiere revisión")
        # 7. Cantidad proporcional (sin inflar: si sale pequeña, se descarta)
        if sig.trader_fraction is None or sig.trader_fraction <= 0:
            raise Rejected("no_sizing_info", "No se conoce el tamaño relativo de la operación del trader")
        proportional = (pstate.equity * sig.trader_fraction).quantize(Decimal("0.01"))
        details["proportional_eur"] = str(proportional)
        # 8–9. Saldo y límites de riesgo
        risk = capacity_for_buy(pstate, sig.asset, lim)
        details["risk"] = risk.details
        if not risk.allowed:
            if risk.suspend_strategy and not state.drawdown_suspended(mode):
                state.set_value(f"drawdown_suspended:{mode}", True)
                audit("strategy_suspended_drawdown", level="CRITICAL", mode=mode, drawdown=str(pstate.drawdown))
            raise Rejected(risk.reason, risk.message)
        amount = min(proportional, risk.max_amount, pstate.cash).quantize(Decimal("0.01"))
        if amount < proportional:
            details["clipped_by_limits"] = True
        minimum = max(lim.min_trade_eur, product.quote_min_size)
        if amount < minimum:
            raise Rejected("below_minimum", f"Importe {amount} € < mínimo {minimum} € (no se aumenta el tamaño)")
        # 10. Viabilidad económica
        slip = market.estimate_slippage_pct(product_id, "BUY", amount, _min_slip())
        est = costs.estimate(amount, lim.order_style, maker, taker, spread, slip, lim.max_round_trip_cost_pct, fee_src)
        details["costs"] = est.as_dict()
        if not est.viable:
            raise Rejected("cost_too_high", f"Coste ida y vuelta {est.total_cost_pct:.2f}% > {lim.max_round_trip_cost_pct}%")
        # 11–12. Registro y ejecución
        if mode == "observation":
            raise Rejected("observation_mode", "Operación viable, no ejecutada (modo observación)")
        order = _new_order(s, sig, mode, product_id, "BUY", lim.order_style, quote_size=amount)
        if order is None:
            raise Rejected("duplicate", "Orden ya existente para esta señal (idempotencia)")
        broker.submit(s, order, product, quote, slip)
        details.update(order_status=order.status, client_order_id=order.client_order_id)
        return _decide(s, sig, mode, order.status not in ("REJECTED", "FAILED"), "executed" if order.status in ("FILLED", "OPEN") else order.status.lower(), order.error or "", details)
    except Rejected as r:
        return _decide(s, sig, mode, False, r.reason, r.message, details)


def _min_slip() -> Decimal:
    from .config import get_settings

    return get_settings().min_slippage_bps


def _sell(s, sig, mode, broker, pos, product, quote, taker, fee_src, lim, details, now) -> Decision:
    frac = sig.trader_fraction if sig.trader_fraction and sig.trader_fraction > 0 else Decimal(1)
    base = pos.base_qty * min(frac, Decimal(1))
    base = costs.floor_to(base, product.base_increment)
    remainder = pos.base_qty - base
    # Si el resto quedaría por debajo del mínimo, se vende todo para no dejar polvo invendible
    if remainder > 0 and (remainder < product.base_min_size or remainder * quote.bid < product.quote_min_size):
        base = costs.floor_to(pos.base_qty, product.base_increment)
    if base <= 0 or base < product.base_min_size:
        raise Rejected("below_minimum", "Cantidad a vender por debajo del mínimo del producto")
    details.update(sell_base=str(base), position_base=str(pos.base_qty))
    if mode == "observation":
        raise Rejected("observation_mode", "Venta no ejecutada (modo observación)")
    order = _new_order(s, sig, mode, product.product_id, "SELL", "taker", base_size=base)
    if order is None:
        raise Rejected("duplicate", "Orden ya existente para esta señal (idempotencia)")
    slip = market.estimate_slippage_pct(product.product_id, "SELL", base * quote.bid, _min_slip())
    broker.submit(s, order, product, quote, slip)
    details.update(order_status=order.status, client_order_id=order.client_order_id, realized_pnl=str(order.realized_pnl_eur))
    return _decide(s, sig, mode, order.status not in ("REJECTED", "FAILED"), "executed" if order.status in ("FILLED", "OPEN") else order.status.lower(), order.error or "", details)


def _new_order(s: Session, sig: Signal, mode: str, product_id: str, side: str, style: str, quote_size=None, base_size=None) -> Order | None:
    coid = client_order_id(sig.id, mode)
    if s.scalar(select(Order).where(Order.client_order_id == coid)) is not None:
        return None
    o = Order(client_order_id=coid, mode=mode, trader_id=sig.trader_id, signal_id=sig.id, product_id=product_id,
              side=side, style=style, quote_size=quote_size, base_size=base_size, status="PENDING")
    s.add(o)
    try:
        s.flush()
    except IntegrityError:
        s.rollback()
        return None
    return o


def _decide(s: Session, sig: Signal, mode: str, accepted: bool, reason: str, message: str, details: dict) -> Decision:
    d = Decision(signal_id=sig.id, mode=mode, accepted=accepted, reason=reason, message=message, details=_json(details))
    s.add(d)
    sig.processed = True
    s.flush()
    audit("decision", mode=mode, signal_id=sig.id, accepted=accepted, reason=reason, message=message)
    return d


def _json(o):
    if isinstance(o, dict):
        return {k: _json(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_json(v) for v in o]
    if isinstance(o, Decimal):
        return str(o)
    return o


def ingest_signal(s: Session, trader: Trader, raw) -> Signal | None:
    """Inserta una señal normalizada si no existe ya (dedupe por fuente + id externo)."""
    exists = s.scalar(select(Signal).where(Signal.source == trader.source, Signal.external_id == raw.external_id))
    if exists:
        return None
    sig = Signal(trader_id=trader.id, source=trader.source, external_id=raw.external_id, asset=raw.asset, side=raw.side,
                 kind=raw.kind, trader_price=raw.price, trader_size=raw.size, trader_fraction=raw.fraction,
                 signal_time=raw.time, raw=_json(raw.raw))
    s.add(sig)
    s.flush()
    return sig


def cancel_open_orders(s: Session, mode: str, client=None) -> int:
    n = 0
    for o in s.scalars(select(Order).where(Order.mode == mode, Order.status == "OPEN")):
        if mode == "live" and client is not None and o.exchange_order_id:
            try:
                client.cancel_orders([o.exchange_order_id])
            except Exception as e:  # noqa: BLE001
                audit("cancel_failed", level="CRITICAL", client_order_id=o.client_order_id, error=str(e))
                continue
        if o.side == "BUY" and o.quote_size:
            w = portfolio.get_wallet(s, mode)
            if o.style == "maker":
                w.reserved_eur -= o.quote_size
                w.cash_eur += o.quote_size
        o.status = "CANCELLED"
        o.error = "Cancelada por parada de emergencia"
        n += 1
    return n
