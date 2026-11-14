from decimal import Decimal as D

from app import state
from app.costs import estimate, floor_to
from app.risk import PortfolioState, capacity_for_buy


def test_taker_round_trip_with_entry_tier_fees_is_not_viable():
    e = estimate(D(15), "taker", D("0.006"), D("0.012"), D("0.02"), D("0.02"), D("1.0"))
    assert e.total_cost_pct > D("2.4") and not e.viable
    assert e.breakeven_return_pct > e.total_cost_pct


def test_maker_entry_cheaper_than_taker():
    m = estimate(D(15), "maker", D("0.004"), D("0.006"), D("0.02"), D("0.02"), D("1.0"))
    t = estimate(D(15), "taker", D("0.004"), D("0.006"), D("0.02"), D("0.02"), D("1.0"))
    assert m.total_cost_pct < t.total_cost_pct
    assert m.entry_cost_pct == D("0.4")  # maker: sin spread ni deslizamiento en la entrada


def test_threshold_configurable():
    e = estimate(D(15), "maker", D("0.0025"), D("0.004"), D("0.02"), D("0.02"), D("1.0"))
    assert e.viable
    e2 = estimate(D(15), "maker", D("0.0025"), D("0.004"), D("0.02"), D("0.02"), D("0.5"))
    assert not e2.viable


def test_floor_to_increment():
    assert floor_to(D("0.123456789"), D("0.00000001")) == D("0.12345678")
    assert floor_to(D("15.239"), D("0.01")) == D("15.23")


def ps(**kw):
    base = dict(cash=D(100), equity=D(100), peak_equity=D(100), day_start_equity=D(100), exposure_by_asset={}, open_positions=0, trades_today=0, initial_capital=D(100))
    base.update(kw)
    return PortfolioState(**base)


def test_capacity_respects_asset_cap():
    r = capacity_for_buy(ps(), "BTC", state.get_limits())
    assert r.allowed and r.max_amount == D(20)


def test_capacity_respects_reserve_and_total():
    lim = state.get_limits()
    r = capacity_for_buy(ps(cash=D(40), equity=D(100), exposure_by_asset={"ETH": D(20), "SOL": D(20), "ADA": D(20)}, open_positions=3), "BTC", lim)
    assert r.allowed and r.max_amount == D(10)  # reserva 30 → sólo 10 disponibles
    r = capacity_for_buy(ps(cash=D(30), equity=D(100), exposure_by_asset={"ETH": D(20), "SOL": D(20), "ADA": D(20)}, open_positions=3), "BTC", lim)
    assert not r.allowed


def test_daily_loss_blocks_buys():
    r = capacity_for_buy(ps(equity=D(98), day_start_equity=D(100)), "BTC", state.get_limits())
    assert not r.allowed and r.reason == "daily_loss_limit"


def test_drawdown_suspends():
    r = capacity_for_buy(ps(equity=D(95), peak_equity=D(105), day_start_equity=D(95)), "BTC", state.get_limits())
    assert not r.allowed and r.reason == "drawdown_limit" and r.suspend_strategy


def test_max_positions_and_trades():
    lim = state.get_limits()
    exp = {a: D(5) for a in ("A", "B", "C", "D")}
    assert capacity_for_buy(ps(exposure_by_asset=exp, open_positions=4), "BTC", lim).reason == "max_open_positions"
    assert capacity_for_buy(ps(exposure_by_asset=exp, open_positions=4), "A", lim).allowed  # añadir a una existente sí
    assert capacity_for_buy(ps(trades_today=3), "BTC", lim).reason == "max_trades_per_day"


def test_hard_limits_cannot_be_exceeded():
    import pytest

    with pytest.raises(ValueError):
        state.update_limits({"max_total_exposure_eur": "80"})
    with pytest.raises(ValueError):
        state.update_limits({"min_cash_reserve_eur": "10"})
    lim = state.update_limits({"max_round_trip_cost_pct": "1.5", "allowed_assets": "btc, eth, sol"})
    assert lim.max_round_trip_cost_pct == D("1.5") and lim.assets == ["BTC", "ETH", "SOL"]
