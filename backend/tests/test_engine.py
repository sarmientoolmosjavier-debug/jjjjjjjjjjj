from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app import engine, market, portfolio, services, state
from app.db import session_scope
from app.models import Decision, Order, Position, Trader
from app.sources.base import RawSignal


def make_trader(enabled=True, verified=True, score=80):
    with session_scope() as s:
        t = Trader(source="hyperliquid", external_id="0x" + "1" * 40, label="T1", enabled=enabled, verified=verified, score=D(score), metrics={}, evaluation_notes=[])
        s.add(t)
        s.flush()
        return t.id


_n = [0]


def signal(tid, side="BUY", asset="BTC", fraction=D("0.15"), age=5):
    _n[0] += 1
    with session_scope() as s:
        t = s.get(Trader, tid)
        sig = engine.ingest_signal(s, t, RawSignal(f"x{_n[0]}", asset, side, "perp_long_open" if side == "BUY" else "perp_long_close",
                                                   D("60000"), D("1"), fraction, datetime.now(timezone.utc) - timedelta(seconds=age)))
        return sig.id


def run(mode="simulation"):
    services.process_pending(mode)
    with session_scope() as s:
        return list(s.scalars(select(Decision).order_by(Decision.id)))


@pytest.fixture
def taker_style():
    state.update_limits({"order_style": "taker"})


def test_buy_executes_in_simulation_with_real_fees(fake_cb, taker_style):
    tid = make_trader()
    signal(tid)
    d = run()[-1]
    assert d.accepted and d.reason == "executed", d.message
    with session_scope() as s:
        o = s.scalar(select(Order))
        assert o.status == "FILLED" and o.side == "BUY" and o.mode == "simulation"
        assert o.quote_size == D("15.00")  # 15 % de 100 €
        assert o.avg_price > D("60010")  # ask + deslizamiento
        assert o.fee_eur > 0
        w = portfolio.get_wallet(s, "simulation")
        assert w.cash_eur < D(85.01) and w.fees_total == o.fee_eur
    assert d.details["costs"]["viable"] is True


def test_conservative_default_fees_reject_for_cost(public_cb, taker_style):
    tid = make_trader()
    signal(tid)
    d = run()[-1]
    assert not d.accepted and d.reason == "cost_too_high"


def test_proportional_clipped_to_asset_cap(fake_cb, taker_style):
    tid = make_trader()
    signal(tid, fraction=D("0.9"))
    d = run()[-1]
    assert d.accepted and d.details["clipped_by_limits"]
    with session_scope() as s:
        assert s.scalar(select(Order)).quote_size == D("20.00")


def test_small_signal_not_inflated(fake_cb, taker_style):
    tid = make_trader()
    signal(tid, fraction=D("0.02"))  # 2 € < mínimo 5 €
    d = run()[-1]
    assert not d.accepted and d.reason == "below_minimum"


def test_stale_signal_and_unverified(fake_cb, taker_style):
    tid = make_trader()
    signal(tid, age=600)
    assert run()[-1].reason == "stale_signal"
    tid2 = make_trader_other(verified=False)
    signal(tid2)
    assert run()[-1].reason == "trader_not_verified"


def make_trader_other(**kw):
    with session_scope() as s:
        t = Trader(source="hyperliquid", external_id="0x" + "2" * 40, label="T2", enabled=True, verified=kw.get("verified", True), score=D(80), metrics={}, evaluation_notes=[])
        s.add(t)
        s.flush()
        return t.id


def test_stale_market_data_rejected(fake_cb, taker_style):
    tid = make_trader()
    market.store_quote("BTC-EUR", D(60000), D(60010), D(60005), datetime.now(timezone.utc) - timedelta(minutes=5), "test")
    fake_cb.book["BTC-EUR"] = (D(60000), D(60010))
    # El fallback REST obtiene un precio nuevo → sí opera; si el REST falla, no
    market.get_client()._http._transport.handler = lambda req: __import__("httpx").Response(503)
    signal(tid)
    d = run()[-1]
    assert d.reason == "stale_market_data"


def test_asset_not_allowed(fake_cb, taker_style):
    tid = make_trader()
    signal(tid, asset="DOGE")
    assert run()[-1].reason == "asset_not_allowed"


def test_buy_then_sell_realizes_pnl(fake_cb, taker_style):
    tid = make_trader()
    signal(tid)
    run()
    now = datetime.now(timezone.utc)
    market.store_quote("BTC-EUR", D(66000), D(66010), D(66005), now, "test")
    fake_cb.book["BTC-EUR"] = (D(66000), D(66010))
    signal(tid, side="SELL", fraction=D(1))
    d = run()[-1]
    assert d.accepted, d.message
    with session_scope() as s:
        sell = s.scalar(select(Order).where(Order.side == "SELL"))
        assert sell.realized_pnl_eur > D("1")
        pos = s.scalar(select(Position))
        assert pos.base_qty == 0


def test_sell_without_position_ignored(fake_cb):
    tid = make_trader()
    signal(tid, side="SELL")
    assert run()[-1].reason == "no_position"


def test_idempotent_client_order_id(fake_cb, taker_style):
    tid = make_trader()
    sid = signal(tid)
    run()
    assert engine.client_order_id(sid, "simulation") == engine.client_order_id(sid, "simulation")
    with session_scope() as s:
        from app.models import Signal

        sig = s.get(Signal, sid)
        sig.processed = False
    d = run()[-1]
    assert d.reason == "duplicate"
    with session_scope() as s:
        assert len(list(s.scalars(select(Order)))) == 1


def test_duplicate_signal_ingest(fake_cb):
    tid = make_trader()
    with session_scope() as s:
        t = s.get(Trader, tid)
        raw = RawSignal("same", "BTC", "BUY", "spot", D(1), D(1), D("0.1"), datetime.now(timezone.utc))
        assert engine.ingest_signal(s, t, raw) is not None
        assert engine.ingest_signal(s, t, raw) is None


def test_max_three_trades_per_day(fake_cb, taker_style):
    state.update_limits({"max_asset_exposure_eur": "20"})
    tid = make_trader()
    for a in ("BTC", "ETH", "BTC", "ETH"):
        signal(tid, asset=a, fraction=D("0.06"))
    ds = run()
    assert [d.accepted for d in ds] == [True, True, True, False]
    assert ds[-1].reason == "max_trades_per_day"


def test_observation_mode_does_not_execute(fake_cb, taker_style):
    tid = make_trader()
    signal(tid)
    d = run("observation")[-1]
    assert d.reason == "observation_mode"
    with session_scope() as s:
        assert s.scalar(select(Order)) is None


def test_emergency_stop(fake_cb, taker_style):
    tid = make_trader()
    services.emergency_stop()
    signal(tid)
    assert run()[-1].reason == "emergency_stop"


def test_maker_order_fills_only_on_trade_through(fake_cb):
    tid = make_trader()
    signal(tid)
    d = run()[-1]
    assert d.accepted
    with session_scope() as s:
        o = s.scalar(select(Order))
        assert o.status == "OPEN" and o.limit_price == D("60000")
        w = portfolio.get_wallet(s, "simulation")
        assert w.reserved_eur > 0
    services.poll_orders()  # el precio no ha bajado del límite → sigue abierta
    with session_scope() as s:
        assert s.scalar(select(Order)).status == "OPEN"
    market.store_quote("BTC-EUR", D(59980), D(59990), D(59985), datetime.now(timezone.utc), "test")
    services.poll_orders()
    with session_scope() as s:
        o = s.scalar(select(Order))
        assert o.status == "FILLED" and o.avg_price == D("60000")
        w = portfolio.get_wallet(s, "simulation")
        assert w.reserved_eur == 0 and w.cash_eur + o.base_size * o.avg_price + o.fee_eur == D(100)


def test_maker_order_expires(fake_cb):
    tid = make_trader()
    signal(tid)
    run()
    with session_scope() as s:
        o = s.scalar(select(Order))
        o.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    services.poll_orders()
    with session_scope() as s:
        assert s.scalar(select(Order)).status == "CANCELLED"
        w = portfolio.get_wallet(s, "simulation")
        assert w.cash_eur == D(100) and w.reserved_eur == 0


def test_drawdown_suspension_persists_until_review(fake_cb, taker_style):
    tid = make_trader()
    with session_scope() as s:
        w = portfolio.get_wallet(s, "simulation")
        w.peak_equity = D(111)
    signal(tid)
    assert run()[-1].reason == "drawdown_limit"
    assert state.drawdown_suspended("simulation")
    signal(tid)
    assert run()[-1].reason == "strategy_suspended"
