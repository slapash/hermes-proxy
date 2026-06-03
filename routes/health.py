"""Health check route — unauthenticated probe for monitoring."""
import sqlite3

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
import httpx

from core import logger, _STATE_DB_PATH, _meta_db_conn, _API_SERVER_URL

router = APIRouter()


@router.get("/healthz")
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
            checks["api_server"] = True
    except Exception as exc:
        logger.warning("healthz: api_server check failed: %s", exc)
        checks["api_server"] = False
        if overall != "unhealthy":
            overall = "degraded"

    status_code = 200 if overall != "unhealthy" else 503
    return JSONResponse({"status": overall, "checks": checks}, status_code=status_code)
