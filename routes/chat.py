"""Chat streaming route."""
import json as _json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from core import (
    logger,
    _is_authenticated,
    _auth_error,
    _get_token,
    _get_client_ip,
    _check_chat_rate_limit,
    _evict_stale_browser_sessions,
    browser_sessions,
    _session_created,
    _assign_pending_uploads,
    _API_SERVER_URL,
    _API_SERVER_KEY,
    _SESSION_ID_RE,
    _UPLOADS_DIR,
)

router = APIRouter()


@router.post("/api/chat")
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
            if not (url.startswith("http://") or url.startswith("https://") or url.startswith("/")):
                return JSONResponse({"error": f"attachment[{idx}] url must be http://, https://, or /"}, status_code=400)

            filename = str(att.get("filename") or Path(urlparse(url).path).name or f"attachment-{idx + 1}")
            mime_type = str(att.get("mime_type") or __import__("mimetypes").guess_type(filename)[0] or "application/octet-stream")
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
    current_session = browser_sessions.get(token) or ""
    if current_session:
        response_headers["X-Hermes-Session-Id"] = current_session

    return StreamingResponse(
        generate_with_capture(),
        media_type="text/event-stream",
        headers=response_headers,
    )
