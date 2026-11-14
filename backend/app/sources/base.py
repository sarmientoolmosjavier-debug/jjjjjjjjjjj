from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class RawSignal:
    """Operación observada de un trader, ya normalizada a 'compra/venta spot de ASSET'."""

    external_id: str
    asset: str  # BTC, ETH… (activo base; se buscará ASSET-EUR en Coinbase)
    side: str  # BUY | SELL
    kind: str  # spot | perp_long_open | perp_long_close | manual
    price: Decimal  # precio del trader (USD/USDC en Hyperliquid; referencia, no se usa para ejecutar)
    size: Decimal
    fraction: Decimal | None  # BUY: fracción de su cuenta; SELL: fracción de su posición
    time: datetime
    raw: dict = field(default_factory=dict)
