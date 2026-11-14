"""Activación del modo real: lista de comprobaciones, confirmación expresa y autorización por arranque.

* El modo real requiere ``LIVE_TRADING_ALLOWED=true`` en el entorno (interruptor maestro).
* La autorización queda ligada al identificador de arranque del *worker*. Si el worker se reinicia,
  el identificador cambia y el sistema vuelve a simulación automáticamente.
* Sólo si ``LIVE_PERSIST_AUTH=true`` se acepta una autorización persistente: firmada con HMAC
  (``APP_SECRET``), ligada a la huella de la clave API y con caducidad (``LIVE_PERSIST_AUTH_HOURS``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from . import costs, market, state
from .audit import audit
from .coinbase.client import CoinbaseError
from .config import get_settings
from .db import session_scope
from .models import AuditLog, Position, Product, Wallet

CONFIRMATION_PHRASE = "ACTIVO LA OPERATIVA REAL CON DINERO REAL"


def _sign(payload: dict) -> str:
    key = get_settings().app_secret.get_secret_value().encode()
    return hmac.new(key, json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def checklist() -> list[dict]:
    cfg = get_settings()
    client = market.get_client()
    items: list[dict] = []

    def add(key, ok, msg):
        items.append({"key": key, "ok": bool(ok), "message": msg})

    add("master_switch", cfg.live_trading_allowed, "LIVE_TRADING_ALLOWED=true en el entorno" if cfg.live_trading_allowed else "LIVE_TRADING_ALLOWED no está activado en el entorno")
    if not client.authenticated:
        add("credentials", False, "No hay claves CDP configuradas")
        return items
    add("credentials", True, "Claves CDP cargadas desde el entorno")
    balances = None
    try:
        balances = client.balances()
        add("connection", True, "Conexión y lectura de saldos correctas")
    except CoinbaseError as e:
        add("connection", False, f"Error de conexión: {e}")
    try:
        perms = client.key_permissions()
        add("perm_trade", perms.get("can_trade") is True, "La clave tiene permiso de trading" if perms.get("can_trade") else "La clave no tiene permiso 'trade'")
        add("perm_no_transfer", perms.get("can_transfer") is False, "La clave NO permite transferencias/retiradas" if perms.get("can_transfer") is False else "La clave permite transferencias: crea otra sólo con 'view' y 'trade'")
    except CoinbaseError as e:
        add("perm_trade", False, f"No se pudieron comprobar permisos: {e}")
    # Pruebas de órdenes: preview (no ejecuta) de una compra mínima en cada activo permitido
    lim = state.get_limits()
    preview_ok, preview_msgs = True, []
    maker, taker, src = market.fee_rates()
    viable_any = False
    with session_scope() as s:
        for asset in lim.assets:
            pid = f"{asset}-EUR"
            p = s.get(Product, pid)
            if p is None or not p.tradable:
                preview_msgs.append(f"{pid}: no negociable")
                continue
            q = market.fresh_quote(pid) or market.refresh_quote_rest(pid)
            if q is None:
                preview_ok = False
                preview_msgs.append(f"{pid}: sin precio reciente")
                continue
            amt = max(lim.min_trade_eur, p.quote_min_size)
            try:
                cfg_order = client.order_configuration("taker", "BUY", costs.floor_to(amt, p.quote_increment), None, None)
                pv = client.preview_order(pid, "BUY", cfg_order)
                if pv.get("errs"):
                    preview_ok = False
                    preview_msgs.append(f"{pid}: {pv['errs']}")
            except CoinbaseError as e:
                preview_ok = False
                preview_msgs.append(f"{pid}: {e}")
            est = costs.estimate(amt, lim.order_style, maker, taker, q.spread_pct, cfg.min_slippage_bps / 100, lim.max_round_trip_cost_pct, src)
            viable_any = viable_any or est.viable
    add("order_preview", preview_ok, "Órdenes de prueba (preview) aceptadas" + (f" ({'; '.join(preview_msgs)})" if preview_msgs else "") if preview_ok else "; ".join(preview_msgs))
    add("fees_real", src == "coinbase", "Comisiones reales leídas de la cuenta" if src == "coinbase" else "No se pudieron leer las comisiones reales")
    add("economic_viability", viable_any, "Al menos un activo permitido pasa el umbral de costes" if viable_any else
        f"Con comisiones maker {maker*100:.2f}% / taker {taker*100:.2f}% ningún activo pasa el umbral de {lim.max_round_trip_cost_pct}%: se mantiene la simulación")
    # Reconciliación: el libro interno del modo real debe estar cubierto por los saldos reales
    rec_ok, rec_msgs = balances is not None, []
    if balances is not None:
        with session_scope() as s:
            w = s.get(Wallet, "live")
            if w is not None and balances.get("EUR", Decimal(0)) + Decimal("0.01") < w.cash_eur:
                rec_ok = False
                rec_msgs.append(f"EUR en Coinbase {balances.get('EUR')} < efectivo contable {w.cash_eur}")
            if w is None and balances.get("EUR", Decimal(0)) < lim.min_cash_reserve_eur + lim.min_trade_eur:
                rec_ok = False
                rec_msgs.append("Saldo EUR insuficiente para operar con reserva")
            for p in s.scalars(select(Position).where(Position.mode == "live", Position.base_qty > 0)):
                asset = p.product_id.split("-")[0]
                if balances.get(asset, Decimal(0)) < p.base_qty:
                    rec_ok = False
                    rec_msgs.append(f"{asset}: saldo real < posición contable")
    add("reconciliation", rec_ok, "Saldos reconciliados" if rec_ok else "; ".join(rec_msgs) or "No reconciliado")
    with session_scope() as s:
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        crit = s.scalar(select(AuditLog).where(AuditLog.level == "CRITICAL", AuditLog.ts >= since, AuditLog.event != "live_deactivated"))
        add("no_critical_errors", crit is None, "Sin errores críticos en 24 h" if crit is None else f"Error crítico reciente: {crit.event}")
    hb = state.get_value("worker_heartbeat")
    alive = hb and (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds() < 120
    add("worker_alive", alive, "Servicio de ejecución activo" if alive else "El worker no está en marcha")
    return items


def activate(confirmation: str, actor: str = "user") -> dict:
    items = checklist()
    failed = [i for i in items if not i["ok"]]
    if failed:
        audit("live_activation_refused", level="WARN", actor=actor, failed=[i["key"] for i in failed])
        return {"activated": False, "checklist": items}
    if confirmation.strip() != CONFIRMATION_PHRASE:
        return {"activated": False, "checklist": items, "error": "Frase de confirmación incorrecta"}
    cfg = get_settings()
    client = market.get_client()
    now = datetime.now(timezone.utc)
    payload = {
        "boot_id": state.get_value("worker_boot_id"),
        "key_fp": client.creds.fingerprint,
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=cfg.live_persist_auth_hours)).isoformat(),
    }
    state.set_value("live_authorization", {**payload, "sig": _sign(payload)})
    with session_scope() as s:
        if s.get(Wallet, "live") is None:
            eur = client.balances().get("EUR", Decimal(0))
            cap = min(eur, cfg.initial_capital_eur)
            from zoneinfo import ZoneInfo

            s.add(Wallet(mode="live", cash_eur=cap, start_equity=cap, peak_equity=cap, day_start_equity=cap,
                         day=now.astimezone(ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d"), fees_total=0, realized_pnl=0, reserved_eur=0))
    state.set_mode("live")
    audit("live_activated", level="WARN", actor=actor, key_fp=payload["key_fp"])
    return {"activated": True, "checklist": items}


def live_authorized() -> tuple[bool, str]:
    cfg = get_settings()
    if not cfg.live_trading_allowed:
        return False, "Interruptor maestro desactivado"
    auth = state.get_value("live_authorization")
    if not auth:
        return False, "Sin autorización"
    payload = {k: auth[k] for k in ("boot_id", "key_fp", "issued_at", "expires_at")}
    if not hmac.compare_digest(_sign(payload), auth.get("sig", "")):
        return False, "Firma de autorización inválida"
    client = market.get_client()
    if not client.authenticated or client.creds.fingerprint != auth["key_fp"]:
        return False, "La clave API ha cambiado"
    if auth["boot_id"] == state.get_value("worker_boot_id"):
        return True, "Autorizado en este arranque"
    if cfg.live_persist_auth and datetime.fromisoformat(auth["expires_at"]) > datetime.now(timezone.utc):
        return True, "Autorización persistente vigente"
    return False, "El servicio se reinició: el modo real requiere una nueva autorización"


def deactivate(reason: str, actor: str = "engine") -> None:
    state.set_value("live_authorization", None)
    if state.get_mode() == "live":
        state.set_mode("simulation")
    audit("live_deactivated", level="WARN", actor=actor, reason=reason)
