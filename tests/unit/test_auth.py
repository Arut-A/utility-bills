"""Auth layer tests: Google token verification paths + session JWT + API-key fallback."""

import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "api-server"))

SECRET = "test-secret"
WEB_CLIENT = "web-client-id.apps.googleusercontent.com"
ALLOWED = "allowed@example.com"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setenv("GOOGLE_WEB_CLIENT_ID", WEB_CLIENT)
    monkeypatch.setenv("ALLOWED_EMAILS", ALLOWED)
    monkeypatch.setenv("API_SECRET_KEY", "internal-key")
    import auth
    importlib.reload(auth)
    app = FastAPI()
    app.include_router(auth.router)

    from fastapi import Depends

    @app.get("/protected", dependencies=[Depends(auth.require_auth)])
    async def protected():
        return {"ok": True}

    return TestClient(app), auth


def _mock_verify(claims):
    return patch("google.oauth2.id_token.verify_oauth2_token", return_value=claims)


def test_valid_token_allowed_email(client):
    c, _ = client
    with _mock_verify({"email": ALLOWED, "email_verified": True}):
        r = c.post("/api/auth/google", json={"id_token": "x"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == ALLOWED
    claims = jwt.decode(body["session_token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == ALLOWED


def test_wrong_email_rejected(client):
    c, _ = client
    with _mock_verify({"email": "intruder@gmail.com", "email_verified": True}):
        r = c.post("/api/auth/google", json={"id_token": "x"})
    assert r.status_code == 403


def test_unverified_email_rejected(client):
    c, _ = client
    with _mock_verify({"email": ALLOWED, "email_verified": False}):
        r = c.post("/api/auth/google", json={"id_token": "x"})
    assert r.status_code == 403


def test_forged_token_rejected(client):
    c, _ = client
    with patch("google.oauth2.id_token.verify_oauth2_token",
               side_effect=ValueError("bad signature/audience")):
        r = c.post("/api/auth/google", json={"id_token": "forged"})
    assert r.status_code == 403


def test_session_jwt_grants_access(client):
    c, _ = client
    with _mock_verify({"email": ALLOWED, "email_verified": True}):
        token = c.post("/api/auth/google", json={"id_token": "x"}).json()["session_token"]
    r = c.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_expired_session_rejected(client):
    c, _ = client
    expired = jwt.encode(
        {"sub": ALLOWED, "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
        SECRET, algorithm="HS256")
    r = c.get("/protected", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_tampered_session_rejected(client):
    c, _ = client
    other = jwt.encode(
        {"sub": ALLOWED, "exp": datetime.now(timezone.utc) + timedelta(days=1)},
        "wrong-secret", algorithm="HS256")
    r = c.get("/protected", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 401


def test_api_key_fallback_still_works(client):
    c, _ = client
    assert c.get("/protected", headers={"X-API-Key": "internal-key"}).status_code == 200
    assert c.get("/protected", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/protected").status_code == 401


def test_rate_limit(client):
    c, _ = client
    with patch("google.oauth2.id_token.verify_oauth2_token",
               side_effect=ValueError("nope")):
        codes = [c.post("/api/auth/google", json={"id_token": "x"}).status_code
                 for _ in range(7)]
    assert 429 in codes


def test_unconfigured_auth_returns_503(client, monkeypatch):
    c, auth_mod = client
    monkeypatch.setattr(auth_mod, "SESSION_SECRET", "")
    r = c.post("/api/auth/google", json={"id_token": "x"})
    assert r.status_code == 503
