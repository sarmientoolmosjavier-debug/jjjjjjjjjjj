from decimal import Decimal as D

from app import backtest, state

DAY = 86_400_000


def _history(days):
    pts, av, pnl = [], 100000.0, 0.0
    for i in range(days):
        pts.append((i * DAY, av, pnl))
        g = av * (0.004 if i % 3 else -0.003)
        pnl += g
        av += g
    return pts


def _fills(days):
    out = []
    for i in range(0, days, 4):
        out.append({"oid": i, "tid": i, "dir": "Open Long", "coin": "BTC", "px": "60000", "sz": "0.25", "time": i * DAY + 1000, "startPosition": "0"})
        out.append({"oid": i + 1, "tid": i + 1, "dir": "Close Long", "coin": "BTC", "px": "61000", "sz": "0.25", "time": i * DAY + 2 * DAY, "startPosition": "0.25"})
    return out


def candles_factory(prices):
    def candles(pid, start, end):
        return [(t, p) for t, p in prices if start <= t <= end]
    return candles


def test_no_lookahead_execution_uses_next_candle_after_delay():
    days = 300
    cutoff = 200 * DAY
    # El precio sube justo en la vela del instante de la señal; la ejecución con 60 s de retraso debe usar la siguiente
    prices = []
    for m in range(0, days * 1440, 5):  # velas de 5 min
        t = m * 60
        prices.append((t, D(60000 + m // 5)))
    lim = state.update_limits({"max_round_trip_cost_pct": "3"})
    res = backtest.run("0xabc", _history(days), _fills(days), {}, candles_factory(prices), cutoff, days * DAY, lim,
                       D("0.004"), D("0.006"), delay_s=60)
    assert res.selected, res.selection_notes
    assert res.trades, res.rejected
    first = res.trades[0]
    assert first["t"] >= cutoff // 1000  # nada antes del corte
    # Precio de ejecución = apertura de la primera vela >= señal + retraso (más spread/deslizamiento), nunca el del trader
    exp_open = next(p for t, p in prices if t >= first["t"])
    assert D(first["price"]) > exp_open and D(first["price"]) < exp_open * D("1.001")
    assert res.btc_hold_final is not None and res.cash_final == D(100)
    assert res.fees_paid > 0


def test_unselected_trader_stays_in_observation():
    lim = state.get_limits()
    res = backtest.run("0xabc", _history(30), _fills(30), {}, candles_factory([]), 20 * DAY, 30 * DAY, lim, D("0.004"), D("0.006"))
    assert not res.selected and res.trades == [] and res.final_equity == D(100)
