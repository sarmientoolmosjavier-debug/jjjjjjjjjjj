"""Autenticación JWT para Coinbase Advanced Trade (claves CDP ECDSA, ES256).

Formato según https://docs.cdp.coinbase.com/get-started/authentication/jwt-authentication :
cabecera ``{"alg": "ES256", "kid": <nombre de clave>, "nonce": <hex aleatorio>}`` y carga
``{"sub": <nombre>, "iss": "cdp", "nbf": now, "exp": now+120, "uri": "GET api.coinbase.com/api/v3/..."}``.
Para WebSocket se omite ``uri``.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from ..config import Settings


class CredentialsError(RuntimeError):
    pass


@dataclass(frozen=True)
class CdpCredentials:
    key_name: str
    _private_key_pem: str  # nunca se registra ni se serializa

    def __repr__(self) -> str:  # evita filtrar la clave en logs/tracebacks
        return f"CdpCredentials(key=…{self.key_name[-6:]})"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.key_name.encode()).hexdigest()[:16]

    def private_key(self):
        try:
            key = serialization.load_pem_private_key(self._private_key_pem.encode(), password=None)
        except Exception as e:  # noqa: BLE001
            raise CredentialsError(
                "No se pudo leer la clave privada. Crea la clave CDP con algoritmo ECDSA (no Ed25519)."
            ) from e
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise CredentialsError("La clave debe ser ECDSA (ES256). Las claves Ed25519 no sirven para Advanced Trade.")
        return key


def load_credentials(cfg: Settings) -> CdpCredentials | None:
    name, pem = cfg.coinbase_api_key_name, cfg.coinbase_api_private_key.get_secret_value() if cfg.coinbase_api_private_key else None
    if cfg.coinbase_api_key_file and not (name and pem) and os.path.exists(cfg.coinbase_api_key_file):
        with open(cfg.coinbase_api_key_file, encoding="utf-8") as f:
            data = json.load(f)
        name, pem = data.get("name"), data.get("privateKey")
    if not name or not pem:
        return None
    pem = pem.replace("\\n", "\n").strip()
    return CdpCredentials(key_name=name, _private_key_pem=pem)


def build_jwt(creds: CdpCredentials, method: str | None = None, host: str | None = None, path: str | None = None) -> str:
    now = int(time.time())
    payload = {"sub": creds.key_name, "iss": "cdp", "nbf": now, "exp": now + 120}
    if method and host and path:
        payload["uri"] = f"{method.upper()} {host}{path}"
    headers = {"kid": creds.key_name, "nonce": secrets.token_hex(16)}
    return jwt.encode(payload, creds.private_key(), algorithm="ES256", headers=headers)
