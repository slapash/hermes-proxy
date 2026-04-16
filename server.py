import hashlib
import hmac
import json as _json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Load .env manually (no python-dotenv dependency)
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).parent / ".env"
if _ENV_PATH.exists():
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _, _val = _line.partition("=")
            os.environ.setdefault(_key.strip(), _val.strip())

# ---------------------------------------------------------------------------
# Config validation at import time (before uvicorn starts serving)
# ---------------------------------------------------------------------------
_HERMES_PROXY_PASSWORD = os.environ.get("HERMES_PROXY_PASSWORD", "")
_API_SERVER_KEY = os.environ.get("API_SERVER_KEY", "")
_API_SERVER_URL = os.environ.get("API_SERVER_URL", "http://127.0.0.1:8642")
_STATE_DB_PATH = os.environ.get("STATE_DB_PATH", "/Users/clawd/.hermes/state.db")

if not _HERMES_PROXY_PASSWORD:
    raise RuntimeError("HERMES_PROXY_PASSWORD is unset or empty — refusing to start")
if not _API_SERVER_KEY:
    raise RuntimeError("API_SERVER_KEY is unset or empty — refusing to start")

# Stable signing key derived from password.
# Tokens are HMAC-signed so they survive proxy restarts — no server-side token store needed.
# Rotating the password invalidates all existing cookies automatically.
_SIGNING_KEY = hashlib.sha256(_HERMES_PROXY_PASSWORD.encode()).digest()

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
browser_sessions = {}  # type: dict  # cookie_token -> hermes_session_id

# Rate limiting: { ip: {"count": int, "window_start": float} }
_login_attempts = {}  # type: dict
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60  # seconds

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token() -> str:
    """Generate a signed auth token: <random_hex>.<hmac_sig>"""
    payload = secrets.token_hex(32)
    sig = hmac.new(_SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_token(token: str) -> bool:
    """Verify a signed token without hitting any server state."""
    if not token or "." not in token:
        return False
    payload, _, sig = token.rpartition(".")
    expected = hmac.new(_SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _get_token(request: Request) -> Optional[str]:
    return request.cookies.get("hermes-proxy-auth")


def _is_authenticated(request: Request) -> bool:
    token = _get_token(request)
    return token is not None and _verify_token(token)


def _auth_error() -> JSONResponse:
    return JSONResponse({"error": "Not authenticated"}, status_code=401)


def _check_rate_limit(ip: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = time.monotonic()
    entry = _login_attempts.get(ip)
    if entry is None:
        _login_attempts[ip] = {"count": 1, "window_start": now}
        return True
    if now - entry["window_start"] > _RATE_LIMIT_WINDOW:
        _login_attempts[ip] = {"count": 1, "window_start": now}
        return True
    entry["count"] += 1
    if entry["count"] > _RATE_LIMIT_MAX:
        return False
    return True


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="hermes-proxy-auth",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
        max_age=2592000,
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key="hermes-proxy-auth", path="/")


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.post("/auth/login")
async def auth_login(request: Request) -> Response:
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        return JSONResponse({"error": "Too many attempts"}, status_code=429)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    password = body.get("password", "")
    if not hmac.compare_digest(password, _HERMES_PROXY_PASSWORD):
        return JSONResponse({"error": "Wrong password"}, status_code=401)

    token = _make_token()
    response = JSONResponse({"ok": True})
    _set_auth_cookie(response, token)
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request) -> Response:
    token = _get_token(request)
    if token:
        browser_sessions.pop(token, None)
    response = JSONResponse({"ok": True})
    _clear_auth_cookie(response)
    return response


@app.get("/auth/status")
async def auth_status(request: Request) -> Response:
    return JSONResponse({"authenticated": _is_authenticated(request)})


@app.get("/api/session/validate")
async def session_validate(request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    token = _get_token(request)
    session_id = browser_sessions.get(token) if token else None
    return JSONResponse({"valid": session_id is not None, "session_id": session_id})


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def api_chat(request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()

    token = _get_token(request)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    message = body.get("message", "")
    session_id_override = body.get("session_id")

    if "session_id" in body:
        if session_id_override:
            browser_sessions[token] = session_id_override
        else:
            # Explicit null = new session requested, clear the mapping
            browser_sessions.pop(token, None)

    hermes_session_id = browser_sessions.get(token)

    upstream_body = {
        "model": "hermes-agent",
        "messages": [{"role": "user", "content": message}],
        "stream": True,
    }

    upstream_headers = {
        "Authorization": f"Bearer {_API_SERVER_KEY}",
        "Content-Type": "application/json",
    }
    if hermes_session_id:
        upstream_headers["X-Hermes-Session-Id"] = hermes_session_id

    async def generate_with_capture():
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST",
                f"{_API_SERVER_URL}/v1/chat/completions",
                json=upstream_body,
                headers=upstream_headers,
            ) as upstream_response:
                new_session_id = upstream_response.headers.get("x-hermes-session-id")
                if new_session_id and token:
                    browser_sessions[token] = new_session_id

                async for chunk in upstream_response.aiter_bytes():
                    yield chunk

                # After stream ends, emit a synthetic SSE event with the captured
                # session ID so the browser can store it regardless of whether this
                # was the first message (headers are locked at stream start).
                if new_session_id:
                    payload = _json.dumps({"hermes_session_id": new_session_id})
                    yield f"event: session\ndata: {payload}\n\n".encode()

    response_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    # Best-effort: include session ID in headers if already known (second+ message).
    # Browser MUST also parse the session SSE event for first-message case.
    current_session = browser_sessions.get(token) or ""
    if current_session:
        response_headers["X-Hermes-Session-Id"] = current_session

    return StreamingResponse(
        generate_with_capture(),
        media_type="text/event-stream",
        headers=response_headers,
    )


@app.get("/api/sessions")
async def api_sessions(request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()

    try:
        conn = sqlite3.connect(_STATE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Pull first user message to use as title when title is NULL.
        # Exclude Open WebUI system-generated sessions (### Task: prefix).
        cur.execute(
            """
            SELECT s.id, s.title, s.started_at, s.ended_at, s.message_count, s.model,
                   (SELECT m.content FROM messages m
                    WHERE m.session_id = s.id AND m.role = 'user'
                    ORDER BY m.timestamp ASC LIMIT 1) AS first_msg
            FROM sessions s
            WHERE s.source = 'api_server'
            ORDER BY s.started_at DESC LIMIT 50
            """
        )
        rows = []
        for row in cur.fetchall():
            r = dict(row)
            first_msg = r.pop("first_msg", None) or ""
            # Skip Open WebUI system sessions
            if first_msg.startswith("### Task:"):
                continue
            # Use first user message as display title when DB title is absent
            if not r.get("title") and first_msg:
                r["title"] = first_msg[:72].strip()
            rows.append(r)
        conn.close()
        return JSONResponse(rows)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/sessions/{session_id}/messages")
async def api_session_messages(session_id: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()

    try:
        conn = sqlite3.connect(_STATE_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT role, content, timestamp FROM messages "
            "WHERE session_id = ? AND role IN ('user', 'assistant') "
            "AND content IS NOT NULL AND content != '' "
            "ORDER BY timestamp ASC",
            (session_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return JSONResponse(rows)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# Static files + root
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)


@app.get("/")
async def root(request: Request) -> Response:
    index_file = _STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse({"error": "index.html not found"}, status_code=404)
    return Response(
        content=index_file.read_bytes(),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
