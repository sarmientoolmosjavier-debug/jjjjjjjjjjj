from decimal import Decimal as D

from app.ranking import compute_metrics, equity_from_pnl, score
from app.sources.hyperliquid import fills_to_signals, normalize_asset

DAY = 86_400_000


def test_equity_index_ignores_deposits():
    # Deposita 1000 el día 2 (valor de cuenta sube) pero el PnL no cambia → rentabilidad 0
    pts = [(0, 1000, 0), (DAY, 1000, 0), (2 * DAY, 2000, 0), (3 * DAY, 2000, 100)]
    eq = equity_from_pnl(pts)
    assert eq[2][1] == 1.0
    assert abs(eq[3][1] - 1.05) < 1e-9


def _series(days, daily_r):
    pts, av, pnl = [], 1000.0, 0.0
    for i in range(days):
        pts.append((i * DAY, av, pnl))
        g = av * daily_r(i)
        pnl += g
        av += g
    return pts


def _fills(n, days, coin="BTC", direction="Open Long"):
    return [{"oid": i, "dir": direction, "coin": coin, "px": "60000", "sz": "0.01", "time": int(i * days * DAY / n), "fee": "0.3"} for i in range(n)]


def test_good_trader_verified():
    pts = _series(200, lambda i: 0.004 if i % 3 else -0.003)
    m = compute_metrics(pts, _fills(40, 200), 1.0, 1.0, 1.2, "test")
    sc, ok, notes = score(m)
    assert ok, notes
    assert m.history_days > 190 and m.max_drawdown_pct < 5 and sc > 60


def test_short_history_not_verified():
    pts = _series(30, lambda i: 0.01)
    sc, ok, notes = score(compute_metrics(pts, _fills(40, 30), 1.0, 1.0, 1.0, "test"))
    assert not ok and any("Historial" in n for n in notes)


def test_unreplicable_not_verified():
    pts = _series(200, lambda i: 0.004 if i % 3 else -0.003)
    sc, ok, notes = score(compute_metrics(pts, _fills(40, 200), 0.1, 1.0, 1.0, "test"))
    assert not ok and any("replicable" in n for n in notes)


def test_fills_to_signals():
    names = {"@142": "UBTC", "@1": "HYPE"}
    fills = [
        {"oid": 1, "tid": 10, "dir": "Open Long", "coin": "BTC", "px": "60000", "sz": "0.5", "time": 1000, "startPosition": "0", "hash": "0xa"},
        {"oid": 1, "tid": 11, "dir": "Open Long", "coin": "BTC", "px": "60100", "sz": "0.5", "time": 1001, "startPosition": "0.5", "hash": "0xa"},
        {"oid": 2, "tid": 12, "dir": "Open Short", "coin": "ETH", "px": "3000", "sz": "1", "time": 1002, "startPosition": "0"},
        {"oid": 3, "tid": 13, "dir": "Close Long", "coin": "BTC", "px": "61000", "sz": "0.25", "time": 2000, "startPosition": "1"},
        {"oid": 4, "tid": 14, "dir": "Buy", "coin": "@142", "px": "60000", "sz": "0.1", "time": 3000, "startPosition": "0"},
    ]
    sigs = fills_to_signals("0xABC", fills, names, D(600000), True)
    assert [(s.asset, s.side, s.kind) for s in sigs] == [("BTC", "BUY", "perp_long_open"), ("BTC", "SELL", "perp_long_close"), ("BTC", "BUY", "spot")]
    assert sigs[0].price == D("60050") and sigs[0].fraction == D("60050") / D(600000)
    assert sigs[1].fraction == D("0.25")
    assert sigs[0].external_id.startswith("hl:0xabc:1:10")
    assert fills_to_signals("0x", fills, names, D(1), False)[0].kind == "spot"  # sin perps


def test_normalize_asset():
    assert normalize_asset("@142", {"@142": "UBTC"}) == ("BTC", True)
    assert normalize_asset("ETH", {}) == ("ETH", False)
