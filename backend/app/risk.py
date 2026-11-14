"""Motor de riesgo: límites de exposición, pérdidas y frecuencia. Funciones puras sobre un snapshot."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .state import Limits

ZERO = Decimal(0)


@dataclass
class PortfolioState:
    cash: Decimal  # disponible (sin reservas de órdenes abiertas)
    equity: Decimal
    peak_equity: Decimal
    day_start_equity: Decimal
    exposure_by_asset: dict[str, Decimal]  # valor de mercado (+ órdenes de compra abiertas)
    open_positions: int  # nº de activos con posición
    trades_today: int
    initial_capital: Decimal

    @property
    def invested(self) -> Decimal:
        return sum(self.exposure_by_asset.values(), ZERO)

    @property
    def daily_pnl(self) -> Decimal:
        return self.equity - self.day_start_equity

    @property
    def drawdown(self) -> Decimal:
        return max(self.peak_equity - self.equity, ZERO)


@dataclass
class RiskResult:
    allowed: bool
    reason: str = "ok"
    message: str = ""
    max_amount: Decimal = ZERO
    suspend_strategy: bool = False
    details: dict = field(default_factory=dict)


def capacity_for_buy(p: PortfolioState, asset: str, lim: Limits) -> RiskResult:
    """Importe máximo que se puede comprar en ``asset`` respetando todos los límites."""
    if p.drawdown >= lim.max_drawdown_eur:
        return RiskResult(False, "drawdown_limit", f"Drawdown {p.drawdown:.2f} € ≥ {lim.max_drawdown_eur} €: estrategia suspendida, requiere revisión", suspend_strategy=True)
    if -p.daily_pnl >= lim.max_daily_loss_eur:
        return RiskResult(False, "daily_loss_limit", f"Pérdida diaria {-p.daily_pnl:.2f} € ≥ {lim.max_daily_loss_eur} €: compras bloqueadas hasta mañana")
    if p.trades_today >= lim.max_new_trades_per_day:
        return RiskResult(False, "max_trades_per_day", f"Ya hay {p.trades_today} operaciones nuevas hoy (máx. {lim.max_new_trades_per_day})")
    has_position = p.exposure_by_asset.get(asset, ZERO) > 0
    if not has_position and p.open_positions >= lim.max_open_positions:
        return RiskResult(False, "max_open_positions", f"Máximo de {lim.max_open_positions} posiciones abiertas")
    # Los límites en € se aplican sobre el capital de referencia (100 €), en proporción si el patrimonio cambia
    scale = min(p.equity / p.initial_capital, Decimal(1)) if p.initial_capital > 0 else Decimal(1)
    total_cap = lim.max_total_exposure_eur * scale - p.invested
    asset_cap = lim.max_asset_exposure_eur * scale - p.exposure_by_asset.get(asset, ZERO)
    reserve_cap = p.cash - lim.min_cash_reserve_eur * scale
    room = min(total_cap, asset_cap, reserve_cap)
    details = {"total_cap": str(total_cap), "asset_cap": str(asset_cap), "reserve_cap": str(reserve_cap)}
    if room <= 0:
        which = min((("max_total_exposure", total_cap), ("max_asset_exposure", asset_cap), ("cash_reserve", reserve_cap)), key=lambda x: x[1])[0]
        return RiskResult(False, which, "Sin margen dentro de los límites de exposición/reserva", details=details)
    return RiskResult(True, max_amount=room, details=details)


def limits_status(p: PortfolioState, lim: Limits) -> list[dict]:
    scale = min(p.equity / p.initial_capital, Decimal(1)) if p.initial_capital > 0 else Decimal(1)
    rows = [
        ("Exposición total", p.invested, lim.max_total_exposure_eur * scale),
        ("Pérdida diaria", max(-p.daily_pnl, ZERO), lim.max_daily_loss_eur),
        ("Drawdown desde máximo", p.drawdown, lim.max_drawdown_eur),
        ("Posiciones abiertas", Decimal(p.open_positions), Decimal(lim.max_open_positions)),
        ("Operaciones nuevas hoy", Decimal(p.trades_today), Decimal(lim.max_new_trades_per_day)),
    ]
    out = []
    for name, value, limit in rows:
        ratio = value / limit if limit > 0 else Decimal(1)
        out.append({"name": name, "value": str(value.quantize(Decimal("0.01"))), "limit": str(limit.quantize(Decimal("0.01"))),
                    "status": "bloqueado" if ratio >= 1 else ("aviso" if ratio >= Decimal("0.75") else "ok")})
    reserve_min = lim.min_cash_reserve_eur * scale
    out.append({"name": "Liquidez (mín. reserva)", "value": str(p.cash.quantize(Decimal("0.01"))), "limit": str(reserve_min.quantize(Decimal("0.01"))),
                "status": "bloqueado" if p.cash <= reserve_min else ("aviso" if p.cash <= reserve_min * Decimal("1.2") else "ok")})
    for asset, v in sorted(p.exposure_by_asset.items()):
        lim_a = lim.max_asset_exposure_eur * scale
        ratio = v / lim_a if lim_a > 0 else Decimal(1)
        out.append({"name": f"Exposición {asset}", "value": str(v.quantize(Decimal("0.01"))), "limit": str(lim_a.quantize(Decimal("0.01"))),
                    "status": "bloqueado" if ratio >= 1 else ("aviso" if ratio >= Decimal("0.75") else "ok")})
    return out
