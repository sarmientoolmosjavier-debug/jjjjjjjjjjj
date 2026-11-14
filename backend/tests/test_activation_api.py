import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import activation, config, state


def keys(items):
    return {i["key"]: i["ok"] for i in items}


@pytest.fixture
def worker_alive():
    state.set_value("worker_boot_id", "boot-1")
    state.set_value("worker_heartbeat", datetime.now(timezone.utc).isoformat())


def test_live_blocked_without_master_switch(fake_cb, worker_alive):
    k = keys(activation.checklist())
    assert k["master_switch"] is False
    assert activation.activate(activation.CONFIRMATION_PHRASE)["activated"] is False
    assert state.get_mode() == "simulation"


def test_live_blocked_if_key_can_transfer(fake_cb, worker_alive, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ALLOWED", "true")
    config.get_settings.cache_clear()
    fake_cb.perms["can_transfer"] = True
    k = keys(activation.checklist())
    assert k["perm_no_transfer"] is False


def test_live_activation_and_restart_reverts(fake_cb, worker_alive, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ALLOWED", "true")
    config.get_settings.cache_clear()
    items = activation.checklist()
    assert all(i["ok"] for i in items), items
    assert activation.activate("si")["activated"] is False  # frase incorrecta
    assert activation.activate(activation.CONFIRMATION_PHRASE)["activated"] is True
    assert state.get_mode() == "live" and activation.live_authorized()[0]
    # Reinicio del worker → nuevo boot id → el modo real no se reanuda
    state.set_value("worker_boot_id", "boot-2")
    from app import services

    services.enforce_live_authorization()
    assert state.get_mode() == "simulation"


def test_persistent_authorization_survives_restart_only_if_enabled(fake_cb, worker_alive, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ALLOWED", "true")
    monkeypatch.setenv("LIVE_PERSIST_AUTH", "true")
    config.get_settings.cache_clear()
    assert activation.activate(activation.CONFIRMATION_PHRASE)["activated"]
    state.set_value("worker_boot_id", "boot-2")
    assert activation.live_authorized()[0]
    auth = state.get_value("live_authorization")
    auth["expires_at"] = "2099-01-01T00:00:00+00:00"  # manipulación → firma inválida
    state.set_value("live_authorization", auth)
    assert activation.live_authorized() == (False, "Firma de autorización inválida")


def test_economic_viability_blocks_live(fake_cb, worker_alive, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ALLOWED", "true")
    config.get_settings.cache_clear()
    fake_cb.fees = {"maker_fee_rate": "0.006", "taker_fee_rate": "0.012", "pricing_tier": "Intro 1"}
    assert keys(activation.checklist())["economic_viability"] is False


def test_live_order_flow_uses_preview_and_client_order_id(fake_cb, worker_alive, monkeypatch):
    from decimal import Decimal as D
    from sqlalchemy import select
    from app.db import session_scope
    from app.models import Order
    from tests.test_engine import make_trader, signal
    from app import services

    monkeypatch.setenv("LIVE_TRADING_ALLOWED", "true")
    config.get_settings.cache_clear()
    state.update_limits({"order_style": "taker"})
    assert activation.activate(activation.CONFIRMATION_PHRASE)["activated"]
    tid = make_trader()
    signal(tid)
    services.process_pending()
    assert len(fake_cb.created) == 1
    body = fake_cb.created[0]
    assert body["order_configuration"]["market_market_ioc"]["quote_size"] == "15"
    assert any(r.url.path.endswith("/orders/preview") for r in fake_cb.requests)
    services.poll_orders()
    with session_scope() as s:
        o = s.scalar(select(Order))
        assert o.mode == "live" and o.status == "FILLED" and o.client_order_id == body["client_order_id"] and o.fee_eur == D("0.05")


# ------------------------------------------------------------------ API
@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "tok")
    monkeypatch.setenv("WEBHOOK_SECRET", "whs")
    config.get_settings.cache_clear()
    from app.api import app

    with TestClient(app) as c:
        yield c


H = {"X-Admin-Token": "tok"}


def test_summary_starts_with_100_virtual(api, public_cb):
    r = api.get("/api/summary").json()
    assert r["mode"] == "simulation" and r["equity"] == "100.00" and r["simulated"] is True


def test_admin_token_required(api, public_cb):
    assert api.put("/api/settings", json={"max_round_trip_cost_pct": "1.2"}).status_code == 401
    assert api.put("/api/settings", json={"max_round_trip_cost_pct": "1.2"}, headers=H).status_code == 200
    assert api.put("/api/settings", json={"max_total_exposure_eur": "90"}, headers=H).status_code == 400


def test_mode_endpoint_cannot_enable_live(api, public_cb):
    assert api.post("/api/mode", json={"mode": "live"}, headers=H).status_code == 400
    assert api.post("/api/mode", json={"mode": "observation"}, headers=H).json()["mode"] == "observation"


def test_viability_reports_inviable_with_conservative_fees(api, public_cb):
    v = api.get("/api/viability").json()
    assert v["fee_source"] == "estimacion_conservadora" and v["any_viable"] is False and v["message"]


def test_trader_cannot_be_enabled_unverified(api, public_cb):
    r = api.post("/api/traders", json={"source": "hyperliquid", "external_id": "0x" + "a" * 40}, headers=H)
    tid = r.json()["id"]
    assert api.patch(f"/api/traders/{tid}", json={"enabled": True}, headers=H).status_code == 400
    assert api.post("/api/traders", json={"source": "hyperliquid", "external_id": "nope"}, headers=H).status_code == 400


def test_webhook_signature(api, public_cb):
    api.post("/api/traders", json={"source": "manual", "external_id": "mio"}, headers=H)
    body = json.dumps({"id": "1", "trader": "mio", "asset": "BTC", "side": "BUY", "fraction": 0.1, "timestamp": time.time()}).encode()
    assert api.post("/api/signals/webhook", content=body, headers={"X-Signature": "bad"}).status_code == 401
    sig = hmac.new(b"whs", body, hashlib.sha256).hexdigest()
    r = api.post("/api/signals/webhook", content=body, headers={"X-Signature": sig}).json()
    assert r["accepted"] is True
    r = api.post("/api/signals/webhook", content=body, headers={"X-Signature": sig}).json()
    assert r["duplicate"] is True


def test_emergency_stop_endpoint(api, public_cb):
    assert api.post("/api/emergency-stop", headers=H).json()["emergency_stop"] is True
    assert api.get("/api/summary").json()["emergency_stop"] is True
    api.post("/api/emergency-stop/release", headers=H)
    assert api.get("/api/summary").json()["emergency_stop"] is False
