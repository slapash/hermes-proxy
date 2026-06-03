"""Upload/attachment routes."""
import time
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import JSONResponse

from core import (
    logger,
    _is_authenticated,
    _auth_error,
    _get_token,
    _meta_db_conn,
    _table_has_column,
    browser_sessions,
    _UPLOADS_DIR,
    _UPLOAD_MAX_SIZE,
    _UPLOAD_TTL_DAYS,
    _evict_stale_uploads,
)

router = APIRouter()


@router.post("/api/attachments")
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


@router.get("/api/uploads/stats")
async def api_uploads_stats(request: Request):
    """Return upload statistics: total files, total bytes, oldest timestamp."""
    if not _is_authenticated(request):
        return _auth_error()
    try:
        with _meta_db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as total_files, COALESCE(SUM(size), 0) as total_bytes, "
                "COALESCE(MIN(uploaded_at), 0) as oldest FROM uploads"
            ).fetchone()
        uploads_root = Path(_UPLOADS_DIR).resolve()
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
