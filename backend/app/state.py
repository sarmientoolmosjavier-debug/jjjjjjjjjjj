"""Estado de ejecución persistente: modo, ajustes editables, parada de emergencia."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .config import EDITABLE, HARD_LIMITS, HARD_MINIMUMS, get_settings
from .db import session_scope
from .models import Setting

MODES = ("observation", "simulation", "live")


def get_value(key: str, default: Any = None) -> Any:
    with session_scope() as s:
        row = s.get(Setting, key)
        return row.value if row is not None else default


def set_value(key: str, value: Any) -> None:
    with session_scope() as s:
        row = s.get(Setting, key)
        if row is None:
            s.add(Setting(key=key, value=value))
        else:
            row.value = value


@dataclass
class Limits:
    min_cash_reserve_eur: Decimal
    max_total_exposure_eur: Decimal
    max_asset_exposure_eur: Decimal
    max_open_positions: int
    max_new_trades_per_day: int
    max_daily_loss_eur: Decimal
    max_drawdown_eur: Decimal
    max_round_trip_cost_pct: Decimal
    min_trade_eur: Decimal
    order_style: str
    max_signal_age_seconds: int
    max_spread_pct: Decimal
    min_trader_score: Decimal
    allowed_assets: str
    hl_include_perp_longs: bool

    @property
    def assets(self) -> list[str]:
        return [a.strip().upper() for a in self.allowed_assets.split(",") if a.strip()]


def _coerce(key: str, value: Any) -> Any:
    t = EDITABLE[key]
    if t is bool:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "si", "sí", "yes")
        return bool(value)
    if t is Decimal:
        return Decimal(str(value))
    return t(value)


def get_limits() -> Limits:
    cfg = get_settings()
    overrides = get_value("limits", {}) or {}
    vals = {}
    for k in EDITABLE:
        v = overrides.get(k, getattr(cfg, k))
        vals[k] = _coerce(k, v)
    return Limits(**vals)


def validate_limits(changes: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for k, v in changes.items():
        if k not in EDITABLE:
            raise ValueError(f"Ajuste no editable: {k}")
        v = _coerce(k, v)
        if k in HARD_LIMITS and v > HARD_LIMITS[k]:
            raise ValueError(f"{k} no puede superar {HARD_LIMITS[k]} (perfil moderado)")
        if k in HARD_MINIMUMS and v < HARD_MINIMUMS[k]:
            raise ValueError(f"{k} no puede ser inferior a {HARD_MINIMUMS[k]}")
        if isinstance(v, Decimal) and v < 0:
            raise ValueError(f"{k} no puede ser negativo")
        if k == "order_style" and v not in ("maker", "taker"):
            raise ValueError("order_style debe ser 'maker' o 'taker'")
        if k == "allowed_assets":
            v = ",".join(a.strip().upper() for a in v.split(",") if a.strip().isalnum())
        clean[k] = str(v) if isinstance(v, Decimal) else v
    return clean


def update_limits(changes: dict[str, Any]) -> Limits:
    clean = validate_limits(changes)
    current = get_value("limits", {}) or {}
    current.update(clean)
    set_value("limits", current)
    return get_limits()


def get_mode() -> str:
    return get_value("mode", "simulation")


def set_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError("Modo inválido")
    set_value("mode", mode)


def emergency_stopped() -> bool:
    return bool(get_value("emergency_stop", False))


def drawdown_suspended(mode: str) -> bool:
    return bool(get_value(f"drawdown_suspended:{mode}", False))
