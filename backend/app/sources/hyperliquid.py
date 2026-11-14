"""Fuente Hyperliquid: API pública ``info`` (https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).

Datos on-chain y verificables, pero **de una dirección**, no del historial completo de una persona.
Sólo se convierten en señales las operaciones replicables como spot largo sin apalancamiento.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

# Tokens spot "envueltos" de Hyperliquid → activo equivalente en Coinbase. Lista explícita y corta.
WRAPPED = {"UBTC": "BTC", "UETH": "ETH", "USOL": "SOL", "UFART": None}


class HyperliquidClient:
    def __init__(self, url: str, transport: httpx.BaseTransport | None = None):
        self.url = url
        self._http = httpx.Client(timeout=15, transport=transport)
        self._spot_names: dict[str, str] | None = None

    def _post(self, body: dict) -> Any:
        r = self._http.post(self.url, json=body)
        r.raise_for_status()
        return r.json()

    def fills_since(self, user: str, start_ms: int, end_ms: int | None = None, max_pages: int = 10) -> list[dict]:
        out: list[dict] = []
        start = start_ms
        for _ in range(max_pages):
            body = {"type": "userFillsByTime", "user": user, "startTime": start, "aggregateByTime": False}
            if end_ms:
                body["endTime"] = end_ms
            page = self._post(body) or []
            out.extend(page)
            if len(page) < 2000:
                break
            start = max(f["time"] for f in page) + 1
        return out

    def portfolio(self, user: str) -> dict[str, dict]:
        return {k: v for k, v in (self._post({"type": "portfolio", "user": user}) or [])}

    def account_value(self, user: str) -> Decimal | None:
        perp = self._post({"type": "clearinghouseState", "user": user}) or {}
        v = Decimal(str((perp.get("marginSummary") or {}).get("accountValue", "0")))
        try:
            spot = self._post({"type": "spotClearinghouseState", "user": user}) or {}
            for b in spot.get("balances", []):
                if b.get("coin") in ("USDC", "USDT", "USDH"):
                    v += Decimal(str(b.get("total", "0")))
        except httpx.HTTPError:
            pass
        return v if v > 0 else None

    def leverage(self, user: str) -> Decimal | None:
        perp = self._post({"type": "clearinghouseState", "user": user}) or {}
        ms = perp.get("marginSummary") or {}
        av, ntl = Decimal(str(ms.get("accountValue", "0"))), Decimal(str(ms.get("totalNtlPos", "0")))
        return (ntl / av) if av > 0 else None

    def spot_names(self) -> dict[str, str]:
        """'@107' → nombre del token base (p. ej. 'HYPE')."""
        if self._spot_names is None:
            meta = self._post({"type": "spotMeta"}) or {}
            tokens = {t["index"]: t["name"] for t in meta.get("tokens", [])}
            names = {}
            for u in meta.get("universe", []):
                base = tokens.get(u["tokens"][0])
                names[u["name"]] = base
                names[f"@{u['index']}"] = base
            self._spot_names = names
        return self._spot_names


def normalize_asset(coin: str, spot_names: dict[str, str]) -> tuple[str | None, bool]:
    """Devuelve (activo, es_spot)."""
    if coin.startswith("@") or "/" in coin:
        base = spot_names.get(coin) or coin.split("/")[0]
        if base in WRAPPED:
            return WRAPPED[base], True
        return (base.upper() if base else None), True
    return coin.upper(), False


def fills_to_signals(user: str, fills: list[dict], spot_names: dict[str, str], account_value: Decimal | None, include_perp_longs: bool) -> list["RawSignal"]:
    from .base import RawSignal

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in fills:
        groups[(f.get("oid"), f.get("dir"), f.get("coin"))].append(f)
    out = []
    for (oid, direction, coin), fs in groups.items():
        asset, is_spot = normalize_asset(coin, spot_names)
        if not asset:
            continue
        if is_spot and direction in ("Buy", "Sell"):
            side, kind = ("BUY" if direction == "Buy" else "SELL"), "spot"
        elif not is_spot and include_perp_longs and direction == "Open Long":
            side, kind = "BUY", "perp_long_open"
        elif not is_spot and include_perp_longs and direction in ("Close Long", "Long > Short"):
            side, kind = "SELL", "perp_long_close"
        else:
            continue  # cortos, liquidaciones, etc.: no replicables en spot
        sz = sum(Decimal(f["sz"]) for f in fs)
        px = sum(Decimal(f["px"]) * Decimal(f["sz"]) for f in fs) / sz
        first = min(fs, key=lambda f: f["time"])
        start_pos = abs(Decimal(str(first.get("startPosition", "0"))))
        if side == "BUY":
            fraction = min(px * sz / account_value, Decimal(1)) if account_value else None
        else:
            fraction = Decimal(1) if direction == "Long > Short" else (min(sz / start_pos, Decimal(1)) if start_pos > 0 else Decimal(1))
        out.append(
            RawSignal(
                external_id=f"hl:{user.lower()}:{oid}:{first.get('tid')}",
                asset=asset, side=side, kind=kind, price=px, size=sz, fraction=fraction,
                time=datetime.fromtimestamp(first["time"] / 1000, tz=timezone.utc),
                raw={"hash": first.get("hash"), "dir": direction, "coin": coin, "fills": len(fs)},
            )
        )
    return sorted(out, key=lambda s: s.time)
