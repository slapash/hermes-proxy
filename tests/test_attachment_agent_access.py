import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

import server


def _login(client):
    resp = client.post("/auth/login", json={"password": os.environ.get("HERMES_PROXY_PASSWORD", "testpass123")})
    assert resp.status_code == 200


class _FakeStreamResponse:
    headers = {"x-hermes-session-id": "api-feedface12345678"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_bytes(self):
        yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        yield b"data: [DONE]\n\n"


class _FakeAsyncClient:
    captured_json = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, json=None, headers=None):
        _FakeAsyncClient.captured_json = json
        return _FakeStreamResponse()


def test_chat_forwards_agent_readable_attachment_context(monkeypatch):
    client = TestClient(server.app)
    _login(client)
    monkeypatch.setattr(server.httpx, "AsyncClient", _FakeAsyncClient)
    local_file = server._UPLOADS_DIR / "cat.png"
    local_file.write_bytes(b"fake image")

    resp = client.post(
        "/api/chat",
        json={
            "message": "describe it",
            "attachments": [
                    {
                        "url": "/uploads/cat.png",
                        "markdown": "![cat.png](/uploads/cat.png)",
                    }
            ],
        },
    )

    assert resp.status_code == 200
    content = _FakeAsyncClient.captured_json["messages"][0]["content"]
    assert "[Attachment 1]" in content
    assert "filename: cat.png" in content
    assert "mime_type: image/png" in content
    assert "local_path: /home/hermes/apps/hermes-proxy/uploads/cat.png" in content
    assert "url: http://testserver/uploads/cat.png" in content
    assert "Use vision_analyze on local_path or url" in content
    assert content.rstrip().endswith("describe it")


def test_upload_allows_images_above_one_mb(client, tmp_path):
    _login(client)
    payload = b"\x89PNG\r\n\x1a\n" + (b"0" * (2 * 1024 * 1024))
    resp = client.post(
        "/api/attachments",
        files={"file": ("large.png", payload, "image/png")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"].startswith("large_")
    assert data["mime_type"] == "image/png"
    assert data["size"] == len(payload)
    assert data["local_path"].startswith(str(server._UPLOADS_DIR.resolve()))
    assert data["absolute_url"].startswith("http://testserver/uploads/")
    assert Path(data["local_path"]).exists()
