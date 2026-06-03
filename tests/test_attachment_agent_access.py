import json
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import server
import core

def _login(client):
    resp = client.post("/auth/login", json={"password": os.environ.get("HERMES_PROXY_PASSWORD", "testpass123")})
    assert resp.status_code == 200


class _FakeStreamResponse:
    status_code = 200
    headers = {
        "content-type": "text/event-stream",
        "x-hermes-session-id": "api-feedface12345678",
    }

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
    monkeypatch.setattr(core.httpx, "AsyncClient", _FakeAsyncClient)
    local_file = core._UPLOADS_DIR / "cat.png"
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


def test_upload_allows_general_files_and_returns_link_markdown(client, tmp_path):
    _login(client)
    payload = b"hello,file\n1,2\n"
    resp = client.post(
        "/api/attachments",
        files={"file": ("notes.csv", payload, "text/csv; charset=utf-8")},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"].startswith("notes_")
    assert data["filename"].endswith(".csv")
    assert data["mime_type"] == "text/csv"
    assert data["size"] == len(payload)
    assert data["markdown"].startswith("[notes_")
    assert not data["markdown"].startswith("![")
    assert Path(data["local_path"]).exists()


def test_upload_limit_is_50mb():
    assert core._UPLOAD_MAX_SIZE == 50 * 1024 * 1024


def test_first_chat_reassigns_pending_uploads_to_new_session(monkeypatch, tmp_path):
    meta_db = tmp_path / "proxy_meta.db"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    conn = sqlite3.connect(meta_db)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE uploads (filename TEXT PRIMARY KEY, size INTEGER NOT NULL, "
        "mime_type TEXT NOT NULL, uploaded_at REAL NOT NULL, session_id TEXT, token TEXT)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(core, "_PROXY_META_DB_PATH", str(meta_db))
    monkeypatch.setattr(core, "_UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(core.httpx, "AsyncClient", _FakeAsyncClient)

    client = TestClient(server.app)
    _login(client)
    upload = client.post(
        "/api/attachments",
        files={"file": ("cat.png", b"\x89PNG\r\n\x1a\n" + b"fake image", "image/png")},
    )
    assert upload.status_code == 200
    attachment = upload.json()

    resp = client.post(
        "/api/chat",
        json={
            "message": "describe it",
            "attachments": [{
                "url": attachment["url"],
                "markdown": attachment["markdown"],
                "filename": attachment["filename"],
                "mime_type": attachment["mime_type"],
            }],
        },
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(meta_db)
    row = conn.execute(
        "SELECT session_id FROM uploads WHERE filename = ?",
        (attachment["filename"],),
    ).fetchone()
    conn.close()
    assert row[0] == "api-feedface12345678"
