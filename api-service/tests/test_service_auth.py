"""Real RSA JWT validation and API ownership tests; no identity/provider network calls."""

import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from app.config import Settings, get_settings


@pytest.fixture
def jwt_setup(client, monkeypatch):
    from app import security

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("AUTH_MODE", "jwt")
    monkeypatch.setenv("JWT_ISSUER", "https://identity.example.test/tenant/v2.0")
    monkeypatch.setenv("JWT_AUDIENCE", "meeting-api")
    monkeypatch.setenv("JWT_JWKS_URL", "https://identity.example.test/tenant/keys")
    get_settings.cache_clear()

    class Keys:
        def get_signing_key_from_jwt(self, token):
            assert jwt.get_unverified_header(token)["kid"] == "test-signing-key"
            return SimpleNamespace(key=key.public_key())

    monkeypatch.setattr(security, "signing_keys", lambda url: Keys())

    def issue(subject="alice", **changes):
        payload = {
            "iss": get_settings().jwt_issuer,
            "aud": "meeting-api",
            "sub": subject,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "roles": ["Meeting.Access"],
        }
        payload.update(changes)
        payload = {k: v for k, v in payload.items() if v is not None}
        token = jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-signing-key"})
        return {"Authorization": "Bearer " + token}

    return issue


def test_jwt_identity_ownership_and_global_admin_endpoints(client, jwt_setup):
    alice, bob = jwt_setup(), jwt_setup("bob")
    assert client.get("/api/auth/me", headers=alice).status_code == 200
    mid = client.post(
        "/api/meetings", headers=alice, json={"title": "Private meeting", "consent_confirmed": True}
    ).json()["id"]
    for suffix in ["", "/transcript", "/jobs", "/coverage", "/audit", "/snapshot"]:
        assert client.get(f"/api/meetings/{mid}{suffix}", headers=bob).status_code == 403
    assert client.get("/api/meetings", headers=bob).json() == []
    assert client.post(f"/api/meetings/{mid}/start", headers=bob).status_code == 403
    assert client.get("/api/storage", headers=alice).status_code == 403
    assert client.post("/api/storage/backup", headers=alice).status_code == 403
    admin = jwt_setup("operator", roles=["Meeting.Access", "Meeting.Admin"])
    assert client.get("/api/storage", headers=admin).status_code == 200
    # Administrative backup permission does not bypass individual meeting ownership.
    assert client.get(f"/api/meetings/{mid}", headers=admin).status_code == 403


@pytest.mark.parametrize(
    "claims",
    [
        {"exp": 1},
        {"exp": None},
        {"iat": None},
        {"iat": 9999999999},
        {"nbf": 9999999999},
        {"aud": "other-api"},
        {"iss": "https://other.test"},
        {"sub": ""},
        {"sub": None},
        {"roles": "Meeting.Access"},
    ],
)
def test_invalid_jwt_rejected(client, jwt_setup, claims):
    assert client.get("/api/auth/me", headers=jwt_setup(**claims)).status_code == 401


def test_jwt_permission_scope_and_no_api_key_fallback(client, jwt_setup):
    assert client.get("/api/auth/me", headers=jwt_setup(roles=[])).status_code == 403
    assert (
        client.get("/api/auth/me", headers=jwt_setup(roles=[], scp="Meeting.Access")).status_code
        == 200
    )
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer test-token"}).status_code
        == 401
    )
    forged = jwt.encode(
        {"sub": "alice"},
        "not-a-valid-RSA-key" * 3,
        algorithm="HS256",
        headers={"kid": "test-signing-key"},
    )
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer " + forged}).status_code == 401
    )


def test_bad_signature_and_key_outage(client, jwt_setup, monkeypatch):
    from app import security

    valid = jwt_setup()["Authorization"].split()[1]
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    claims = jwt.decode(valid, options={"verify_signature": False})
    forged = jwt.encode(claims, other, algorithm="RS256", headers={"kid": "test-signing-key"})
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer " + forged}).status_code == 401
    )

    class Offline:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientConnectionError("private identity connection")

    monkeypatch.setattr(security, "signing_keys", lambda url: Offline())
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer " + valid})
    assert response.status_code == 503
    assert "private identity" not in response.text


def test_distinct_service_keys_and_admin_scope(client, monkeypatch):
    monkeypatch.setenv("API_CLIENT_KEYS", json.dumps({"portal": "p" * 40, "integration": "i" * 40}))
    monkeypatch.setenv("API_ADMIN_CLIENTS", '["integration"]')
    get_settings.cache_clear()
    portal = {"Authorization": "Bearer " + "p" * 40}
    other = {"Authorization": "Bearer " + "i" * 40}
    mid = client.post(
        "/api/meetings", headers=portal, json={"title": "Portal only", "consent_confirmed": True}
    ).json()["id"]
    assert client.get(f"/api/meetings/{mid}", headers=other).status_code == 403
    assert client.get("/api/storage", headers=portal).status_code == 403
    assert client.get("/api/storage", headers=other).status_code == 200
    assert (
        client.get("/api/auth/me", headers={"Authorization": "Bearer test-token"}).status_code
        == 401
    )


def test_api_key_config_validation_and_separate_storage():
    from app.config import BACKEND_DIR

    assert Settings(_env_file=None).storage_dir == BACKEND_DIR / "var" / "audio"
    for mapping in [{"one": "short"}, {"one": "x" * 40, "two": "x" * 40}, {"bad actor": "x" * 40}]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, api_client_keys=mapping)


def test_notes_are_owner_scoped(client, jwt_setup):
    from uuid import uuid4

    mid = str(uuid4())
    assert (
        client.post(
            "/api/recaps", headers=jwt_setup(), json={"id": mid, "title": "Alice notes"}
        ).status_code
        == 201
    )
    assert client.get(f"/api/recaps/{mid}", headers=jwt_setup("bob")).status_code == 403
