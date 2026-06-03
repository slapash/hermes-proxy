"""Session CRUD + search routes."""
import sqlite3
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from core import (
    logger,
    _is_authenticated,
    _auth_error,
    _SESSION_ID_RE,
    _meta_db_conn,
    _conversation_session_ids,
)
import core

router = APIRouter()


@router.get("/api/sessions")
async def api_sessions(request: Request, offset: int = 0, limit: int = 30) -> Response:
    if not _is_authenticated(request):
        return _auth_error()

    offset = max(0, offset)
    limit = max(1, min(limit, 100))

    try:
        with sqlite3.connect(core._STATE_DB_PATH, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
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
                if first_msg.startswith("### Task:"):
                    continue
                if not r.get("title") and first_msg:
                    r["title"] = first_msg[:72].strip()
                rows.append(r)

        # Collapse compression child sessions
        roots: dict[str, dict] = {}
        for r in rows:
            root_id = r.get("parent_session_id") or r["id"]
            r["root_session_id"] = root_id
            current = roots.get(root_id)
            if current is None or (r.get("started_at") or 0) > (current.get("started_at") or 0):
                roots[root_id] = r
        rows = sorted(roots.values(), key=lambda r: r.get("started_at") or 0, reverse=True)

        # Overlay custom names and filter archived
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


@router.get("/api/sessions/search")
async def api_sessions_search(q: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()
    q = (q or "").strip()
    if not q:
        return JSONResponse({"error": "q is required"}, status_code=400)
    try:
        with sqlite3.connect(core._STATE_DB_PATH, timeout=5) as conn:
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

        # Overlay custom names and filter archived
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


@router.get("/api/sessions/{session_id}/messages")
async def api_session_messages(session_id: str, request: Request) -> Response:
    if not _is_authenticated(request):
        return _auth_error()

    try:
        with sqlite3.connect(core._STATE_DB_PATH, timeout=5) as conn:
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


@router.put("/api/sessions/{session_id}/rename")
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
        # Lazy eviction of orphaned session_meta rows
        try:
            with _meta_db_conn() as pmconn:
                pmconn.execute(f"ATTACH DATABASE ? AS statedb", (core._STATE_DB_PATH,))
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


@router.put("/api/sessions/{session_id}/archive")
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


@router.put("/api/sessions/{session_id}/unarchive")
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


@router.delete("/api/sessions/{session_id}")
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

        with sqlite3.connect(core._STATE_DB_PATH, timeout=5) as conn:
            conn.execute(f"DELETE FROM messages WHERE session_id IN ({placeholders})", conversation_ids)
            conn.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", conversation_ids)
            conn.commit()

        try:
            uploads_root = __import__("pathlib").Path(core._UPLOADS_DIR).resolve()
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
