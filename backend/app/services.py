"""Orquestación compartida por la API y el worker."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from . import activation, engine, market, portfolio, ranking, state
from .audit import audit, log
from .brokers import LiveBroker, PaperBroker
from .config import get_settings
from .db import session_scope
from .models import Order, Signal, Snapshot, Trader
from .sources.hyperliquid import HyperliquidClient, fills_to_signals

_hl: HyperliquidClient | None = None


def hl_client() -> HyperliquidClient:
    global _hl
    if _hl is None:
        _hl = HyperliquidClient(get_settings().hyperliquid_info_url)
    return _hl


def set_hl_client(c) -> None:  # tests
    global _hl
    _hl = c


def broker_for(mode: str):
    maker, taker, _ = market.fee_rates()
    if mode == "live":
        ok, why = activation.live_authorized()
        if not ok:
            raise RuntimeError(f"Modo real no autorizado: {why}")
        return LiveBroker(market.get_client(), maker, taker)
    return PaperBroker(maker, taker)


def execution_mode() -> str:
    """Modo con el que se ejecutan las órdenes: observation no ejecuta, pero sí evalúa."""
    return state.get_mode()


def ledger_mode(mode: str) -> str:
    return "live" if mode == "live" else "simulation"


# ------------------------------------------------------------------ traders
def evaluate_trader(trader_id: int) -> dict:
    lim = state.get_limits()
    with session_scope() as s:
        t = s.get(Trader, trader_id)
        if t is None:
            raise ValueError("Trader no encontrado")
        source, ext = t.source, t.external_id
    if source == "hyperliquid":
        m, sc, ok, notes = ranking.evaluate_hyperliquid(hl_client(), ext, lim.assets, int(time.time() * 1000))
    else:
        m, sc, ok, notes = ranking.evaluate_round_trips(manual_round_trips(trader_id), "señales propias registradas por este sistema")
    with session_scope() as s:
        t = s.get(Trader, trader_id)
        t.metrics, t.score, t.verified, t.evaluation_notes = m.as_dict(), Decimal(str(sc)), ok, notes
        t.last_evaluated_at = datetime.now(timezone.utc)
        if not ok and t.enabled:
            t.enabled = False
            audit("trader_auto_disabled", level="WARN", trader=t.external_id, notes=notes)
    audit("trader_evaluated", trader_id=trader_id, score=sc, verified=ok)
    return {"score": sc, "verified": ok, "notes": notes, "metrics": m.as_dict()}


def manual_round_trips(trader_id: int) -> list[tuple[datetime, datetime, float]]:
    with session_scope() as s:
        sigs = list(s.scalars(select(Signal).where(Signal.trader_id == trader_id).order_by(Signal.signal_time)))
    open_: dict[str, Signal] = {}
    out = []
    for sg in sigs:
        if sg.side == "BUY" and sg.asset not in open_:
            open_[sg.asset] = sg
        elif sg.side == "SELL" and sg.asset in open_:
            b = open_.pop(sg.asset)
            if b.trader_price > 0:
                out.append((b.signal_time, sg.signal_time, float(sg.trader_price / b.trader_price - 1)))
    return out


def poll_sources() -> int:
    lim = state.get_limits()
    n = 0
    with session_scope() as s:
        traders = list(s.scalars(select(Trader).where(Trader.enabled.is_(True), Trader.source == "hyperliquid")))
    for t in traders:
        now_ms = int(time.time() * 1000)
        start = t.last_polled_ms or now_ms - lim.max_signal_age_seconds * 1000
        try:
            c = hl_client()
            fills = c.fills_since(t.external_id, start, now_ms)
            av = c.account_value(t.external_id) if any(f.get("dir") in ("Buy", "Open Long") for f in fills) else None
            raws = fills_to_signals(t.external_id, fills, c.spot_names(), av, lim.hl_include_perp_longs)
        except Exception as e:  # noqa: BLE001
            audit("source_poll_failed", level="WARN", trader=t.external_id, error=str(e))
            continue
        with session_scope() as s:
            tt = s.get(Trader, t.id)
            for raw in raws:
                if engine.ingest_signal(s, tt, raw):
                    n += 1
            tt.last_polled_ms = now_ms
    return n


# ------------------------------------------------------------------ ciclo
def process_pending(mode: str | None = None) -> int:
    mode = mode or execution_mode()
    lm = ledger_mode(mode)
    broker = broker_for(lm) if mode != "observation" else None
    n = 0
    with session_scope() as s:
        pending = list(s.scalars(select(Signal).where(Signal.processed.is_(False)).order_by(Signal.signal_time)))
        for sig in pending:
            engine.process_signal(s, sig, mode if mode == "observation" else lm, broker)
            n += 1
    return n


def poll_orders() -> None:
    for lm in ("simulation", "live"):
        if lm == "live" and not activation.live_authorized()[0]:
            continue
        with session_scope() as s:
            if s.scalar(select(Order).where(Order.mode == lm, Order.status.in_(("OPEN", "UNKNOWN")))) is None:
                continue
            broker_for(lm).poll_open(s)


def snapshot(lm: str) -> None:
    btc = market.fresh_quote("BTC-EUR") or market.refresh_quote_rest("BTC-EUR")
    with session_scope() as s:
        prices = engine.current_prices(s, lm)
        portfolio.portfolio_state(s, lm, prices)  # actualiza pico y día
        portfolio.take_snapshot(s, lm, prices, btc.mid if btc else None)


def enforce_live_authorization() -> None:
    if state.get_mode() == "live":
        ok, why = activation.live_authorized()
        if not ok:
            activation.deactivate(why)


def emergency_stop(actor: str = "user") -> int:
    state.set_value("emergency_stop", True)
    n = 0
    for lm in ("simulation", "live"):
        with session_scope() as s:
            n += engine.cancel_open_orders(s, lm, market.get_client() if lm == "live" else None)
    audit("emergency_stop", level="CRITICAL", actor=actor, cancelled_orders=n)
    return n


def last_snapshot_age(lm: str) -> float:
    with session_scope() as s:
        snap = s.scalar(select(Snapshot).where(Snapshot.mode == lm).order_by(Snapshot.ts.desc()).limit(1))
        if snap is None:
            return 1e9
        ts = snap.ts if snap.ts.tzinfo else snap.ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()


def watched_products() -> list[str]:
    lim = state.get_limits()
    ids = {f"{a}-EUR" for a in lim.assets} | {"BTC-EUR"}
    with session_scope() as s:
        for lm in ("simulation", "live"):
            ids |= {p.product_id for p in portfolio.positions(s, lm)}
    return sorted(ids)


def stale_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=get_settings().max_quote_age_seconds)


_ = log
