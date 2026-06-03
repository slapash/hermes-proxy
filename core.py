"""HermesProxy shared core — logging, config, auth, DB helpers, constants.

Everything that both server.py and routes/*.py need lives here to avoid
circular imports.
"""
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
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(logging.INFO)
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
_HERMES_PROXY_PASSWORD = os.environ.get("HERMES_PROXY_PASSWORD", "")
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


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
browser_sessions = {}       # type: dict  # cookie_token -> hermes_session_id
_session_created: dict = {} # token -> float (time.time()) for TTL eviction

_SESSION_TTL = 2_592_000    # 30 days — matches cookie max_age
_SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{8,80}$')

# Rate limiting: { ip: {"count": int, "window_start": float} }
_login_attempts = {}  # type: dict
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_EVICT_AFTER = _RATE_LIMIT_WINDOW * 10

# Chat rate limiting config
_CHAT_RPM = int(os.environ.get("HERMES_PROXY_CHAT_RPM", "30"))
_CHAT_BURST = int(os.environ.get("HERMES_PROXY_CHAT_BURST", "5"))
_CHAT_RATE_LIMITS = {}  # type: dict  # key -> SlidingWindowRateLimiter instance

# ---------------------------------------------------------------------------
# Static / uploads constants
# ---------------------------------------------------------------------------
_STATIC_DIR = Path(__file__).parent / "static"
_STATIC_DIR.mkdir(exist_ok=True)
_PLUGIN_DIR = _STATIC_DIR / "__plugins__"
_PLUGIN_DIR.mkdir(exist_ok=True)
_UPLOADS_DIR = Path(__file__).parent / "uploads"
_UPLOADS_DIR.mkdir(exist_ok=True)
_UPLOAD_MAX_SIZE = 50 * 1024 * 1024  # 50 MB
_UPLOAD_TTL_DAYS = int(os.environ.get("HERMES_PROXY_UPLOAD_TTL_DAYS", "30"))
_MAX_PLUGIN_SIZE = 10 * 1024 * 1024  # 10 MiB


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
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
        try:
            conn.execute("ALTER TABLE session_meta ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass
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
            pass
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
    """Return root + child session IDs for the displayed conversation."""
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
# Auth / rate-limit helpers
# ---------------------------------------------------------------------------
def _get_client_ip(request: Request) -> str:
    """Return the real client IP, respecting X-Forwarded-For."""
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
        self._tokens = float(burst)
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
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
        self._refill()
        if self._tokens >= 1.0:
            return 0.0
        return max(0.0, (60.0 / self._rpm) * (1.0 - self._tokens))


def _check_chat_rate_limit(key: str) -> tuple[bool, float]:
    """Check chat rate limit for a key (auth token or IP)."""
    now = time.monotonic()
    stale = [k for k, lim in _CHAT_RATE_LIMITS.items()
             if now - lim._last_refill > 600]
    for k in stale:
        del _CHAT_RATE_LIMITS[k]

    limiter = _CHAT_RATE_LIMITS.get(key)
    if limiter is None:
        limiter = _SlidingWindowRateLimiter(_CHAT_RPM, _CHAT_BURST)
        _CHAT_RATE_LIMITS[key] = limiter
    return limiter.allow(), limiter.retry_after


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
# Upload helpers
# ---------------------------------------------------------------------------
def _evict_stale_uploads() -> None:
    """Delete upload files and DB rows older than _UPLOAD_TTL_DAYS."""
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
                if fp.is_file() and uploads_root in fp.parents:
                    try:
                        fp.unlink()
                    except OSError:
                        pass
            conn.execute("DELETE FROM uploads WHERE uploaded_at < ?", (cutoff,))
            conn.commit()
    except Exception as exc:
        logger.warning("Upload eviction failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Plugin loader
# ---------------------------------------------------------------------------
def _load_plugins(plugin_dir: Path) -> tuple[list[str], list[str]]:
    """Resolve HERMES_PROXY_PLUGIN_* env vars into safe server-side paths."""
    safe_root = plugin_dir.resolve()
    _stale_plugin_re = re.compile(r'^\d+_.*\.js$')
    for existing in plugin_dir.iterdir():
        if existing.is_file() and _stale_plugin_re.match(existing.name):
            try:
                existing.unlink()
            except OSError:
                pass
    scripts: list[str] = []
    errors: list[str] = []
    for i in range(10):
        val = os.environ.get(f"HERMES_PROXY_PLUGIN_{i}", "").strip()
        if not val:
            continue
        if val.startswith("local:"):
            raw = val[6:]
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
            if fp.is_symlink():
                link_parent = fp.parent.resolve()
                if not str(real_fp).startswith(str(link_parent)):
                    errors.append(f"Plugin {i} symlink escapes parent dir: {raw}")
                    continue
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
# OG / favicon helpers
# ---------------------------------------------------------------------------
def _extract_favicon(html: str, base_url: str) -> str:
    """Extract favicon URL from HTML <link rel=icon> tags."""
    favicon_patterns = [
        re.compile(r'<link[^>]+rel=["\'](?:shortcut\s+)?icon["\'][^>]+href=["\']([^"\']+)["\']', re.I),
        re.compile(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'](?:shortcut\s+)?icon["\']', re.I),
    ]
    for pat in favicon_patterns:
        m = pat.search(html)
        if m:
            href = m.group(1).strip()
            if href.startswith("http://") or href.startswith("https://"):
                return href
            if href.startswith("//"):
                return "https:" + href
            return urljoin(base_url, href)
    return ""


def _domain_favicon(url: str) -> str:
    """Best-effort favicon URL from domain root."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    return ""


def _extract_title(html: str) -> str:
    """Extract <title> from HTML."""
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------------------
# Root HTML injection helper
# ---------------------------------------------------------------------------
def _inject_plugins(html: str) -> str:
    """Inject <script> tags for resolved plugins into the served HTML."""
    scripts, errors = _load_plugins(_PLUGIN_DIR)
    if errors:
        logger.warning("Plugin load errors: %s", errors)
    if not scripts:
        return html
    tags = []
    for src in scripts:
        if src.startswith("http://") or src.startswith("https://"):
            tags.append(f'<script src="{src}" crossorigin="anonymous" defer></script>')
        else:
            tags.append(f'<script src="{src}" defer></script>')
    marker = "</head>"
    if marker in html:
        html = html.replace(marker, "\n".join(tags) + "\n" + marker, 1)
    return html
