import hmac
import json as _json
import logging
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_meta (
                session_id TEXT PRIMARY KEY,
                custom_name TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()

_init_proxy_meta_db()

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

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI()

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class _MaxBodyMiddleware(BaseHTTPMiddleware):
    """Reject POST bodies over 1 MB to prevent memory exhaustion."""
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > 1_048_576:
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
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none';"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_MaxBodyMiddleware)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
# Auth routes
# ---------------------------------------------------------------------------

@app.post("/auth/login")
async def auth_login(request: Request) -> Response:
    ip = request.client.host if request.client else "unknown"
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
                    if token not in _session_created:
                        _session_created[token] = time.time()
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
        with sqlite3.connect(_STATE_DB_PATH, timeout=5) as conn:
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
        # Overlay custom names from proxy_meta.db
        try:
            with sqlite3.connect(_PROXY_META_DB_PATH, timeout=5) as pmconn:
                pmconn.row_factory = sqlite3.Row
                pmcur = pmconn.cursor()
                pmcur.execute("SELECT session_id, custom_name FROM session_meta")
                custom_names = {r["session_id"]: r["custom_name"] for r in pmcur.fetchall()}
            for r in rows:
                if r["id"] in custom_names:
                    r["title"] = custom_names[r["id"]]
        except Exception as exc:
            logger.warning("proxy_meta.db read failed (non-fatal): %s", exc)
        return JSONResponse(rows)
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
        # Overlay custom names
        try:
            with sqlite3.connect(_PROXY_META_DB_PATH, timeout=5) as pmconn:
                pmconn.row_factory = sqlite3.Row
                pmcur = pmconn.cursor()
                pmcur.execute("SELECT session_id, custom_name FROM session_meta")
                custom_names = {r2["session_id"]: r2["custom_name"] for r2 in pmcur.fetchall()}
            for r in rows:
                if r["id"] in custom_names:
                    r["title"] = custom_names[r["id"]]
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
        with sqlite3.connect(_PROXY_META_DB_PATH, timeout=5) as conn:
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
            with sqlite3.connect(_PROXY_META_DB_PATH, timeout=5) as pmconn:
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
_UPLOAD_MAX_SIZE = 5 * 1024 * 1024  # 5 MB
_UPLOAD_WHITELIST = {"image/jpeg", "image/png", "image/gif", "image/webp"}


@app.post("/api/attachments")
async def api_attachments(file: UploadFile = File(...)):
    """Accept a single image upload and return markdown URL."""
    if not file.content_type:
        return JSONResponse({"error": "Missing Content-Type"}, status_code=400)
    ct = file.content_type.lower()
    if ct not in _UPLOAD_WHITELIST:
        return JSONResponse({"error": f"File type not allowed: {ct}"}, status_code=400)
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
    url = f"/uploads/{final_name}"
    md = f"![{final_name}]({url})"
    return JSONResponse({"url": url, "markdown": md})


@app.get("/api/og")
async def api_og(url: str):
    """Fetch a URL and return Open Graph metadata."""
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return JSONResponse({"error": "URL must start with http:// or https://"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        logger.warning("OG fetch failed: %s", exc)
        return JSONResponse({"title": "", "description": "", "image": "", "url": url})

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

    return JSONResponse({
        "title": _meta_tag("title", html),
        "description": _meta_tag("description", html),
        "image": _meta_tag("image", html),
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


app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(_UPLOADS_DIR)), name="uploads")
