"""Backtest de copia con retraso, costes y límites reales, sin sesgo de anticipación.

* **Selección** del trader sólo con datos anteriores a ``cutoff`` (evita sesgo de supervivencia
  respecto al periodo simulado: no elegimos a quien sabemos que ganó *después*).
* **Ejecución** al precio de apertura de la primera vela posterior a ``señal + retraso``
  (nunca al precio del trader ni a precios del pasado).
* Costes: comisión taker de entrada y salida, medio spread y deslizamiento en cada lado.
* Benchmarks: mantener BTC (comprado en ``cutoff`` con la misma comisión) y mantener efectivo.

Uso (requiere red):  python -m app.backtest --address 0x... --cutoff 2026-05-01 --end 2026-11-01
"""
from __future__ import annotations

import argparse
import bisect
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from . import costs, ranking
from .risk import PortfolioState, capacity_for_buy
from .sources.hyperliquid import fills_to_signals
from .state import Limits

ZERO = Decimal(0)
Candles = Callable[[str, int, int], list[tuple[int, Decimal]]]  # (ts_s, open) ordenadas


@dataclass
class BacktestResult:
    selected: bool
    selection_score: float
    selection_notes: list[str]
    start_equity: Decimal
    final_equity: Decimal
    btc_hold_final: Decimal | None
    cash_final: Decimal
    fees_paid: Decimal
    trades: list[dict] = field(default_factory=list)
    rejected: dict[str, int] = field(default_factory=dict)
    equity_curve: list[tuple[int, str]] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        for k in ("start_equity", "final_equity", "btc_hold_final", "cash_final", "fees_paid"):
            d[k] = str(d[k]) if d[k] is not None else None
        d["return_pct"] = str(((self.final_equity / self.start_equity) - 1) * 100) if self.start_equity else None
        return d


def _price_at(candles: list[tuple[int, Decimal]], ts: int) -> Decimal | None:
    keys = [c[0] for c in candles]
    i = bisect.bisect_left(keys, ts)
    if i >= len(candles) or candles[i][0] - ts > 600:
        return None  # no hay vela en los 10 min siguientes: datos incompletos → no se opera
    return candles[i][1]


def run(user: str, portfolio_points: list[tuple[int, float, float]], fills: list[dict], spot_names: dict[str, str],
        candles: Candles, cutoff_ms: int, end_ms: int, limits: Limits, maker_fee: Decimal, taker_fee: Decimal,
        delay_s: int = 60, spread_pct: Decimal = Decimal("0.05"), slippage_pct: Decimal = Decimal("0.05"),
        capital: Decimal = Decimal(100)) -> BacktestResult:
    # --- 1. Selección sólo con información anterior al corte
    pre_pts = [p for p in portfolio_points if p[0] < cutoff_ms]
    pre_fills = [f for f in fills if f["time"] < cutoff_ms]
    rep = sum(float(f["px"]) * float(f["sz"]) for f in pre_fills if f.get("dir") in ("Buy", "Sell", "Open Long", "Close Long"))
    tot = sum(float(f["px"]) * float(f["sz"]) for f in pre_fills)
    m = ranking.compute_metrics(pre_pts, pre_fills, rep, tot, None, "backtest")
    sc, ok, notes = ranking.score(m)
    res = BacktestResult(ok and sc >= float(limits.min_trader_score), sc, notes, capital, capital, None, capital, ZERO)
    btc = candles("BTC-EUR", cutoff_ms // 1000, end_ms // 1000)
    btc_p0 = btc[0][1] if btc else None
    if btc_p0:
        res.btc_hold_final = capital * (1 - taker_fee) * btc[-1][1] / btc_p0 * (1 - taker_fee)
    if not res.selected:
        return res  # el sistema habría permanecido en observación
    # --- 2. Simulación posterior al corte
    av_hist = sorted((p[0], p[1]) for p in portfolio_points)
    av_keys = [a[0] for a in av_hist]
    post = [f for f in fills if cutoff_ms <= f["time"] < end_ms]
    cache: dict[str, list] = {}
    cash, fees = capital, ZERO
    pos: dict[str, tuple[Decimal, Decimal]] = {}  # asset → (qty, coste)
    peak, day, day_start, trades_day = capital, None, capital, 0
    for sig in _signals_with_av(user, post, spot_names, av_hist, av_keys):
        pid = f"{sig.asset}-EUR"
        if pid not in cache:
            cache[pid] = candles(pid, cutoff_ms // 1000, end_ms // 1000 + 3600)
        ts = int(sig.time.timestamp()) + delay_s
        px = _price_at(cache[pid], ts)
        if px is None:
            res.rejected["sin_datos"] = res.rejected.get("sin_datos", 0) + 1
            continue
        equity = cash + sum(q * (_price_at(cache[f"{a}-EUR"], ts) or (c / q)) for a, (q, c) in pos.items() if q > 0)
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if d != day:
            day, day_start, trades_day = d, equity, 0
        peak = max(peak, equity)
        if sig.side == "BUY":
            if sig.asset not in limits.assets:
                res.rejected["activo_no_permitido"] = res.rejected.get("activo_no_permitido", 0) + 1
                continue
            st = PortfolioState(cash, equity, peak, day_start,
                                {a: q * px if a == sig.asset else q * (c / q) for a, (q, c) in pos.items() if q > 0},
                                len([1 for q, _ in pos.values() if q > 0]), trades_day, capital)
            r = capacity_for_buy(st, sig.asset, limits)
            if not r.allowed:
                res.rejected[r.reason] = res.rejected.get(r.reason, 0) + 1
                continue
            amount = min(equity * (sig.fraction or ZERO), r.max_amount)
            if amount < limits.min_trade_eur:
                res.rejected["below_minimum"] = res.rejected.get("below_minimum", 0) + 1
                continue
            est = costs.estimate(amount, "taker", maker_fee, taker_fee, spread_pct, slippage_pct, limits.max_round_trip_cost_pct)
            if not est.viable:
                res.rejected["cost_too_high"] = res.rejected.get("cost_too_high", 0) + 1
                continue
            fill = px * (1 + spread_pct / 200 + slippage_pct / 100)
            fee = amount * taker_fee
            qty = (amount - fee) / fill
            q0, c0 = pos.get(sig.asset, (ZERO, ZERO))
            pos[sig.asset] = (q0 + qty, c0 + amount)
            cash -= amount
            fees += fee
            trades_day += 1
            res.trades.append({"t": ts, "asset": sig.asset, "side": "BUY", "eur": str(amount.quantize(Decimal("0.01"))), "price": str(fill)})
        else:
            q0, c0 = pos.get(sig.asset, (ZERO, ZERO))
            if q0 <= 0:
                continue
            qty = q0 * min(sig.fraction or Decimal(1), Decimal(1))
            fill = px * (1 - spread_pct / 200 - slippage_pct / 100)
            gross = qty * fill
            fee = gross * taker_fee
            cash += gross - fee
            fees += fee
            pos[sig.asset] = (q0 - qty, c0 * (1 - qty / q0))
            res.trades.append({"t": ts, "asset": sig.asset, "side": "SELL", "eur": str(gross.quantize(Decimal("0.01"))), "price": str(fill)})
        res.equity_curve.append((ts, str(equity.quantize(Decimal("0.01")))))
    final = cash
    for a, (q, c) in pos.items():
        if q > 0:
            series = cache.get(f"{a}-EUR") or []
            last = series[-1][1] if series else c / q
            final += q * last * (1 - taker_fee)  # liquidación teórica final, con comisión
    res.final_equity, res.cash_final, res.fees_paid = final, capital, fees
    return res


def _signals_with_av(user, fills, names, av_hist, av_keys):
    for f in sorted(fills, key=lambda x: x["time"]):
        i = bisect.bisect_right(av_keys, f["time"]) - 1
        av = Decimal(str(av_hist[i][1])) if i >= 0 else None
        yield from fills_to_signals(user, [f], names, av, True)


def main() -> None:  # pragma: no cover - requiere red
    from .coinbase.client import CoinbaseClient
    from .config import get_settings
    from .db import init_engine
    from .sources.hyperliquid import HyperliquidClient
    from . import state

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--address", required=True)
    ap.add_argument("--cutoff", required=True, help="YYYY-MM-DD: fin del periodo de selección")
    ap.add_argument("--end", required=True)
    ap.add_argument("--delay", type=int, default=60)
    a = ap.parse_args()
    cfg = get_settings()
    init_engine()
    hl, cb = HyperliquidClient(cfg.hyperliquid_info_url), CoinbaseClient(cfg.coinbase_rest_base, None)
    cut = int(datetime.fromisoformat(a.cutoff).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    pf = hl.portfolio(a.address).get("allTime") or {}
    av = {int(t): float(v) for t, v in pf.get("accountValueHistory", [])}
    pnl = {int(t): float(v) for t, v in pf.get("pnlHistory", [])}
    pts = sorted((t, av[t], pnl[t]) for t in av.keys() & pnl.keys())
    fills = hl.fills_since(a.address, cut - 365 * 86_400_000, end, max_pages=50)

    def candles(pid, start, stop):
        out, t = [], start
        while t < stop:
            chunk = cb.candles(pid, t, min(t + 300 * 60, stop))
            out += [(int(c["start"]), Decimal(c["open"])) for c in chunk]
            t += 300 * 60
        return sorted(set(out))

    res = run(a.address, pts, fills, hl.spot_names(), candles, cut, end, state.get_limits(), cfg.default_maker_fee, cfg.default_taker_fee, a.delay)
    print(json.dumps(res.as_dict(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
