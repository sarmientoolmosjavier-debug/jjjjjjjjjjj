"""Módulo de optimización de costes para una cartera pequeña.

Todas las cifras en porcentaje del importe negociado. La salida se presupone *taker*
(conservador: cuando hay que salir no se puede esperar a ser maker).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN

HUNDRED = Decimal(100)


@dataclass
class CostEstimate:
    amount_eur: Decimal
    style: str
    fee_source: str
    entry_fee_pct: Decimal
    exit_fee_pct: Decimal
    spread_pct: Decimal
    slippage_pct: Decimal
    entry_cost_pct: Decimal
    exit_cost_pct: Decimal
    total_cost_pct: Decimal
    breakeven_return_pct: Decimal
    entry_fee_eur: Decimal
    exit_fee_eur: Decimal
    total_cost_eur: Decimal
    threshold_pct: Decimal

    @property
    def viable(self) -> bool:
        return self.total_cost_pct <= self.threshold_pct

    def as_dict(self) -> dict:
        d = {k: (str(v.quantize(Decimal("0.0001"))) if isinstance(v, Decimal) else v) for k, v in asdict(self).items()}
        d["viable"] = self.viable
        return d


def estimate(amount_eur: Decimal, style: str, maker_fee: Decimal, taker_fee: Decimal, spread_pct: Decimal, slippage_pct: Decimal, threshold_pct: Decimal, fee_source: str = "") -> CostEstimate:
    """maker_fee/taker_fee como fracción (0.006 = 0,6 %); spread/slippage/threshold en %."""
    half_spread = spread_pct / 2
    if style == "maker":
        entry_fee = maker_fee * HUNDRED
        entry_cost = entry_fee  # compra en el bid: no cruza spread ni desliza (si se ejecuta)
    else:
        entry_fee = taker_fee * HUNDRED
        entry_cost = entry_fee + half_spread + slippage_pct
    exit_fee = taker_fee * HUNDRED
    exit_cost = exit_fee + half_spread + slippage_pct
    total = entry_cost + exit_cost
    breakeven = total / (1 - total / HUNDRED) if total < HUNDRED else Decimal("Infinity")
    return CostEstimate(
        amount_eur=amount_eur, style=style, fee_source=fee_source,
        entry_fee_pct=entry_fee, exit_fee_pct=exit_fee, spread_pct=spread_pct, slippage_pct=slippage_pct,
        entry_cost_pct=entry_cost, exit_cost_pct=exit_cost, total_cost_pct=total, breakeven_return_pct=breakeven,
        entry_fee_eur=amount_eur * entry_fee / HUNDRED, exit_fee_eur=amount_eur * exit_fee / HUNDRED,
        total_cost_eur=amount_eur * total / HUNDRED, threshold_pct=threshold_pct,
    )


def floor_to(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        return value
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment
