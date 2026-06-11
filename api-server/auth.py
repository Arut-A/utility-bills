"""
api-server/auth.py
Google Sign-In -> session JWT, single-email allowlist.

POST /api/auth/google  {id_token} -> {session_token, expires_at, email}
require_auth dependency: accepts EITHER a valid Bearer session JWT
(the Android app) OR the X-API-Key header (internal scripts, parser).
"""

import hmac
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import jwt
from google.auth.transport import requests as g_requests
from google.oauth2 import id_token as g_id_token
from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

log = logging.getLogger("auth")

router = APIRouter()

SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
GOOGLE_WEB_CLIENT_ID = os.environ.get("GOOGLE_WEB_CLIENT_ID", "")
ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()
}
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_BEARER_PREFIX = "Bearer "

# ── Rate limit: 5 auth attempts / minute / IP (single-user home server) ──
_attempts: dict[str, deque] = {}
_attempts_lock = threading.Lock()


def _rate_limited(ip: str, limit: int = 5, window: float = 60.0) -> bool:
    now = time.monotonic()
    with _attempts_lock:
        q = _attempts.setdefault(ip, deque())
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
    return False


def _telegram_notice(text_msg: str) -> None:
    """Fire-and-forget login notice to Home Alerts. Never blocks auth."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    def _send():
        try:
            import urllib.parse
            import urllib.request
            data = urllib.parse.urlencode(
                {"chat_id": chat_id, "text": text_msg}).encode()
            urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=data, timeout=10)
        except Exception as exc:  # noqa: BLE001 — notice must never break auth
            log.warning("Telegram login notice failed: %s", exc)

    threading.Thread(target=_send, daemon=True).start()


class GoogleAuthBody(BaseModel):
    id_token: str


class SessionResponse(BaseModel):
    session_token: str
    expires_at: str
    email: str


@router.post("/api/auth/google", response_model=SessionResponse)
async def auth_google(body: GoogleAuthBody, request: Request):
    if not SESSION_SECRET or not GOOGLE_WEB_CLIENT_ID or not ALLOWED_EMAILS:
        log.error("Auth not configured (SESSION_SECRET/GOOGLE_WEB_CLIENT_ID/ALLOWED_EMAILS)")
        raise HTTPException(status_code=503, detail="Auth not configured")

    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        raise HTTPException(status_code=429, detail="Too many attempts")

    try:
        claims = g_id_token.verify_oauth2_token(
            body.id_token, g_requests.Request(), audience=GOOGLE_WEB_CLIENT_ID)
    except Exception as exc:  # noqa: BLE001 — any verify failure is a 403, never a 500
        log.warning("Google token rejected from %s: %s: %s",
                    ip, type(exc).__name__, exc)
        raise HTTPException(status_code=403, detail="Forbidden")

    email = str(claims.get("email", "")).lower()
    if not claims.get("email_verified") or email not in ALLOWED_EMAILS:
        log.warning("Login denied for %r from %s", email, ip)
        raise HTTPException(status_code=403, detail="Forbidden")

    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    token = jwt.encode(
        {"sub": email, "exp": expires, "iat": datetime.now(timezone.utc)},
        SESSION_SECRET, algorithm="HS256")

    log.info("Login OK for %s from %s", email, ip)
    _telegram_notice(f"📱 Household Bills app: login OK ({email}, session {SESSION_DAYS}d)")
    return SessionResponse(session_token=token,
                           expires_at=expires.isoformat(), email=email)


def _valid_session(auth_header: str | None) -> bool:
    if not auth_header or not auth_header.startswith(_BEARER_PREFIX):
        return False
    if not SESSION_SECRET:
        return False
    try:
        claims = jwt.decode(auth_header[len(_BEARER_PREFIX):], SESSION_SECRET,
                            algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return False
    return str(claims.get("sub", "")).lower() in ALLOWED_EMAILS


async def require_auth(request: Request,
                       api_key: str = Security(_API_KEY_HEADER)):
    """Bearer session JWT (app) OR X-API-Key (internal scripts)."""
    if _valid_session(request.headers.get("Authorization")):
        return
    expected = os.environ.get("API_SECRET_KEY", "")
    if api_key and expected and hmac.compare_digest(api_key, expected):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")
