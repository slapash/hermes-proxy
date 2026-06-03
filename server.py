"""hermes-proxy — FastAPI server for Hermes Agent.

Routes are split into the `routes/` package; shared helpers live in `core.py`.
This file only sets up the app, middleware, and mounts static files.
"""
import logging
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from core import (
    logger,
    _request_id,
    _session_id_ctx,
    _get_client_ip,
    _is_authenticated,
    _auth_error,
    _get_token,
    _inject_plugins,
    browser_sessions,
    _STATIC_DIR,
    _UPLOADS_DIR,
)

# Import routes after core to avoid circular issues
import routes

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
                from core import _UPLOAD_MAX_SIZE
                default_limit = 1_048_576
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
        response.headers["Permissions-Policy"] = "microphone=(self), on-device-speech-recognition=(self)"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


app.add_middleware(_SecurityHeadersMiddleware)
app.add_middleware(_MaxBodyMiddleware)


class _RequestLogMiddleware(BaseHTTPMiddleware):
    """Assign a per-request ID and log method/path/status/duration."""

    async def dispatch(self, request: Request, call_next):
        import secrets
        rid = secrets.token_hex(4)
        _request_id.set(rid)
        sid = request.headers.get("x-hermes-session-id", "-")
        if sid:
            _session_id_ctx.set(sid)
        request.state.request_id = rid

        start = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - start) * 1000, 1)

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
        response.headers["X-Request-Id"] = rid
        return response


app.add_middleware(_RequestLogMiddleware)

# ---------------------------------------------------------------------------
# Mount routes
# ---------------------------------------------------------------------------
for router in routes.routers:
    app.include_router(router)

# ---------------------------------------------------------------------------
# Static files + root
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(_UPLOADS_DIR)), name="uploads")


@app.get("/")
async def root(request: Request) -> Response:
    """Serve index.html with plugin scripts injected (SPA handles auth client-side)."""
    index_path = _STATIC_DIR / "index.html"
    html = index_path.read_text(encoding="utf-8") if index_path.exists() else "<h1>Hermes Chat</h1>"
    html = _inject_plugins(html)
    return Response(content=html, media_type="text/html")
