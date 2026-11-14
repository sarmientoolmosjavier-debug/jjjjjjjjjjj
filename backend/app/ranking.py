"""Evaluación y puntuación de traders (0–100) a partir de datos verificables.

Funciones puras (``compute_metrics``, ``score``) separadas de la obtención de datos para poder
probarlas y para que el backtest pueda usarlas con datos anteriores a la fecha de corte
(evita sesgo de anticipación y de supervivencia).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean, pstdev

# Requisitos mínimos para considerar a un trader "verificado y copiable"
MIN_HISTORY_DAYS = 90
MIN_TRADES = 20
MAX_DRAWDOWN_PCT = 40.0
MIN_REPLICABLE_PCT = 30.0
MAX_TRADES_PER_DAY = 3.0  # con 100 € y 1–2 % por ciclo, más frecuencia destruye la rentabilidad


@dataclass
class Metrics:
    data_source: str
    history_days: float = 0
    total_return_pct: float = 0
    max_drawdown_pct: float = 0
    sharpe: float = 0
    monthly_positive_pct: float = 0
    months: int = 0
    trades: int = 0
    trades_per_day: float = 0
    replicable_pct: float = 0
    fee_pct_of_volume: float = 0
    leverage: float | None = None
    risk_level: str = "desconocido"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def equity_from_pnl(points: list[tuple[int, float, float]]) -> list[tuple[int, float]]:
    """points = [(ts_ms, account_value, cumulative_pnl)] → índice de patrimonio que ignora depósitos/retiradas."""
    idx, out = 1.0, []
    prev = None
    for ts, av, pnl in points:
        if prev is not None:
            _, pav, ppnl = prev
            if pav > 0:
                r = (pnl - ppnl) / pav
                r = max(r, -0.99)
                idx *= 1 + r
        out.append((ts, idx))
        prev = (ts, av, pnl)
    return out


def compute_metrics(points: list[tuple[int, float, float]], fills: list[dict], replicable_notional: float, total_notional: float, leverage: float | None, source: str) -> Metrics:
    m = Metrics(data_source=source)
    if len(points) < 2:
        m.notes.append("Historial de PnL insuficiente")
        return m
    eq = equity_from_pnl(points)
    m.history_days = (eq[-1][0] - eq[0][0]) / 86_400_000
    m.total_return_pct = (eq[-1][1] - 1) * 100
    peak, mdd = eq[0][1], 0.0
    for _, v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak > 0 else 0)
    m.max_drawdown_pct = mdd * 100
    rets = [eq[i][1] / eq[i - 1][1] - 1 for i in range(1, len(eq)) if eq[i - 1][1] > 0]
    if len(rets) >= 2 and pstdev(rets) > 0:
        avg_dt_days = max(m.history_days / len(rets), 1e-6)
        m.sharpe = mean(rets) / pstdev(rets) * math.sqrt(365 / avg_dt_days)
    monthly: dict[str, list[float]] = defaultdict(list)
    for ts, v in eq:
        monthly[datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m")].append(v)
    keys = sorted(monthly)
    mret = []
    prev_end = eq[0][1]
    for k in keys:
        end = monthly[k][-1]
        mret.append(end / prev_end - 1 if prev_end > 0 else 0)
        prev_end = end
    m.months = len(mret)
    m.monthly_positive_pct = 100 * sum(1 for r in mret if r > 0) / len(mret) if mret else 0
    orders = {(f.get("oid"), f.get("dir")) for f in fills}
    m.trades = len(orders)
    span_days = max((max(f["time"] for f in fills) - min(f["time"] for f in fills)) / 86_400_000, 1) if fills else 1
    m.trades_per_day = m.trades / span_days if fills else 0
    m.replicable_pct = 100 * replicable_notional / total_notional if total_notional > 0 else 0
    fees = sum(abs(float(f.get("fee", 0))) for f in fills)
    m.fee_pct_of_volume = 100 * fees / total_notional if total_notional > 0 else 0
    m.leverage = leverage
    if leverage is None:
        m.risk_level = "desconocido"
    elif leverage <= 1.5 and m.max_drawdown_pct <= 20:
        m.risk_level = "bajo"
    elif leverage <= 3 and m.max_drawdown_pct <= 35:
        m.risk_level = "moderado"
    else:
        m.risk_level = "alto"
    return m


def score(m: Metrics) -> tuple[float, bool, list[str]]:
    """Puntuación 0–100 y si pasa los filtros de verificación/copiabilidad (con motivos)."""
    notes = list(m.notes)
    s = 0.0
    s += 25 * min(max(m.sharpe, 0), 3) / 3
    s += 20 * max(0.0, 1 - m.max_drawdown_pct / 50)
    s += 15 * m.monthly_positive_pct / 100
    s += 10 * min(m.history_days / 365, 1)
    s += 10 * min(m.trades / 60, 1)
    s += 15 * min(m.replicable_pct / 100, 1)
    s += 5 * (1 if m.trades_per_day <= MAX_TRADES_PER_DAY else max(0, 1 - (m.trades_per_day - MAX_TRADES_PER_DAY) / 10))
    if m.risk_level == "alto":
        s *= 0.8
    ok = True
    checks = [
        (m.history_days >= MIN_HISTORY_DAYS, f"Historial < {MIN_HISTORY_DAYS} días"),
        (m.trades >= MIN_TRADES, f"Menos de {MIN_TRADES} operaciones observadas"),
        (m.max_drawdown_pct <= MAX_DRAWDOWN_PCT, f"Drawdown > {MAX_DRAWDOWN_PCT:.0f} %"),
        (m.replicable_pct >= MIN_REPLICABLE_PCT, f"Menos del {MIN_REPLICABLE_PCT:.0f} % del volumen es replicable en Coinbase spot"),
        (m.total_return_pct > 0, "Rentabilidad histórica no positiva"),
        (m.trades_per_day <= MAX_TRADES_PER_DAY * 2, "Frecuencia de operaciones incompatible con las comisiones"),
    ]
    for passed, why in checks:
        if not passed:
            ok = False
            notes.append(why)
    return round(max(0.0, min(100.0, s)), 1), ok, notes


# ------------------------------------------------------------------ obtención (Hyperliquid)
def evaluate_hyperliquid(client, user: str, allowed_assets: list[str], now_ms: int, lookback_days: int = 180) -> tuple[Metrics, float, bool, list[str]]:
    from .sources.hyperliquid import normalize_asset

    pf = client.portfolio(user)
    all_time = pf.get("allTime") or {}
    av = {int(t): float(v) for t, v in all_time.get("accountValueHistory", [])}
    pnl = {int(t): float(v) for t, v in all_time.get("pnlHistory", [])}
    pts = sorted((t, av[t], pnl[t]) for t in av.keys() & pnl.keys())
    fills = client.fills_since(user, now_ms - lookback_days * 86_400_000, now_ms)
    names = client.spot_names()
    rep, total = 0.0, 0.0
    for f in fills:
        notional = float(f["px"]) * float(f["sz"])
        total += notional
        asset, is_spot = normalize_asset(f["coin"], names)
        long_side = f.get("dir") in ("Buy", "Sell", "Open Long", "Close Long")
        if asset in allowed_assets and long_side:
            rep += notional
    lev = client.leverage(user)
    m = compute_metrics(pts, fills, rep, total, float(lev) if lev is not None else None, "hyperliquid_info_api (on-chain, dirección pública)")
    m.notes.append("Historial de una dirección on-chain: puede no ser el historial completo del inversor")
    sc, ok, notes = score(m)
    return m, sc, ok, notes


def evaluate_round_trips(round_trips: list[tuple[datetime, datetime, float]], source: str) -> tuple[Metrics, float, bool, list[str]]:
    """Para señales propias: [(apertura, cierre, rentabilidad_fraccional)] registradas por el sistema."""
    m = Metrics(data_source=source)
    if not round_trips:
        m.notes.append("Sin operaciones cerradas registradas")
        sc, ok, notes = score(m)
        return m, sc, False, notes
    rts = sorted(round_trips, key=lambda r: r[1])
    pts, cum, av = [], 0.0, 1.0
    pts.append((int(rts[0][0].timestamp() * 1000), 1.0, 0.0))
    for _, closed, r in rts:
        cum += av * r
        av *= 1 + r
        pts.append((int(closed.timestamp() * 1000), av, cum))
    fills = [{"oid": i, "dir": "rt", "time": int(c.timestamp() * 1000)} for i, (_, c, _) in enumerate(rts)]
    m = compute_metrics(pts, fills, 1.0, 1.0, 1.0, source)
    sc, ok, notes = score(m)
    return m, sc, ok, notes


def decimal_or_none(x: float | None) -> Decimal | None:
    return Decimal(str(round(x, 2))) if x is not None else None
