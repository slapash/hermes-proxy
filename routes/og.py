"""OpenGraph / favicon extraction route."""
import re
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import logger, _is_authenticated, _auth_error

router = APIRouter()


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


@router.get("/api/og")
async def api_og(url: str, request: Request):
    if not _is_authenticated(request):
        return _auth_error()
    target = (url or "").strip()
    if not target:
        return JSONResponse({"error": "url is required"}, status_code=400)
    if not (target.startswith("http://") or target.startswith("https://")):
        return JSONResponse({"error": "url must be http:// or https://"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(target, headers={
                "User-Agent": "Mozilla/5.0 (compatible; HermesProxy/1.0)"
            })
            html = resp.text
            title = _extract_title(html)
            favicon = _extract_favicon(html, str(resp.url)) or _domain_favicon(str(resp.url))
            return JSONResponse({
                "title": title,
                "favicon": favicon,
                "url": str(resp.url),
            })
    except httpx.TimeoutException:
        return JSONResponse({"error": "Timeout fetching URL"}, status_code=504)
    except Exception as exc:
        logger.warning("OG fetch failed for %s: %s", target, exc)
        return JSONResponse({"error": "Failed to fetch URL"}, status_code=502)
