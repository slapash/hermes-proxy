import hmac
import json as _json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import FastAPI, Request, Response, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_session_id_ctx: ContextVar[str] = ContextVar("session_id", default="-")

_LOG_FORMAT = os.environ.get("HERMES_PROXY_LOG_FORMAT", "auto").lower()


class _JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "request_id": _request_id.get("-"),
            "session_id": _session_id_ctx.get("-"),
        }
        if record.exc_info and record.exc_info[0] is not None:
            obj["exc"] = self.formatException(record.exc_info)
        # Merge any extra fields passed via logger.info(..., extra={...})
        for key in ("method", "path", "status", "duration_ms", "ip"):
            val = getattr(record, key, None)
            if val is not None:
                obj[key] = val
        return _json.dumps(obj, separators=(",", ":"))


class _TextFormatter(logging.Formatter):
    """Human-readable one-liner with request ID."""

    def format(self, record: logging.LogRecord) -> str:
        rid = _request_id.get("-")
        sid = _session_id_ctx.get("-")
        base = f"{record.levelname:>7} [{rid}|{sid}] {record.getMessage()}"
        if record.exc_info and record.exc_info[0] is not None:
            base += "\n" + self.formatException(record.exc_info)
        return base


def _setup_logging() -> None:
    is_tty = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
    fmt_choice = _LOG_FORMAT
    if fmt_choice == "auto":
        fmt_choice = "text" if is_tty else "json"

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_TextFormatter() if fmt_choice == "text" else _JSONFormatter())
    root = logging.getLogger()
    # Only configure if no handlers are set yet (avoid double-logging in tests)
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Quiet down noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


_setup_logging()

logger = logging.getLogger(__name__)

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
_HERMES_PROXY_PASSWORD=os.environ.get("HERMES_PROXY_PASSWORD", "")
_API_SERVER_KEY = os.environ.get("API_SERVER_KEY", "")
_API_SERVER_URL = os.environ.get("API_SERVER_URL", "http://127.0.0.1:8642")
_STATE_DB_PATH = os.environ.get("STATE_DB_PATH", str(Path.home() / ".hermes" / "state.db"))
_PROXY_META_DB_PATH = os.environ.get(
    "PROXY_META_DB_PATH",
    str(Path.home() / ".hermes" / "proxy_meta.db")
)
_SIGNING_KEY_HEX = os.environ.get("HERMES_PROXY_SIGNING_KEY", "")

if not _HERMES_PROXY_PASSWORD:
    raise RuntimeError("HERMES_PROXY_PASSWORD is unset or empty — refusing to start")
if not _API_SERVER_KEY:
    raise RuntimeError("API_SERVER_KEY is unset or empty — refusing to start")
if not _SIGNING_KEY_HEX:
    raise RuntimeError("HERMES_PROXY_SIGNING_KEY is unset — refusing to start")

try:
    _SIGNING_KEY = bytes.fromhex(_SIGNING_KEY_HEX)
except ValueError:
    raise RuntimeError("HERMES_PROXY_SIGNING_KEY must be a valid hex string")
if len(_SIGNING_KEY) < 32:
    raise RuntimeError("HERMES_PROXY_SIGNING_KEY must be at least 32 bytes (64 hex chars)")


def _init_proxy_meta_db() -> None:
    with sqlite3.connect(_PROXY_META_DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_meta (
                session_id TEXT PRIMARY KEY,
                custom_name TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        # Add archived column if upgrading from older schema
        try:
            conn.execute("ALTER TABLE session_meta ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass  # Column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uploads (
                filename TEXT PRIMARY KEY,
                size INTEGER NOT NULL,
                mime_type TEXT NOT NULL,
                uploaded_at REAL NOT NULL,
                session_id TEXT,
                token TEXT
            )
        """)
        try:
            conn.execute("ALTER TABLE uploads ADD COLUMN token TEXT")
        except Exception:
            pass  # Column already exists
        conn.commit()

_init_proxy_meta_db()


def _meta_db_conn(timeout: int = 5) -> sqlite3.Connection:
    """Open a connection to proxy_meta.db with WAL mode and busy_timeout set."""
    conn = sqlite3.connect(_PROXY_META_DB_PATH, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _conversation_session_ids(session_id: str) -> list[str]:
    """Return root + child session IDs for the displayed conversation.

    Hermes compression children point at the original root via parent_session_id.
    Sidebar actions should operate on the whole visible conversation, not only
    the latest leaf row that happens to be displayed.
    """
    with sqlite3.connect(_STATE_DB_PATH, timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, parent_session_id FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return []
        root_id = row["parent_session_id"] or row["id"]
        rows = conn.execute(
            "SELECT id FROM sessions WHERE id = ? OR parent_session_id = ?",
            (root_id, root_id),
        ).fetchall()
        ids = [r["id"] for r in rows]
        return ids or [session_id]


def _table_has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _assign_pending_uploads(token: str | None, session_id: str | None) -> None:
    """Attach uploads made before first chat to the session created by that chat."""
    if not token or not session_id:
        return
    try:
        with _meta_db_conn() as conn:
            if not _table_has_column(conn, "uploads", "token"):
                return
            conn.execute(
                "UPDATE uploads SET session_id = ? WHERE token = ? AND session_id IS NULL",
                (session_id, token),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("Pending upload reassignment failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
browser_sessions = {}       # type: dict  # cookie_token -> hermes_session_id
_session_created: dict = {} # token -> float (time.time()) for TTL eviction

_SESSION_TTL = 2_592_000    # 30 days — matches cookie max_age

# Session ID format: hermes api_server produces "api-<16 hex chars>"
# CLI sessions use "YYYYMMDD_HHMMSS_<6hex>". Allow word chars + hyphens, 8-80 chars.
_SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{8,80}$')

# Rate limiting: { ip: {"count": int, "window_start": float} }
_login_attempts = {}  # type: dict
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_EVICT_AFTER = _RATE_LIMIT_WINDOW * 10  # evict entries older than 600s

# Chat rate limiting config
_CHAT_RPM = int(os.environ.get("HERMES_PROXY_CHAT_RPM", "30"))
_CHAT_BURST = int(os.environ.get("HERMES_PROXY_CHAT_BURST", "5"))
_CHAT_RATE_LIMITS = {}  # type: dict  # key -> SlidingWindowRateLimiter instance

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI()

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class _MaxBodyMiddleware(BaseHTTPMiddleware):
    """Reject oversized POST bodies before reading them into memory."""
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length:
                default_limit = 1_048_576
                # File uploads are route-limited separately; allow 50 MB files plus
                # small multipart overhead while keeping JSON/chat requests tight.
                upload_limit = _UPLOAD_MAX_SIZE + 1_048_576
                limit = upload_limit if request.url.path == "/api/attachments" else default_limit
                if int(content_length) > limit:
                    return JSONResponse({"error": "Request too large"}, status_code=413)
        return await call_next(request)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_MaxBodyMiddleware)


class _RequestLogMiddleware(BaseHTTPMiddleware):
    """Assign a per-request ID and log method/path/status/duration."""

    async def dispatch(self, request: Request, call_next):
        rid = secrets.token_hex(4)  # 8-char hex
        _request_id.set(rid)
        # Track session_id if available from header
        sid = request.headers.get("x-hermes-session-id", "-")
        if sid:
            _session_id_ctx.set(sid)
        # Also store in request state for downstream handlers
        request.state.request_id = rid

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)

        # Try to resolve session_id from browser_sessions if not in header
        if sid == "-":
            token = request.cookies.get("hermes-proxy-auth")
            if token and token in browser_sessions:
                sid = browser_sessions[token][:12]
                _session_id_ctx.set(sid)

        logger.info(
            "%s %s %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "ip": _get_client_ip(request),
            },
        )
        # Include request ID in response headers for debugging
        response.headers["X-Request-Id"] = rid
        return response


app.add_middleware(_RequestLogMiddleware)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client_ip(request: Request) -> str:
    """Return the real client IP, respecting X-Forwarded-For.

    The leftmost IP in X-Forwarded-For is the original client.
    Falls back to request.client.host for direct connections.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "-"


def _make_token() -> str:
    """Generate a signed auth token: <random_hex>.<hmac_sig>"""
    payload = secrets.token_hex(32)
    sig = hmac.new(_SIGNING_KEY, payload.encode(), "sha256").hexdigest()
    return f"{payload}.{sig}"


def _verify_token(token: str) -> bool:
    """Verify a signed token without hitting any server state."""
    if not token or "." not in token:
        return False
    payload, _, sig = token.rpartition(".")
    expected = hmac.new(_SIGNING_KEY, payload.encode(), "sha256").hexdigest()
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
    # Opportunistically evict stale entries to prevent unbounded growth
    stale = [k for k, e in _login_attempts.items()
             if now - e["window_start"] > _RATE_LIMIT_EVICT_AFTER]
    for k in stale:
        del _login_attempts[k]
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


class _SlidingWindowRateLimiter:
    """Token-bucket rate limiter: RPM requests per minute with burst allowance."""

    def __init__(self, rpm: int, burst: int):
        self._rpm = rpm
        self._burst = burst
        self._tokens = float(burst)  # start with burst capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        # Add tokens for elapsed time (RPM / 60 = tokens per second)
        refill = elapsed * (self._rpm / 60.0)
        self._tokens = min(float(self._burst), self._tokens + refill)
        self._last_refill = now

    def allow(self) -> bool:
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    @property
    def retry_after(self) -> float:
        """Seconds until the next token is available (0 if tokens > 0)."""
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        # Time for one token: 60 / RPM seconds
        return max(0.0, (60.0 / self._rpm) * (1.0 - self._tokens))


def _check_chat_rate_limit(key: str) -> tuple[bool, float]:
    """Check chat rate limit for a key (auth token or IP).

    Returns (allowed, retry_after_seconds).
    Lazily creates a per-key limiter on first use and evicts stale entries.
    """
    now = time.monotonic()
    # Opportunistic eviction of limiters idle > 10 minutes
    stale = [k for k, lim in _CHAT_RATE_LIMITS.items()
             if now - lim._last_refill > 600]
    for k in stale:
        del _CHAT_RATE_LIMITS[k]

    limiter = _CHAT_RATE_LIMITS.get(key)
    if limiter is None:
        limiter = _SlidingWindowRateLimiter(_CHAT_RPM, _CHAT_BURST)
        _CHAT_RATE_LIMITS[key] = limiter
    allowed = limiter.allow()
    retry_after = limiter.retry_after if not allowed else 0.0
    return allowed, retry_after


def _evict_stale_browser_sessions() -> None:
    """Evict browser_sessions entries older than SESSION_TTL (30 days)."""
    cutoff = time.time() - _SESSION_TTL
    stale = [k for k, ts in _session_created.items() if ts < cutoff]
    for k in stale:
        browser_sessions.pop(k, None)
        _session_created.pop(k, None)


def _set_auth_cookie(response: Response, token: str, secure: bool = True) -> None:
    response.set_cookie(
        key="hermes-proxy-auth",
        value=token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=2592000,
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key="hermes-proxy-auth", path="/")


# ---------------------------------------------------------------------------
# Health check (unauthenticated)
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> Response:
    """Unauthenticated health probe for systemd/docker/Cloudflare monitoring."""
    checks = {}
    overall = "ok"

    # 1. state.db readable
    try:
        with sqlite3.connect(_STATE_DB_PATH, timeout=3) as conn:
            conn.execute("SELECT 1 FROM sessions LIMIT 1")
        checks["state_db"] = True
    except Exception as exc:
        logger.warning("healthz: state_db check failed: %s", exc)
        checks["state_db"] = False
        overall = "unhealthy"

    # 2. proxy_meta_db writable (try a write + rollback)
    try:
        with _meta_db_conn(timeout=3) as conn:
            conn.execute("BEGIN")
            conn.execute("INSERT INTO session_meta (session_id, custom_name, updated_at) "
                          "VALUES ('__healthz_check__', '__healthz__', 0)")
            conn.execute("ROLLBACK")
        checks["meta_db"] = True
    except Exception as exc:
        logger.warning("healthz: meta_db check failed: %s", exc)
        checks["meta_db"] = False
        if overall != "unhealthy":
            overall = "degraded"

    # 3. api_server reachable
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{_API_SERVER_URL}/v1/models")
            # Any non-connection-error response means the server is alive
            checks["api_server"] = True
    except Exception as exc:
        logger.warning("healthz: api_server check failed: %s", exc)
        checks["api_server"] = False
        if overall != "unhealthy":
            overall = "degraded"

    status_code = 200 if overall != "unhealthy" else 503
    return JSONResponse({"status": overall, "checks": checks}, status_code=status_code)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.post("/auth/login")
async def auth_login(request: Request) -> Response:
    ip = _get_client_ip(request)
    if not _check_rate_limit(ip):
        return JSONResponse({"error": "Too many attempts"}, status_code=429)

    try:
        body = await request.json()
    except _json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    except Exception as exc:
        logger.error("Unexpected error parsing login body: %s", exc)
        return JSONResponse({"error": "Bad request"}, status_code=400)

    password = body.get("password", "")
    if not hmac.compare_digest(password, _HERMES_PROXY_PASSWORD):
        return JSONResponse({"error": "Wrong password"}, status_code=401)

    token = _make_token()
    response = JSONResponse({"ok": True})
    # Auto-detect HTTPS: set secure=True only if request is HTTPS or behind HTTPS proxy.
    # This fixes "login loop" on plain HTTP localhost while keeping cookies secure in prod.
    proto = request.headers.get("x-forwarded-proto", "").lower()
    is_https = request.url.scheme == "https" or proto == "https"
    _set_auth_cookie(response, token, secure=is_https)
    return response


@app.post("/auth/logout")
async def auth_logout(request: Request) -> Response:
    token = _get_token(request)
    if token:
        browser_sessions.pop(token, None)
        _session_created.pop(token, None)
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

    # Rate limit on authenticated token, fall back to IP
    rate_limit_key = _get_token(request) or _get_client_ip(request)
    allowed, retry_after = _check_chat_rate_limit(rate_limit_key)
    if not allowed:
        return JSONResponse(
            {"error": "Rate limit exceeded", "retry_after": round(retry_after, 1)},
            status_code=429,
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    _evict_stale_browser_sessions()
    token = _get_token(request)

    try:
        body = await request.json()
    except _json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    except Exception as exc:
        logger.error("Unexpected error parsing chat body: %s", exc)
        return JSONResponse({"error": "Bad request"}, status_code=400)

    message = body.get("message", "")
    session_id_override = body.get("session_id")
    attachments = body.get("attachments")

    # --- Validate and assemble attachments -------------------------------------
    attachment_context = ""
    if attachments is not None:
        if not isinstance(attachments, list):
            return JSONResponse({"error": "attachments must be an array"}, status_code=400)
        for idx, att in enumerate(attachments):
            if not isinstance(att, dict):
                return JSONResponse({"error": f"attachment[{idx}] must be an object"}, status_code=400)
            url = att.get("url")
            md = att.get("markdown")
            if not isinstance(url, str) or not url.strip():
                return JSONResponse({"error": f"attachment[{idx}] missing or invalid url"}, status_code=400)
            if not isinstance(md, str):
                return JSONResponse({"error": f"attachment[{idx}] markdown must be a string"}, status_code=400)
            # Allow absolute URLs or local upload paths
            if not (url.startswith("http://") or url.startswith("https://") or url.startswith("/")):
                return JSONResponse({"error": f"attachment[{idx}] url must be http://, https://, or /"}, status_code=400)

            filename = str(att.get("filename") or Path(urlparse(url).path).name or f"attachment-{idx + 1}")
            mime_type = str(att.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
            absolute_url = str(att.get("absolute_url") or "")
            if not absolute_url:
                absolute_url = str(request.base_url).rstrip("/") + url if url.startswith("/") else url

            local_path = ""
            parsed_path = urlparse(url).path
            if parsed_path.startswith("/uploads/"):
                candidate = (_UPLOADS_DIR / Path(parsed_path).name).resolve()
                uploads_root = _UPLOADS_DIR.resolve()
                if candidate.is_file() and uploads_root in candidate.parents:
                    local_path = str(candidate)
            elif isinstance(att.get("local_path"), str):
                candidate = Path(att["local_path"]).resolve()
                uploads_root = _UPLOADS_DIR.resolve()
                if candidate.is_file() and uploads_root in candidate.parents:
                    local_path = str(candidate)

            attachment_context += (
                f"[Attachment {idx + 1}]\n"
                f"filename: {filename}\n"
                f"mime_type: {mime_type}\n"
                f"local_path: {local_path or 'unavailable'}\n"
                f"url: {absolute_url}\n"
                "Use vision_analyze on local_path or url if you need to inspect image pixels. "
                "Use terminal/read_file on local_path for non-image files when appropriate.\n"
                f"markdown: {md.strip()}\n\n"
            )

    user_content = attachment_context + message if attachment_context else message


    if "session_id" in body:
        if session_id_override:
            if not _SESSION_ID_RE.match(str(session_id_override)):
                return JSONResponse({"error": "Invalid session_id"}, status_code=400)
            if token not in _session_created:
                _session_created[token] = time.time()
            browser_sessions[token] = session_id_override
        else:
            # Explicit null = new session requested, clear the mapping
            browser_sessions.pop(token, None)

    hermes_session_id = browser_sessions.get(token)

    upstream_body = {
        "model": "hermes-agent",
        "messages": [{"role": "user", "content": user_content}],
        "stream": True,
    }

    upstream_headers = {
        "Authorization": f"Bearer {_API_SERVER_KEY}",
        "Content-Type": "application/json",
    }
    if hermes_session_id:
        upstream_headers["X-Hermes-Session-Id"] = hermes_session_id

    def _sse_delta(text: str) -> bytes:
        payload = _json.dumps({"choices": [{"delta": {"content": text}}]})
        return f"data: {payload}\n\n".encode()

    async def generate_with_capture():
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST",
                    f"{_API_SERVER_URL}/v1/chat/completions",
                    json=upstream_body,
                    headers=upstream_headers,
                ) as upstream_response:
                    content_type = upstream_response.headers.get("content-type", "").lower()
                    if upstream_response.status_code >= 400 or "text/event-stream" not in content_type:
                        body = (await upstream_response.aread()).decode("utf-8", errors="replace").strip()
                        logger.warning(
                            "Upstream chat returned unexpected response: status=%s content_type=%s body=%r",
                            upstream_response.status_code,
                            content_type or "-",
                            body[:500],
                        )
                        message = f"_(Upstream returned {upstream_response.status_code} {content_type or 'unknown content-type'} instead of SSE)_"
                        yield _sse_delta(message)
                        yield b"data: [DONE]\n\n"
                        return

                    new_session_id = upstream_response.headers.get("x-hermes-session-id")
                    if new_session_id and token:
                        if token not in _session_created:
                            _session_created[token] = time.time()
                        browser_sessions[token] = new_session_id
                        _assign_pending_uploads(token, new_session_id)

                    async for chunk in upstream_response.aiter_bytes():
                        yield chunk

                    # After stream ends, emit a synthetic SSE event with the captured
                    # session ID so the browser can store it regardless of whether this
                    # was the first message (headers are locked at stream start).
                    if new_session_id:
                        payload = _json.dumps({"hermes_session_id": new_session_id})
                        yield f"event: session\ndata: {payload}\n\n".encode()
        except httpx.RequestError as exc:
            logger.warning("Upstream chat request failed: %s", exc)
            yield _sse_delta("_(Upstream chat request failed)_")
            yield b"data: [DONE]\n\n"

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
async def api_sessions(request: Request, offset: int = 0, limit: int = 30) -> Response:
    if not _is_authenticated(request):
        return _auth_error()

    # Clamp params
    offset = max(0, offset)
    limit = max(1, min(limit, 100))

    try:
        with sqlite3.connect(_STATE_DB_PATH, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            # Pull all candidate rows first. Pagination must happen after collapsing
            # compression children and filtering archived/system sessions, otherwise
            # pages can contain duplicate conversation segments or underfill.
            cur.execute(
                """
                SELECT s.id, s.title, s.started_at, s.ended_at, s.end_reason,
                       s.parent_session_id, s.message_count, s.model,
                       (SELECT m.content FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'user'
                        ORDER BY m.timestamp ASC LIMIT 1) AS first_msg
                FROM sessions s
                WHERE s.source = 'api_server'
                ORDER BY s.started_at DESC
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
        # Collapse compression child sessions: Hermes can continue a long chat in
        # child sessions after context compression. The sidebar should show one
        # conversation row, using the latest leaf, not every historical segment.
        roots: dict[str, dict] = {}
        for r in rows:
            root_id = r.get("parent_session_id") or r["id"]
            r["root_session_id"] = root_id
            current = roots.get(root_id)
            if current is None or (r.get("started_at") or 0) > (current.get("started_at") or 0):
                roots[root_id] = r
        rows = sorted(roots.values(), key=lambda r: r.get("started_at") or 0, reverse=True)

        # Overlay custom names from proxy_meta.db. A custom name on a compressed
        # root applies to the latest child row shown for that conversation.
        # Also filter out archived sessions.
        archived_ids: set[str] = set()
        try:
            with _meta_db_conn() as pmconn:
                pmcur = pmconn.cursor()
                pmcur.execute("SELECT session_id, custom_name, archived FROM session_meta")
                custom_names = {}
                for row in pmcur.fetchall():
                    if row["archived"]:
                        archived_ids.add(row["session_id"])
                    if row["custom_name"]:
                        custom_names[row["session_id"]] = row["custom_name"]
            # Remove archived sessions — also remove children whose root is archived
            rows = [r for r in rows if r["id"] not in archived_ids and r.get("root_session_id", r["id"]) not in archived_ids]
            for r in rows:
                root_id = r.get("root_session_id") or r["id"]
                if r["id"] in custom_names:
                    r["title"] = custom_names[r["id"]]
                elif root_id in custom_names:
                    r["title"] = custom_names[root_id]
        except Exception as exc:
            logger.warning("proxy_meta.db read failed (non-fatal): %s", exc)
        total = len(rows)
        paged_rows = rows[offset:offset + limit]
        resp = JSONResponse(paged_rows)
        resp.headers["X-Total-Count"] = str(total)
        resp.headers["X-Offset"] = str(offset)
        resp.headers["X-Limit"] = str(limit)
        return resp
    except Exception as exc:
        logger.error("DB error in api_sessions: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.get("/api/sessions/search")
async def api_sessions_search(q: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    q = (q or "").strip()
    if not q:
        return JSONResponse({"error": "q is required"}, status_code=400)
    try:
        with sqlite3.connect(_STATE_DB_PATH, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT s.id, s.title, s.started_at, s.ended_at, s.message_count, s.model,
                       s.parent_session_id,
                       m.timestamp AS match_offset,
                       m.content AS match_content,
                       (SELECT m2.content FROM messages m2
                        WHERE m2.session_id = s.id AND m2.role = 'user'
                        ORDER BY m2.timestamp ASC LIMIT 1) AS first_msg
                FROM messages_fts fts
                JOIN messages m ON m.rowid = fts.rowid
                JOIN sessions s ON s.id = m.session_id
                WHERE messages_fts MATCH ?
                  AND s.source = 'api_server'
                  AND m.role IN ('user', 'assistant')
                  AND m.content IS NOT NULL AND m.content != ''
                GROUP BY s.id
                ORDER BY s.started_at DESC
                LIMIT 20
                """,
                (q,),
            )
            rows = []
            for row in cur.fetchall():
                r = dict(row)
                first_msg = r.pop("first_msg", None) or ""
                match_content = r.pop("match_content", "") or ""
                if first_msg.startswith("### Task:"):
                    continue
                if not r.get("title") and first_msg:
                    r["title"] = first_msg[:72].strip()
                snippet = match_content.replace("\n", " ").strip()
                snippet = snippet[:80] + ("…" if len(snippet) > 80 else "")
                r["match_snippet"] = snippet
                rows.append(r)
        # Overlay custom names and filter archived sessions
        try:
            with _meta_db_conn() as pmconn:
                pmcur = pmconn.cursor()
                pmcur.execute("SELECT session_id, custom_name, archived FROM session_meta")
                archived_ids = set()
                custom_names = {}
                for r2 in pmcur.fetchall():
                    if r2["archived"]:
                        archived_ids.add(r2["session_id"])
                    if r2["custom_name"]:
                        custom_names[r2["session_id"]] = r2["custom_name"]
            rows = [
                r for r in rows
                if r["id"] not in archived_ids
                and (r.get("parent_session_id") or r["id"]) not in archived_ids
            ]
            for r in rows:
                root_id = r.get("parent_session_id") or r["id"]
                if r["id"] in custom_names:
                    r["title"] = custom_names[r["id"]]
                elif root_id in custom_names:
                    r["title"] = custom_names[root_id]
        except Exception as exc:
            logger.warning("proxy_meta.db overlay failed (non-fatal): %s", exc)
        return JSONResponse(rows)
    except Exception as exc:
        logger.error("Search error: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.get("/api/sessions/{session_id}/messages")
async def api_session_messages(session_id: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()

    try:
        with sqlite3.connect(_STATE_DB_PATH, timeout=5) as conn:
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
        return JSONResponse(rows)
    except Exception as exc:
        logger.error("DB error in api_session_messages: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.put("/api/sessions/{session_id}/rename")
async def api_session_rename(session_id: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    if not _SESSION_ID_RE.match(session_id):
        return JSONResponse({"error": "Invalid session_id"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "name is required"}, status_code=400)
    if len(name) > 100:
        return JSONResponse({"error": "name too long (max 100 chars)"}, status_code=400)
    try:
        with _meta_db_conn() as conn:
            conn.execute(
                "INSERT INTO session_meta (session_id, custom_name, updated_at) "
                "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "custom_name=excluded.custom_name, updated_at=excluded.updated_at",
                (session_id, name, time.time()),
            )
            conn.commit()
        # Lazy eviction: remove session_meta rows whose session_id no longer exists
        # in state.db. Runs after every successful rename -- no background worker needed.
        try:
            with _meta_db_conn() as pmconn:
                pmconn.execute(f"ATTACH DATABASE ? AS statedb", (_STATE_DB_PATH,))
                pmconn.execute(
                    "DELETE FROM session_meta WHERE session_id NOT IN "
                    "(SELECT id FROM statedb.sessions)"
                )
                pmconn.commit()
        except Exception as evict_exc:
            logger.warning("proxy_meta.db eviction failed (non-fatal): %s", evict_exc)
        return JSONResponse({"ok": True})
    except Exception as exc:
        logger.error("DB error in api_session_rename: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.put("/api/sessions/{session_id}/archive")
async def api_session_archive(session_id: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    if not _SESSION_ID_RE.match(session_id):
        return JSONResponse({"error": "Invalid session_id"}, status_code=400)
    try:
        conversation_ids = _conversation_session_ids(session_id)
        if not conversation_ids:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        now = time.time()
        with _meta_db_conn() as conn:
            # Ensure rows exist for every session segment in the conversation.
            conn.executemany(
                "INSERT INTO session_meta (session_id, custom_name, updated_at, archived) "
                "VALUES (?, '', ?, 1) "
                "ON CONFLICT(session_id) DO UPDATE SET archived=1, updated_at=excluded.updated_at",
                [(sid, now) for sid in conversation_ids],
            )
            conn.commit()
        return JSONResponse({"ok": True, "archived": True})
    except Exception as exc:
        logger.error("DB error in api_session_archive: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.put("/api/sessions/{session_id}/unarchive")
async def api_session_unarchive(session_id: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    if not _SESSION_ID_RE.match(session_id):
        return JSONResponse({"error": "Invalid session_id"}, status_code=400)
    try:
        conversation_ids = _conversation_session_ids(session_id)
        if not conversation_ids:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        placeholders = ",".join("?" for _ in conversation_ids)
        with _meta_db_conn() as conn:
            conn.execute(
                f"UPDATE session_meta SET archived=0, updated_at=? WHERE session_id IN ({placeholders})",
                (time.time(), *conversation_ids),
            )
            conn.commit()
        return JSONResponse({"ok": True, "archived": False})
    except Exception as exc:
        logger.error("DB error in api_session_unarchive: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@app.delete("/api/sessions/{session_id}")
async def api_session_delete(session_id: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    if not _SESSION_ID_RE.match(session_id):
        return JSONResponse({"error": "Invalid session_id"}, status_code=400)
    try:
        conversation_ids = _conversation_session_ids(session_id)
        if not conversation_ids:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        placeholders = ",".join("?" for _ in conversation_ids)

        # Delete from state.db (sessions + messages)
        with sqlite3.connect(_STATE_DB_PATH, timeout=5) as conn:
            conn.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders})", conversation_ids)
            conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", conversation_ids)
            conn.commit()

        # Delete proxy-owned metadata and uploads for this conversation. Upload files
        # are path-checked so only files under _UPLOADS_DIR can be removed.
        try:
            uploads_root = _UPLOADS_DIR.resolve()
            with _meta_db_conn() as pmconn:
                upload_rows = pmconn.execute(
                    f"SELECT filename FROM uploads WHERE session_id IN ({placeholders})", conversation_ids
                ).fetchall()
                for row in upload_rows:
                    fp = (uploads_root / row["filename"]).resolve()
                    if fp.is_file() and uploads_root in fp.parents:
                        try:
                            fp.unlink()
                        except OSError as unlink_exc:
                            logger.warning("Upload delete failed (non-fatal): %s", unlink_exc)
                pmconn.execute(f"DELETE FROM uploads WHERE session_id IN ({placeholders})", conversation_ids)
                pmconn.execute(f"DELETE FROM session_meta WHERE session_id IN ({placeholders})", conversation_ids)
                pmconn.commit()
        except Exception as meta_exc:
            logger.warning("proxy_meta.db cleanup failed (non-fatal): %s", meta_exc)

        return JSONResponse({"ok": True})
    except Exception as exc:
        logger.error("DB error in api_session_delete: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


# ---------------------------------------------------------------------------
# Static files + root
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)

"""Secure plugin loader — path-traversal and symlink protection."""
_MAX_PLUGIN_SIZE = 10 * 1024 * 1024  # 10 MiB


def _load_plugins(plugin_dir: Path) -> tuple[list[str], list[str]]:
    """Resolve HERMES_PROXY_PLUGIN_* env vars into safe server-side paths.
    Returns (scripts, errors)."""
    safe_root = plugin_dir.resolve()
    # Wipe stale plugin copies (files matching the numbered-destination pattern
    # {index}_{name}.js that this loader creates) before loading new ones.
    _stale_plugin_re = re.compile(r'^\d+_.*\.js$')
    for existing in plugin_dir.iterdir():
        if existing.is_file() and _stale_plugin_re.match(existing.name):
            try:
                existing.unlink()
            except OSError:
                pass  # best-effort
    scripts: list[str] = []
    errors: list[str] = []
    for i in range(10):
        val = os.environ.get(f"HERMES_PROXY_PLUGIN_{i}", "").strip()
        if not val:
            continue
        if val.startswith("local:"):
            raw = val[6:]
            # Block path traversal attempts
            if ".." in raw or "\x00" in raw:
                errors.append(f"Plugin {i} path traversal blocked: {raw!r}")
                continue
            fp = Path(raw).expanduser()
            if not fp.is_file():
                errors.append(f"Plugin {i} local path not found: {raw}")
                continue
            try:
                real_fp = fp.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                errors.append(f"Plugin {i} symlink resolution failed ({exc}): {raw}")
                continue
            # If the original path is a symlink, ensure the resolved target
            # does not escape the symlink's parent directory tree. This blocks
            # symlink attacks where a link inside plugins/ points at /etc/passwd.
            if fp.is_symlink():
                link_parent = fp.parent.resolve()
                if not str(real_fp).startswith(str(link_parent)):
                    errors.append(f"Plugin {i} symlink escapes parent dir: {raw}")
                    continue
            # Size gate (TOCTOU — copy is next step, size may change, we accept best-effort)
            try:
                size = real_fp.stat().st_size
            except OSError as exc:
                errors.append(f"Plugin {i} stat failed ({exc}): {raw}")
                continue
            if size > _MAX_PLUGIN_SIZE:
                errors.append(f"Plugin {i} file too large ({size:,} bytes, max {_MAX_PLUGIN_SIZE:,}): {raw}")
                continue
            dest = plugin_dir / f"{i}_{real_fp.name}"
            try:
                dest.write_bytes(real_fp.read_bytes())
                scripts.append(f"/static/__plugins__/{dest.name}")
            except OSError as exc:
                errors.append(f"Plugin {i} copy failed: {exc}")
        elif val.startswith("http://") or val.startswith("https://"):
            scripts.append(val)
        else:
            errors.append(f"Plugin {i} invalid URL (must start with http://, https://, or local:): {val}")
    return scripts, errors


# ---------------------------------------------------------------------------
# Uploads directory for file attachments
# ---------------------------------------------------------------------------
_PLUGIN_DIR = _STATIC_DIR / "__plugins__"
_PLUGIN_DIR.mkdir(exist_ok=True)
_UPLOADS_DIR = Path(__file__).parent / "uploads"
_UPLOADS_DIR.mkdir(exist_ok=True)
_UPLOAD_MAX_SIZE = 50 * 1024 * 1024  # 50 MB
_UPLOAD_TTL_DAYS = int(os.environ.get("HERMES_PROXY_UPLOAD_TTL_DAYS", "30"))


def _evict_stale_uploads() -> None:
    """Delete upload files and DB rows older than _UPLOAD_TTL_DAYS.

    Called lazily on each upload — no background worker needed.
    Path traversal safety: only deletes files whose resolved path is
    inside _UPLOADS_DIR.
    """
    cutoff = time.time() - (_UPLOAD_TTL_DAYS * 86400)
    uploads_root = _UPLOADS_DIR.resolve()
    try:
        with _meta_db_conn() as conn:
            cur = conn.execute(
                "SELECT filename FROM uploads WHERE uploaded_at < ?", (cutoff,)
            )
            stale_files = [row["filename"] for row in cur.fetchall()]
            for fname in stale_files:
                fp = (uploads_root / fname).resolve()
                # Safety: only delete files within the uploads directory
                if fp.is_file() and uploads_root in fp.parents:
                    try:
                        fp.unlink()
                    except OSError:
                        pass  # best-effort
                # Always clean up the DB row even if file was already gone
            conn.execute("DELETE FROM uploads WHERE uploaded_at < ?", (cutoff,))
            conn.commit()
    except Exception as exc:
        logger.warning("Upload eviction failed (non-fatal): %s", exc)


@app.post("/api/attachments")
async def api_attachments(request: Request, file: UploadFile = File(...)):
    """Accept a single authenticated file upload and return markdown URL."""
    if not _is_authenticated(request):
        return _auth_error()
    if not file.content_type:
        return JSONResponse({"error": "Missing Content-Type"}, status_code=400)
    ct = file.content_type.lower().split(";", 1)[0].strip()
    raw = await file.read()
    if len(raw) > _UPLOAD_MAX_SIZE:
        return JSONResponse({"error": f"File too large (max {_UPLOAD_MAX_SIZE // 1024 // 1024} MB)"}, status_code=400)
    # Sanitize filename
    orig = file.filename or "upload.bin"
    name, dot, ext = orig.rpartition(".")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (name or "file"))
    safe_ext = "".join(c for c in ext.lower() if c.isalnum())
    final_name = f"{safe_name}_{int(time.time())}{dot}{safe_ext}" if safe_ext else f"{safe_name}_{int(time.time())}"
    dest = _UPLOADS_DIR / final_name
    try:
        dest.write_bytes(raw)
    except Exception as exc:
        logger.error("Upload write failed: %s", exc)
        return JSONResponse({"error": "Upload failed"}, status_code=500)
    # Record upload metadata in proxy_meta.db
    token = _get_token(request)
    session_id = browser_sessions.get(token or "")
    try:
        with _meta_db_conn() as conn:
            if _table_has_column(conn, "uploads", "token"):
                conn.execute(
                    "INSERT INTO uploads (filename, size, mime_type, uploaded_at, session_id, token) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (final_name, len(raw), ct, time.time(), session_id, token),
                )
            else:
                conn.execute(
                    "INSERT INTO uploads (filename, size, mime_type, uploaded_at, session_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (final_name, len(raw), ct, time.time(), session_id),
                )
            conn.commit()
    except Exception as exc:
        logger.warning("Upload metadata record failed (non-fatal): %s", exc)
    # Lazy eviction of stale uploads
    _evict_stale_uploads()
    url = f"/uploads/{final_name}"
    absolute_url = str(request.base_url).rstrip("/") + url
    if ct.startswith("image/"):
        md = f"![{final_name}]({absolute_url})"
    else:
        md = f"[{final_name}]({absolute_url})"
    return JSONResponse({
        "url": url,
        "absolute_url": absolute_url,
        "local_path": str(dest.resolve()),
        "filename": final_name,
        "mime_type": ct,
        "size": len(raw),
        "markdown": md,
    })


@app.get("/api/uploads/stats")
async def api_uploads_stats(request: Request) -> Response:
    """Return upload statistics: total files, total bytes, oldest timestamp."""
    if not _is_authenticated(request):
        return _auth_error()
    try:
        with _meta_db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as total_files, COALESCE(SUM(size), 0) as total_bytes, "
                "COALESCE(MIN(uploaded_at), 0) as oldest FROM uploads"
            ).fetchone()
        # Also count orphan files in uploads/ dir not tracked in DB
        uploads_root = _UPLOADS_DIR.resolve()
        on_disk = sum(1 for f in _UPLOADS_DIR.iterdir() if f.is_file())
        return JSONResponse({
            "total_files": row["total_files"],
            "total_bytes": row["total_bytes"],
            "oldest_timestamp": row["oldest"],
            "files_on_disk": on_disk,
            "ttl_days": _UPLOAD_TTL_DAYS,
        })
    except Exception as exc:
        logger.error("Upload stats error: %s", exc)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


def _extract_favicon(html: str, base_url: str) -> str:
    """Extract favicon URL from HTML <link rel=icon> tags."""
    favicon_patterns = [
        re.compile(r'<link[^>]+rel=["\'](?:shortcut\s+)?icon["\'][^>]+href=["\']([^"\']+)["\']', re.I),
        re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'](?:shortcut\s+)?icon["\']', re.I),
    ]
    for pattern in favicon_patterns:
        m = pattern.search(html)
        if m:
            href = m.group(1).strip()
            if href.startswith("http://") or href.startswith("https://") or href.startswith("//"):
                if href.startswith("//"):
                    parsed = urlparse(base_url)
                    return f"{parsed.scheme}:{href}"
                return href
            return urljoin(base_url, href)
    return ""


def _domain_favicon(url: str) -> str:
    """Return domain root /favicon.ico URL."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def _extract_title(html: str) -> str:
    """Extract <title> tag content as fallback."""
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    if m:
        return m.group(1).strip()
    return ""


@app.get("/api/og")
async def api_og(url: str, request: Request) -> Response:
    """Fetch a URL and return Open Graph + favicon metadata."""
    if not _is_authenticated(request):
        return _auth_error()
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return JSONResponse({"error": "URL must start with http:// or https://"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except httpx.TimeoutException:
        logger.warning("OG fetch timed out (5s): %s", url)
        return JSONResponse({"title": "", "description": "", "image": "", "favicon": "", "url": url})
    except httpx.RequestError as exc:
        logger.warning("OG fetch failed (network): %s — %s", url, exc)
        return JSONResponse({"title": "", "description": "", "image": "", "favicon": "", "url": url})
    except Exception as exc:
        logger.warning("OG fetch failed: %s", exc)
        return JSONResponse({"title": "", "description": "", "image": "", "favicon": "", "url": url})

    def _meta_tag(name, html_text):
        for attr in [f'property="og:{name}"', f"property='og:{name}'", f'name="{name}"', f"name='{name}'"]:
            pattern = re.compile(r'<meta[^>]+' + re.escape(attr) + r'[^>]+content=["\']([^"\']+)["\']', re.I)
            m = pattern.search(html_text)
            if m:
                return m.group(1)
            pattern2 = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+' + re.escape(attr), re.I)
            m2 = pattern2.search(html_text)
            if m2:
                return m2.group(1)
        return ""

    title = _meta_tag("title", html)
    if not title:
        title = _extract_title(html)

    description = _meta_tag("description", html)
    image = _meta_tag("image", html)

    favicon = _extract_favicon(html, url)
    if not favicon:
        favicon = _domain_favicon(url)

    return JSONResponse({
        "title": title,
        "description": description,
        "image": image,
        "favicon": favicon,
        "url": url,
    })


# ---------------------------------------------------------------------------
# Static files + root
# ---------------------------------------------------------------------------
_plugin_scripts, _plugin_errors = _load_plugins(_PLUGIN_DIR)
for _err in _plugin_errors:
    logger.warning(_err)


def _inject_plugins(html: str) -> str:
    """Inject <script type=\"module\"> tags for each plugin before </body>."""
    if not _plugin_scripts:
        return html
    _tags = "\n".join(f'<script type="module" src="{src}"></script>' for src in _plugin_scripts)
    body_close = html.rfind("</body>")
    if body_close != -1:
        return html[:body_close] + _tags + "\n" + html[body_close:]
    return html + "\n" + _tags


@app.get("/")
async def root(request: Request) -> Response:
    index_file = _STATIC_DIR / "index.html"
    if not index_file.exists():
        return JSONResponse({"error": "index.html not found"}, status_code=404)
    raw_html = index_file.read_text()
    html_with_plugins = _inject_plugins(raw_html)
    return Response(
        content=html_with_plugins.encode(),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/run-python")
async def api_run_python(request: Request) -> Response:
    """Execute submitted Python code safely in a local venv.
    Uses uv run to isolate and time-limit execution."""
    if not _is_authenticated(request):
        return _auth_error()
    try:
        body = await request.json()
    except _json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    code = body.get("code", "")
    if not isinstance(code, str):
        return JSONResponse({"error": "code must be a string"}, status_code=400)
    if not code.strip():
        return JSONResponse({"output": "", "error": "Empty code"}, status_code=200)

    # Safety: strip control characters / null bytes
    code = code.replace("\x00", "")

    import subprocess as sp
    import tempfile
    # Write code to a temp file and execute via uv
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            f.flush()
            tmp_path = f.name
        # Build the uv run command
        uv_bin = os.environ.get("UV_BIN", "uv")
        cmd = [
            uv_bin, "run", "--python", 
            os.environ.get("HERMES_PROXY_RUN_PYTHON", sys.executable),
            tmp_path,
        ]
        result = sp.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(Path.home()),
        )
        # Only return stdout + stderr (truncated if huge)
        max_len = 5000
        output = (result.stdout + "\n" + result.stderr).strip()
        if len(output) > max_len:
            output = output[:max_len] + f"\n... [{len(output) - max_len} chars truncated]"
        return JSONResponse({
            "output": output,
            "error": None,
            "returncode": result.returncode,
        }, status_code=200)
    except sp.TimeoutExpired:
        return JSONResponse({"output": "", "error": "Execution timed out (30s limit)"}, status_code=200)
    except Exception as exc:
        logger.error("Error running python: %s", exc)
        return JSONResponse({"output": "", "error": str(exc)}, status_code=200)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(_UPLOADS_DIR)), name="uploads")
