import json as _json
import logging
import os
import sqlite3
import sys
import time
from contextvars import ContextVar
from pathlib import Path


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
