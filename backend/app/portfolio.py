"""Contabilidad de cartera (simulación y real): saldo, posiciones, valoración y aplicación de ejecuciones."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Order, Position, Snapshot, Wallet
from .risk import PortfolioState

MADRID = ZoneInfo("Europe/Madrid")
ZERO = Decimal(0)
DUST = Decimal("0.00000001")


def today_madrid(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(MADRID).strftime("%Y-%m-%d")


def day_start_utc(now: datetime | None = None) -> datetime:
    local = (now or datetime.now(timezone.utc)).astimezone(MADRID)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def get_wallet(s: Session, mode: str) -> Wallet:
    w = s.get(Wallet, mode)
    if w is None:
        cap = get_settings().initial_capital_eur
        w = Wallet(mode=mode, cash_eur=cap, start_equity=cap, peak_equity=cap, day_start_equity=cap, day=today_madrid(),
                   fees_total=ZERO, realized_pnl=ZERO, reserved_eur=ZERO)
        s.add(w)
        s.flush()
    return w


def positions(s: Session, mode: str) -> list[Position]:
    return list(s.scalars(select(Position).where(Position.mode == mode, Position.base_qty > 0)))


def valuation(s: Session, mode: str, prices: dict[str, Decimal]) -> dict:
    """prices: product_id → precio de liquidación (bid). Devuelve patrimonio y desglose."""
    w = get_wallet(s, mode)
    by_asset: dict[str, Decimal] = {}
    cost_by_asset: dict[str, Decimal] = {}
    missing = []
    for p in positions(s, mode):
        px = prices.get(p.product_id)
        if px is None:
            missing.append(p.product_id)
            px = p.cost_eur / p.base_qty if p.base_qty > 0 else ZERO  # sin precio: a coste (marcado)
        asset = p.product_id.split("-")[0]
        by_asset[asset] = by_asset.get(asset, ZERO) + p.base_qty * px
        cost_by_asset[asset] = cost_by_asset.get(asset, ZERO) + p.cost_eur
    # Órdenes de compra abiertas cuentan como exposición comprometida
    open_buys = s.scalars(select(Order).where(Order.mode == mode, Order.side == "BUY", Order.status == "OPEN"))
    committed: dict[str, Decimal] = {}
    for o in open_buys:
        a = o.product_id.split("-")[0]
        committed[a] = committed.get(a, ZERO) + (o.quote_size or ZERO)
    invested = sum(by_asset.values(), ZERO)
    equity = w.cash_eur + w.reserved_eur + invested
    return {"wallet": w, "by_asset": by_asset, "cost_by_asset": cost_by_asset, "committed": committed,
            "invested": invested, "equity": equity, "missing_prices": missing}


def trades_today(s: Session, mode: str) -> int:
    return s.scalar(
        select(func.count()).select_from(Order).where(
            Order.mode == mode, Order.side == "BUY", Order.status.in_(("OPEN", "FILLED", "PENDING", "UNKNOWN")),
            Order.created_at >= day_start_utc(),
        )
    ) or 0


def portfolio_state(s: Session, mode: str, prices: dict[str, Decimal]) -> PortfolioState:
    v = valuation(s, mode, prices)
    w: Wallet = v["wallet"]
    roll_day(w, v["equity"])
    w.peak_equity = max(w.peak_equity, v["equity"])
    exposure = dict(v["by_asset"])
    for a, q in v["committed"].items():
        exposure[a] = exposure.get(a, ZERO) + q
    return PortfolioState(
        cash=w.cash_eur, equity=v["equity"], peak_equity=w.peak_equity, day_start_equity=w.day_start_equity,
        exposure_by_asset={a: x for a, x in exposure.items() if x > 0},
        open_positions=len([a for a, x in exposure.items() if x > 0]),
        trades_today=trades_today(s, mode), initial_capital=get_settings().initial_capital_eur,
    )


def roll_day(w: Wallet, equity: Decimal) -> None:
    t = today_madrid()
    if w.day != t:
        w.day = t
        w.day_start_equity = equity


def apply_buy_fill(s: Session, o: Order, base: Decimal, price: Decimal, fee: Decimal, from_reserve: Decimal = ZERO) -> None:
    w = get_wallet(s, o.mode)
    cost = base * price + fee
    if from_reserve:
        w.reserved_eur -= from_reserve
        w.cash_eur += from_reserve
    w.cash_eur -= cost
    w.fees_total += fee
    pos = s.scalar(select(Position).where(Position.mode == o.mode, Position.trader_id == o.trader_id, Position.product_id == o.product_id))
    if pos is None:
        pos = Position(mode=o.mode, trader_id=o.trader_id, product_id=o.product_id, base_qty=ZERO, cost_eur=ZERO)
        s.add(pos)
    if pos.base_qty <= 0:
        pos.opened_at = datetime.now(timezone.utc)
    pos.base_qty += base
    pos.cost_eur += cost
    o.filled_base, o.avg_price, o.fee_eur = base, price, fee
    o.status, o.filled_at = "FILLED", datetime.now(timezone.utc)


def apply_sell_fill(s: Session, o: Order, base: Decimal, price: Decimal, fee: Decimal) -> None:
    w = get_wallet(s, o.mode)
    pos = s.scalar(select(Position).where(Position.mode == o.mode, Position.trader_id == o.trader_id, Position.product_id == o.product_id))
    proceeds = base * price - fee
    pnl = ZERO
    if pos is not None and pos.base_qty > 0:
        frac = min(base / pos.base_qty, Decimal(1))
        released = pos.cost_eur * frac
        pnl = proceeds - released
        pos.base_qty -= base
        pos.cost_eur -= released
        if pos.base_qty <= DUST:
            pos.base_qty, pos.cost_eur = ZERO, ZERO
    w.cash_eur += proceeds
    w.fees_total += fee
    w.realized_pnl += pnl
    o.filled_base, o.avg_price, o.fee_eur, o.realized_pnl_eur = base, price, fee, pnl
    o.status, o.filled_at = "FILLED", datetime.now(timezone.utc)


def take_snapshot(s: Session, mode: str, prices: dict[str, Decimal], btc_price: Decimal | None) -> Snapshot:
    v = valuation(s, mode, prices)
    w: Wallet = v["wallet"]
    if w.benchmark_btc_price is None and btc_price:
        w.benchmark_btc_price = btc_price
    btc_hold = (w.start_equity * btc_price / w.benchmark_btc_price) if (btc_price and w.benchmark_btc_price) else None
    w.peak_equity = max(w.peak_equity, v["equity"])
    snap = Snapshot(mode=mode, equity=v["equity"], cash=w.cash_eur + w.reserved_eur, invested=v["invested"], btc_hold=btc_hold, cash_hold=w.start_equity)
    s.add(snap)
    return snap
