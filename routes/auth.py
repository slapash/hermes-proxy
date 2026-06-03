"""Authentication routes."""
import hmac
import json as _json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from core import (
    logger,
    _is_authenticated,
    _auth_error,
    _get_token,
    _make_token,
    _verify_token,
    _check_rate_limit,
    _get_client_ip,
    _set_auth_cookie,
    _clear_auth_cookie,
    browser_sessions,
    _session_created,
    _HERMES_PROXY_PASSWORD,
)

router = APIRouter()


@router.post("/auth/login")
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
    proto = request.headers.get("x-forwarded-proto", "").lower()
    is_https = request.url.scheme == "https" or proto == "https"
    _set_auth_cookie(response, token, secure=is_https)
    return response


@router.post("/auth/logout")
async def auth_logout(request: Request) -> Response:
    token = _get_token(request)
    if token:
        browser_sessions.pop(token, None)
        _session_created.pop(token, None)
    response = JSONResponse({"ok": True})
    _clear_auth_cookie(response)
    return response


@router.get("/auth/status")
async def auth_status(request: Request) -> Response:
    return JSONResponse({"authenticated": _is_authenticated(request)})


@router.get("/api/session/validate")
async def session_validate(request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    token = _get_token(request)
    session_id = browser_sessions.get(token) if token else None
    return JSONResponse({"valid": session_id is not None, "session_id": session_id})
