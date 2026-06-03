# server.py Light Decomposition Plan

## Goal
Cut `server.py` from 1411 lines to ~400 lines by extracting route handlers into a `routes/` package. Keep config, DB helpers, auth, middleware, and app factory in `server.py` for now.

## Files

### New: `routes/__init__.py`
Import and assemble all routers into a list for server.py to mount.

```python
from fastapi import APIRouter
from . import health, auth, chat, sessions, attachments, og

routers = [
    health.router,
    auth.router,
    chat.router,
    sessions.router,
    attachments.router,
    og.router,
]
```

### New: `routes/health.py` (~30 lines)
- `APIRouter(prefix="")`
- `@router.get("/healthz")` — current health check endpoint
- Move `_check_health()` helper logic here or keep it callable from server.py

### New: `routes/auth.py` (~60 lines)
- `APIRouter(prefix="")`
- `@router.post("/auth/login")`
- `@router.post("/auth/logout")`
- `@router.get("/auth/status")`
- `@router.get("/api/session/validate")`
- Depends on: `_make_token`, `_verify_token`, `_is_authenticated`, `_auth_error`, `_set_auth_cookie`, `_clear_auth_cookie`, `browser_sessions`, `_HERMES_PROXY_PASSWORD`

### New: `routes/chat.py` (~180 lines)
- `APIRouter(prefix="")`
- `@router.post("/api/chat")` — the big streaming endpoint
- Move: `_check_chat_rate_limit`, `_evict_stale_browser_sessions`
- Depends on: `_is_authenticated`, `_auth_error`, `_assign_pending_uploads`, `_API_SERVER_URL`, `_API_SERVER_KEY`, `browser_sessions`, logger

### New: `routes/sessions.py` (~200 lines)
- `APIRouter(prefix="")`
- `@router.get("/api/sessions")` — list with pagination, date grouping
- `@router.get("/api/sessions/search")` — search
- `@router.get("/api/sessions/{session_id}/messages")`
- `@router.put("/api/sessions/{session_id}/rename")`
- `@router.put("/api/sessions/{session_id}/archive")`
- `@router.put("/api/sessions/{session_id}/unarchive")`
- `@router.delete("/api/sessions/{session_id}")`
- Move: `_conversation_session_ids`, `_meta_db_conn`, `_STATE_DB_PATH`, `_PROXY_META_DB_PATH`, `_table_has_column`
- Depends on: `_is_authenticated`, `_auth_error`, logger

### New: `routes/attachments.py` (~80 lines)
- `APIRouter(prefix="")`
- `@router.post("/api/attachments")` — file upload
- `@router.get("/api/uploads/stats")`
- Move: `_UPLOADS_DIR`, `_UPLOAD_MAX_SIZE`, `_evict_stale_uploads`
- Depends on: `_is_authenticated`, `_auth_error`, `_get_token`, `_assign_pending_uploads`, logger

### New: `routes/og.py` (~60 lines)
- `APIRouter(prefix="")`
- `@router.get("/api/og")`
- Move: `_extract_favicon`, `_domain_favicon`, `_extract_title`
- Depends on: `_is_authenticated`, `_auth_error`, logger

### Modified: `server.py` (~400 lines remaining)
- Keep: imports, logging setup, `.env` loader, config validation, DB init, app factory, middleware, auth helpers (`_make_token`, `_verify_token`, `_is_authenticated`, etc.), `_inject_plugins`, root route, static mounts
- Add: `for router in routes.routers: app.include_router(router)`

## Import strategy
Routes module imports from `server` for shared state (auth helpers, DB paths, logger). This creates a circular import risk. Mitigation:

1. Move shared helpers (`_is_authenticated`, `_auth_error`, `_make_token`, `_verify_token`, `_get_client_ip`, logger, `_request_id`, `_session_id_ctx`, DB paths) into a new `core.py` module.
2. Both `server.py` and `routes/*.py` import from `core.py`.
3. `server.py` imports `routes` and mounts routers.
4. `routes/*.py` never import `server`.

This is cleaner but adds one more file. Given "light split", we can instead:
- Keep shared helpers in `server.py`
- Have routes import from `server` at runtime (not at top-level) to avoid circulars
- Or use `from __main__ import ...` pattern (fragile)
- Better: just move the shared helpers to `core.py` anyway — it's 5 min of work and prevents pain

## Execution order
1. Create `core.py` with shared helpers
2. Update `server.py` to import from `core.py`
3. Create `routes/` package with 6 route files
4. Mount routers in `server.py`
5. Verify proxy starts: `curl http://localhost:8643/healthz`
6. Run tests if available
7. Commit

## Rollback
If anything breaks, `git checkout -- server.py` restores the original. The new files (`core.py`, `routes/`) can be deleted.

## Risks
- Circular imports if routes import from server.py at module level
- FastAPI router prefix behavior vs current flat routes (no prefix needed, keep routes as-is)
- Middleware stays in server.py, unaffected

## Verification
- `curl http://localhost:8643/healthz` → OK
- `curl -X POST http://localhost:8643/auth/login -d '{"password":"..."}'` → token
- `curl http://localhost:8643/api/sessions` → list
- Upload a file via `/api/attachments`
- Browser: open UI, send a chat message
