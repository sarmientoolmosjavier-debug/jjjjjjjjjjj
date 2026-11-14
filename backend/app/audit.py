"""Auditoría, logging con redacción de secretos y notificaciones de fallos."""
from __future__ import annotations

import logging
import re
import threading
from typing import Any

import httpx

from .config import get_settings
from .db import session_scope
from .models import AuditLog

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----", re.S),
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.=]+"),
    re.compile(r"(organizations/[^/\s]+/apiKeys/)[^\s\"']+"),
    re.compile(r"(\"?(?:private_?key|privateKey|secret|token|password)\"?\s*[:=]\s*\"?)[^\s\",}]+", re.I),
]


def redact(text: str) -> str:
    for p in _SECRET_PATTERNS:
        text = p.sub(lambda m: (m.group(1) if m.groups() else "") + "***", text)
    return text


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("***" if re.search(r"key|secret|token|password|signature", str(k), re.I) and k not in ("key_permissions",) else _redact_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(str(a)) for a in record.args) if isinstance(record.args, tuple) else record.args
        return True


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in logging.getLogger().handlers:
        h.addFilter(RedactingFilter())


log = logging.getLogger("copytrader")


def audit(event: str, actor: str = "engine", level: str = "INFO", **details: Any) -> None:
    details = _redact_obj(details)
    getattr(log, {"INFO": "info", "WARN": "warning", "CRITICAL": "error"}.get(level, "info"))(
        "%s %s %s", actor, event, details
    )
    with session_scope() as s:
        s.add(AuditLog(actor=actor, event=event, level=level, details=_jsonable(details)))
    if level == "CRITICAL":
        notify(f"[CRÍTICO] {event}: {details}")


def _jsonable(o: Any) -> Any:
    from decimal import Decimal
    from datetime import datetime

    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, datetime):
        return o.isoformat()
    return o


def notify(message: str) -> None:
    """Notificación de fallos a un webhook gratuito (ntfy, Discord, etc.). Nunca bloquea."""
    url = get_settings().notify_webhook_url
    if not url:
        return

    def _send():
        try:
            httpx.post(url, content=redact(message).encode(), timeout=5)
        except Exception as e:  # pragma: no cover - mejor esfuerzo
            log.warning("No se pudo enviar la notificación: %s", e)

    threading.Thread(target=_send, daemon=True).start()
