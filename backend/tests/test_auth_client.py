import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from app.coinbase.auth import CdpCredentials, CredentialsError, build_jwt
from tests.conftest import ec_pem


def test_jwt_es256_claims():
    pem = ec_pem()
    creds = CdpCredentials("organizations/o/apiKeys/k", pem)
    tok = build_jwt(creds, "GET", "api.coinbase.com", "/api/v3/brokerage/accounts")
    hdr = jwt.get_unverified_header(tok)
    assert hdr["alg"] == "ES256" and hdr["kid"] == creds.key_name and len(hdr["nonce"]) == 32
    pub = creds.private_key().public_key()
    claims = jwt.decode(tok, pub, algorithms=["ES256"], options={"verify_aud": False})
    assert claims["iss"] == "cdp" and claims["sub"] == creds.key_name
    assert claims["uri"] == "GET api.coinbase.com/api/v3/brokerage/accounts"
    assert claims["exp"] - claims["nbf"] == 120


def test_ed25519_rejected():
    k = ed25519.Ed25519PrivateKey.generate()
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    with pytest.raises(CredentialsError):
        build_jwt(CdpCredentials("n", pem))


def test_repr_hides_secret():
    pem = ec_pem()
    c = CdpCredentials("organizations/o/apiKeys/abcdef123456", pem)
    assert "PRIVATE" not in repr(c) and "abcdef" not in repr(c)


def test_redaction():
    from app.audit import redact

    s = redact("Authorization: Bearer abc.def.ghi -----BEGIN EC PRIVATE KEY-----\nXYZ\n-----END EC PRIVATE KEY-----")
    assert "abc.def" not in s and "XYZ" not in s


def test_products_sync_filters_eur(fake_cb):
    from app.market import get_product

    assert get_product("BTC-EUR").quote_min_size == 1
    assert get_product("BTC-USD") is None


def test_client_sends_bearer_and_fee_rates(fake_cb):
    from app import market

    maker, taker, src = market.fee_rates()
    assert src == "coinbase" and str(maker) == "0.0025" and str(taker) == "0.0040"
    req = [r for r in fake_cb.requests if r.url.path.endswith("/transaction_summary")][0]
    assert req.headers["Authorization"].startswith("Bearer ")


def test_default_fees_are_conservative_without_keys(public_cb):
    from app import market

    maker, taker, src = market.fee_rates()
    assert src == "estimacion_conservadora" and taker >= maker > 0
