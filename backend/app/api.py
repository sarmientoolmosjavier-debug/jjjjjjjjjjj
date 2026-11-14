"""API REST del panel. uvicorn app.api:app"""
from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select

from . import activation, costs, engine, market, portfolio, services, state
from .audit import audit, setup_logging
from .coinbase.client import CoinbaseError
from .config import get_settings
from .db import init_engine, session_scope
from .models import AuditLog, Decision, Order, Position, Product, Signal, Snapshot, Trader
from .sources.base import RawSignal

@asynccontextmanager
async def lifespan(_app):
    setup_logging()
    init_engine()
    if state.get_value("mode") is None:
        state.set_mode("simulation")
    yield


app = FastAPI(title="Coinbase Smart Copy Trader", version="1.0.0", lifespan=lifespan)


app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_list, allow_methods=["*"], allow_headers=["*"])


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    tok = get_settings().admin_token
    if tok is not None and not hmac.compare_digest((x_admin_token or "").encode(), tok.get_secret_value().encode()):
        raise HTTPException(401, "Token de administración requerido")


def plain(x: Decimal | None) -> str | None:
    """Decimal sin ceros de relleno ni notación científica (1.000000000000 → 1, 1E-8 → 0.00000001)."""
    return format(x.normalize(), "f") if x is not None else None


def d(x: Decimal | None, q: str = "0.01") -> str | None:
    return str(x.quantize(Decimal(q))) if x is not None else None


# ------------------------------------------------------------------ resumen
@app.get("/api/health")
def health() -> dict:
    hb = state.get_value("worker_heartbeat")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds() if hb else None
    return {"ok": True, "worker_alive": age is not None and age < 120, "worker_heartbeat_age_s": age}


@app.get("/api/summary")
def summary() -> dict:
    mode = state.get_mode()
    lm = services.ledger_mode(mode)
    with session_scope() as s:
        prices = {}
        for p in portfolio.positions(s, lm):
            q = market.get_quote(p.product_id)
            if q:
                prices[p.product_id] = q.bid
        v = portfolio.valuation(s, lm, prices)
        w = v["wallet"]
        pnl = v["equity"] - w.start_equity
        last = s.scalar(select(Snapshot).where(Snapshot.mode == lm).order_by(Snapshot.ts.desc()).limit(1))
        pos = [{
            "product_id": p.product_id, "trader_id": p.trader_id, "base_qty": plain(p.base_qty), "cost_eur": d(p.cost_eur),
            "value_eur": d(p.base_qty * prices[p.product_id]) if p.product_id in prices else None,
        } for p in portfolio.positions(s, lm)]
        return {
            "mode": mode, "ledger": lm,
            "emergency_stop": state.emergency_stopped(),
            "drawdown_suspended": state.drawdown_suspended(lm),
            "initial_capital": d(get_settings().initial_capital_eur),
            "start_equity": d(w.start_equity),
            "cash": d(w.cash_eur), "reserved": d(w.reserved_eur), "invested": d(v["invested"]), "equity": d(v["equity"]),
            "pnl_eur": d(pnl), "pnl_pct": d(pnl / w.start_equity * 100 if w.start_equity else Decimal(0)),
            "realized_pnl": d(w.realized_pnl), "fees_total": d(w.fees_total, "0.0001"),
            "btc_hold": d(last.btc_hold) if last and last.btc_hold else None, "cash_hold": d(w.start_equity),
            "positions": pos, "missing_prices": v["missing_prices"],
            "simulated": lm == "simulation",
        }


@app.get("/api/history")
def history(limit: int = 2000) -> list[dict]:
    lm = services.ledger_mode(state.get_mode())
    with session_scope() as s:
        rows = list(s.scalars(select(Snapshot).where(Snapshot.mode == lm).order_by(Snapshot.ts.desc()).limit(limit)))
    return [{"ts": r.ts.isoformat(), "equity": d(r.equity), "cash": d(r.cash), "invested": d(r.invested),
             "btc_hold": d(r.btc_hold), "cash_hold": d(r.cash_hold)} for r in reversed(rows)]


# ------------------------------------------------------------------ traders
class TraderIn(BaseModel):
    source: str = "hyperliquid"
    external_id: str
    label: str = ""


def _trader_dict(t: Trader) -> dict:
    m = t.metrics or {}
    return {"id": t.id, "source": t.source, "external_id": t.external_id, "label": t.label, "enabled": t.enabled,
            "verified": t.verified, "score": float(t.score) if t.score is not None else None, "metrics": m,
            "notes": t.evaluation_notes, "last_evaluated_at": t.last_evaluated_at.isoformat() if t.last_evaluated_at else None}


@app.get("/api/traders")
def list_traders() -> list[dict]:
    with session_scope() as s:
        ts = list(s.scalars(select(Trader)))
    return sorted((_trader_dict(t) for t in ts), key=lambda x: -(x["score"] or -1))


@app.post("/api/traders", dependencies=[Depends(require_admin)])
def add_trader(body: TraderIn) -> dict:
    if body.source not in ("hyperliquid", "manual"):
        raise HTTPException(400, "Fuente no soportada")
    ext = body.external_id.strip()
    if body.source == "hyperliquid":
        ext = ext.lower()
        if not (ext.startswith("0x") and len(ext) == 42 and all(c in "0123456789abcdef" for c in ext[2:])):
            raise HTTPException(400, "Dirección Hyperliquid inválida (0x + 40 hex)")
    with session_scope() as s:
        if s.scalar(select(Trader).where(Trader.source == body.source, Trader.external_id == ext)):
            raise HTTPException(409, "Ya existe")
        t = Trader(source=body.source, external_id=ext, label=body.label[:128], enabled=False, verified=False, metrics={}, evaluation_notes=["Pendiente de evaluación"])
        s.add(t)
        s.flush()
        tid = t.id
    audit("trader_added", actor="user", source=body.source, external_id=ext)
    return {"id": tid}


@app.post("/api/traders/{trader_id}/evaluate", dependencies=[Depends(require_admin)])
def evaluate(trader_id: int) -> dict:
    try:
        return services.evaluate_trader(trader_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"No se pudo evaluar con datos de la fuente: {e}")


class TraderPatch(BaseModel):
    enabled: bool | None = None
    label: str | None = None


@app.patch("/api/traders/{trader_id}", dependencies=[Depends(require_admin)])
def patch_trader(trader_id: int, body: TraderPatch) -> dict:
    lim = state.get_limits()
    with session_scope() as s:
        t = s.get(Trader, trader_id)
        if t is None:
            raise HTTPException(404, "No encontrado")
        if body.enabled:
            if not t.verified or (t.score or 0) < lim.min_trader_score:
                raise HTTPException(400, "Sólo se pueden copiar traders verificados con puntuación suficiente")
            n_active = s.scalar(select(func.count()).select_from(Trader).where(Trader.enabled.is_(True), Trader.id != t.id))
            if n_active >= 3:
                raise HTTPException(400, "Máximo 3 estrategias activas")
            t.last_polled_ms = int(datetime.now(timezone.utc).timestamp() * 1000)  # no copiar operaciones pasadas
        if body.enabled is not None:
            t.enabled = body.enabled
        if body.label is not None:
            t.label = body.label[:128]
        out = _trader_dict(t)
    audit("trader_updated", actor="user", trader_id=trader_id, enabled=body.enabled)
    return out


@app.delete("/api/traders/{trader_id}", dependencies=[Depends(require_admin)])
def delete_trader(trader_id: int) -> dict:
    with session_scope() as s:
        t = s.get(Trader, trader_id)
        if t is None:
            raise HTTPException(404, "No encontrado")
        if s.scalar(select(Position).where(Position.trader_id == trader_id, Position.base_qty > 0)):
            raise HTTPException(400, "Tiene posiciones abiertas: desactívalo en lugar de borrarlo")
        if s.scalar(select(Signal).where(Signal.trader_id == trader_id)):
            t.enabled = False
            return {"deleted": False, "disabled": True}
        s.delete(t)
    return {"deleted": True}


# ------------------------------------------------------------------ operaciones y decisiones
@app.get("/api/orders")
def orders(limit: int = 200) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(Order, Trader).outerjoin(Trader, Trader.id == Order.trader_id).order_by(Order.created_at.desc()).limit(limit)).all()
        return [{
            "id": o.id, "created_at": o.created_at.isoformat(), "mode": o.mode, "trader": (t.label or t.external_id) if t else None,
            "product_id": o.product_id, "side": o.side, "style": o.style,
            "amount_eur": d((o.filled_base * o.avg_price) if o.avg_price else o.quote_size),
            "base": plain(o.filled_base or o.base_size or Decimal(0)), "price": plain(o.avg_price or o.limit_price) or "",
            "fee_eur": d(o.fee_eur, "0.0001"), "pnl_eur": d(o.realized_pnl_eur), "status": o.status, "error": o.error,
            "client_order_id": o.client_order_id,
        } for o, t in rows]


@app.get("/api/decisions")
def decisions(limit: int = 200) -> list[dict]:
    with session_scope() as s:
        rows = s.execute(select(Decision, Signal).join(Signal, Signal.id == Decision.signal_id).order_by(Decision.created_at.desc()).limit(limit)).all()
        return [{
            "id": dc.id, "created_at": dc.created_at.isoformat(), "mode": dc.mode, "accepted": dc.accepted, "reason": dc.reason,
            "message": dc.message, "details": dc.details, "asset": sg.asset, "side": sg.side, "kind": sg.kind,
            "signal_time": sg.signal_time.isoformat(), "trader_id": sg.trader_id,
        } for dc, sg in rows]


@app.get("/api/export/orders.csv")
def export_csv():
    from fastapi.responses import PlainTextResponse

    lines = ["fecha,modo,producto,lado,base,precio,importe_eur,comision_eur,resultado_eur,estado"]
    for o in orders(100000):
        lines.append(",".join(str(o[k] or "") for k in ("created_at", "mode", "product_id", "side", "base", "price", "amount_eur", "fee_eur", "pnl_eur", "status")))
    return PlainTextResponse("\n".join(lines), media_type="text/csv")


# ------------------------------------------------------------------ riesgo y costes
@app.get("/api/risk")
def risk() -> dict:
    from .risk import limits_status

    lm = services.ledger_mode(state.get_mode())
    lim = state.get_limits()
    with session_scope() as s:
        prices = {p.product_id: q.bid for p in portfolio.positions(s, lm) if (q := market.get_quote(p.product_id))}
        p = portfolio.portfolio_state(s, lm, prices)
    return {"ledger": lm, "equity": d(p.equity), "cash": d(p.cash), "invested": d(p.invested),
            "daily_pnl": d(p.daily_pnl), "drawdown": d(p.drawdown), "peak_equity": d(p.peak_equity),
            "exposure_pct": d(p.invested / p.equity * 100 if p.equity else Decimal(0)),
            "exposure_by_asset": {a: d(v) for a, v in p.exposure_by_asset.items()},
            "limits": limits_status(p, lim), "drawdown_suspended": state.drawdown_suspended(lm),
            "emergency_stop": state.emergency_stopped()}


@app.get("/api/viability")
def viability(amount: Decimal = Decimal("15")) -> dict:
    """Coste estimado ida y vuelta por activo permitido con las comisiones vigentes."""
    lim = state.get_limits()
    maker, taker, src = market.fee_rates()
    rows = []
    with session_scope() as s:
        for a in lim.assets:
            pid = f"{a}-EUR"
            prod = s.get(Product, pid)
            q = market.get_quote(pid)
            fresh = q is not None and q.age_seconds() <= get_settings().max_quote_age_seconds
            spread = q.spread_pct if q else None
            row = {"product_id": pid, "available": bool(prod and prod.tradable), "quote_fresh": fresh,
                   "quote_min_size": plain(prod.quote_min_size) if prod else None,
                   "base_increment": plain(prod.base_increment) if prod else None}
            for style in ("maker", "taker"):
                est = costs.estimate(amount, style, maker, taker, spread if spread is not None else Decimal("0.05"),
                                     get_settings().min_slippage_bps / 100, lim.max_round_trip_cost_pct, src)
                row[style] = est.as_dict()
            row["spread_assumed"] = spread is None
            rows.append(row)
    any_viable = any(r[lim.order_style]["viable"] and r["available"] for r in rows)
    return {"fee_source": src, "maker_fee_pct": plain(maker * 100), "taker_fee_pct": plain(taker * 100), "amount_eur": str(amount),
            "threshold_pct": str(lim.max_round_trip_cost_pct), "order_style": lim.order_style, "rows": rows, "any_viable": any_viable,
            "message": None if any_viable else
            "Con las comisiones y spreads actuales ninguna operación de este tamaño cumple el umbral de costes. El sistema se mantiene en simulación."}


# ------------------------------------------------------------------ configuración
@app.get("/api/settings")
def get_settings_api() -> dict:
    lim = state.get_limits()
    cfg = get_settings()
    return {"limits": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in lim.__dict__.items()},
            "mode": state.get_mode(), "live_trading_allowed": cfg.live_trading_allowed, "live_persist_auth": cfg.live_persist_auth,
            "coinbase_credentials": market.get_client().authenticated, "admin_token_required": cfg.admin_token is not None,
            "confirmation_phrase": activation.CONFIRMATION_PHRASE}


@app.put("/api/settings", dependencies=[Depends(require_admin)])
def put_settings(body: dict[str, Any]) -> dict:
    try:
        lim = state.update_limits(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit("settings_updated", actor="user", changes=body)
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in lim.__dict__.items()}


class ModeIn(BaseModel):
    mode: str


@app.post("/api/mode", dependencies=[Depends(require_admin)])
def set_mode(body: ModeIn) -> dict:
    if body.mode == "live":
        raise HTTPException(400, "El modo real se activa con /api/live/activate tras la lista de comprobaciones")
    if body.mode not in ("observation", "simulation"):
        raise HTTPException(400, "Modo inválido")
    if state.get_mode() == "live":
        activation.deactivate("Cambio manual de modo", actor="user")
    state.set_mode(body.mode)
    audit("mode_changed", actor="user", mode=body.mode)
    return {"mode": body.mode}


@app.post("/api/emergency-stop", dependencies=[Depends(require_admin)])
def emergency() -> dict:
    n = services.emergency_stop()
    if state.get_mode() == "live":
        activation.deactivate("Parada de emergencia", actor="user")
    return {"emergency_stop": True, "cancelled_orders": n}


@app.post("/api/emergency-stop/release", dependencies=[Depends(require_admin)])
def emergency_release() -> dict:
    state.set_value("emergency_stop", False)
    audit("emergency_stop_released", level="WARN", actor="user")
    return {"emergency_stop": False}


@app.post("/api/risk/review", dependencies=[Depends(require_admin)])
def review_drawdown() -> dict:
    """El usuario revisa la suspensión por drawdown: se reanuda y el pico se reinicia al patrimonio actual."""
    lm = services.ledger_mode(state.get_mode())
    with session_scope() as s:
        prices = engine.current_prices(s, lm)
        v = portfolio.valuation(s, lm, prices)
        v["wallet"].peak_equity = v["equity"]
    state.set_value(f"drawdown_suspended:{lm}", False)
    audit("drawdown_reviewed", level="WARN", actor="user", ledger=lm)
    return {"ok": True}


@app.post("/api/simulation/reset", dependencies=[Depends(require_admin)])
def reset_simulation() -> dict:
    from .models import Wallet

    with session_scope() as s:
        for o in s.scalars(select(Order).where(Order.mode == "simulation")):
            s.delete(o)
        for p in s.scalars(select(Position).where(Position.mode == "simulation")):
            s.delete(p)
        for sn in s.scalars(select(Snapshot).where(Snapshot.mode == "simulation")):
            s.delete(sn)
        w = s.get(Wallet, "simulation")
        if w:
            s.delete(w)
    state.set_value("drawdown_suspended:simulation", False)
    audit("simulation_reset", actor="user")
    return {"ok": True}


# ------------------------------------------------------------------ Coinbase y modo real
@app.get("/api/coinbase/status")
def coinbase_status() -> dict:
    c = market.get_client()
    out: dict[str, Any] = {"credentials": c.authenticated, "products_synced_at": state.get_value("products_synced_at"),
                           "public_api_ok": state.get_value("coinbase_public_ok")}
    if c.authenticated:
        try:
            perms = c.key_permissions()
            out["permissions"] = {k: perms.get(k) for k in ("can_view", "can_trade", "can_transfer", "portfolio_type")}
            bal = c.balances()
            out["balances"] = {k: str(v) for k, v in bal.items() if v > 0}
        except CoinbaseError as e:
            out["error"] = str(e)
    with session_scope() as s:
        out["products"] = [{"product_id": p.product_id, "tradable": p.tradable, "quote_min_size": plain(p.quote_min_size),
                            "base_increment": plain(p.base_increment), "base_min_size": plain(p.base_min_size)}
                           for p in s.scalars(select(Product).where(Product.product_id.in_([f"{a}-EUR" for a in state.get_limits().assets])))]
    maker, taker, src = market.fee_rates()
    out["fees"] = {"maker": str(maker), "taker": str(taker), "source": src}
    return out


@app.get("/api/live/checklist")
def live_checklist() -> dict:
    items = activation.checklist()
    ok, why = activation.live_authorized()
    return {"items": items, "all_ok": all(i["ok"] for i in items), "authorized": ok, "authorization": why}


class ActivateIn(BaseModel):
    confirmation: str


@app.post("/api/live/activate", dependencies=[Depends(require_admin)])
def live_activate(body: ActivateIn) -> dict:
    return activation.activate(body.confirmation)


# ------------------------------------------------------------------ señales propias (webhook firmado)
@app.post("/api/signals/webhook")
async def webhook(request: Request, x_signature: str | None = Header(default=None)) -> dict:
    secret = get_settings().webhook_secret
    if secret is None:
        raise HTTPException(404, "Webhook desactivado (define WEBHOOK_SECRET)")
    body = await request.body()
    expected = hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    if not x_signature or not hmac.compare_digest(expected, x_signature):
        raise HTTPException(401, "Firma inválida")
    data = json.loads(body)
    try:
        side = data["side"].upper()
        assert side in ("BUY", "SELL")
        asset = data["asset"].upper()
        ts = datetime.fromtimestamp(float(data["timestamp"]), tz=timezone.utc)
        frac = Decimal(str(data["fraction"]))
        assert 0 < frac <= 1
        sid = str(data["id"])[:100]
    except (KeyError, AssertionError, ValueError, ArithmeticError):
        raise HTTPException(400, "Campos: id, trader, asset, side (BUY/SELL), fraction (0-1], timestamp (unix)")
    q = market.get_quote(f"{asset}-EUR")
    with session_scope() as s:
        t = s.scalar(select(Trader).where(Trader.source == "manual", Trader.external_id == str(data.get("trader", ""))))
        if t is None:
            raise HTTPException(404, "Trader manual no registrado")
        sig = engine.ingest_signal(s, t, RawSignal(external_id=f"manual:{t.external_id}:{sid}", asset=asset, side=side, kind="manual",
                                                    price=q.mid if q else Decimal(0), size=Decimal(0), fraction=frac, time=ts, raw={"payload": data}))
    return {"accepted": sig is not None, "duplicate": sig is None}


@app.get("/api/audit")
def audit_log(limit: int = 200) -> list[dict]:
    with session_scope() as s:
        rows = list(s.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)))
    return [{"ts": r.ts.isoformat(), "level": r.level, "actor": r.actor, "event": r.event, "details": r.details} for r in rows]
