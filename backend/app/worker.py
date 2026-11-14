"""Servicio de monitorización y ejecución, independiente del frontend y de la API.

python -m app.worker
"""
from __future__ import annotations

import asyncio
import signal
import threading
import time
import uuid
from datetime import datetime, timezone

from . import activation, market, services, state
from .audit import audit, log, setup_logging
from .coinbase.ws import run_ticker
from .config import get_settings
from .db import init_engine

_stop = threading.Event()


def _ws_thread() -> None:
    cfg = get_settings()
    loop = asyncio.new_event_loop()
    stop = asyncio.Event()

    def _watch():
        while not _stop.is_set():
            time.sleep(1)
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=_watch, daemon=True).start()
    loop.run_until_complete(run_ticker(cfg.coinbase_ws_url, services.watched_products, market.store_quote, market.get_client().creds, stop))


def boot() -> None:
    setup_logging()
    init_engine()
    boot_id = uuid.uuid4().hex
    state.set_value("worker_boot_id", boot_id)
    audit("worker_started", actor="worker", boot_id=boot_id, mode=state.get_mode())
    # Tras reiniciar, el modo real NO se reanuda salvo autorización persistente válida
    if state.get_mode() == "live":
        ok, why = activation.live_authorized()
        if not ok:
            activation.deactivate(f"Reinicio: {why}", actor="worker")
    if state.get_value("mode") is None:
        state.set_mode("simulation")


def main() -> None:  # pragma: no cover - bucle de producción
    boot()
    cfg = get_settings()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: _stop.set())
    threading.Thread(target=_ws_thread, daemon=True).start()
    last_products = last_eval = 0.0
    while not _stop.is_set():
        started = time.time()
        state.set_value("worker_heartbeat", datetime.now(timezone.utc).isoformat())
        try:
            if time.time() - last_products > 6 * 3600:
                try:
                    n = market.sync_products()
                    state.set_value("coinbase_public_ok", True)
                    log.info("Productos EUR sincronizados: %s", n)
                    last_products = time.time()
                except Exception as e:  # noqa: BLE001
                    state.set_value("coinbase_public_ok", False)
                    audit("products_sync_failed", level="WARN", actor="worker", error=str(e))
            services.enforce_live_authorization()
            if time.time() - last_eval > 24 * 3600:
                from sqlalchemy import select
                from .db import session_scope
                from .models import Trader

                with session_scope() as s:
                    ids = [t.id for t in s.scalars(select(Trader))]
                for tid in ids:
                    try:
                        services.evaluate_trader(tid)
                    except Exception as e:  # noqa: BLE001
                        audit("trader_eval_failed", level="WARN", actor="worker", trader_id=tid, error=str(e))
                last_eval = time.time()
            if not state.emergency_stopped():
                services.poll_sources()
                services.process_pending()
                services.poll_orders()
            lm = services.ledger_mode(state.get_mode())
            if services.last_snapshot_age(lm) > 300:
                services.snapshot(lm)
        except Exception as e:  # noqa: BLE001
            audit("worker_cycle_error", level="CRITICAL", actor="worker", error=str(e))
        _stop.wait(max(1.0, cfg.poll_interval_seconds - (time.time() - started)))
    audit("worker_stopped", actor="worker")


if __name__ == "__main__":
    main()
