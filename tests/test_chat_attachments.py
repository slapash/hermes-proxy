import os
import sys

sys.path.insert(0, '/home/hermes/apps/hermes-proxy')
os.chdir('/home/hermes/apps/hermes-proxy')
os.environ.setdefault('HERMES_PROXY_SIGNING_KEY', '9d447d6c2c7a73365f2bd9ab2328ff689d5cf65f1c9773624db21765831b3f85')
os.environ.setdefault('HERMES_PROXY_PASSWORD', 'testpass123')
os.environ.setdefault('API_SERVER_KEY', 'testkey123')

from fastapi.testclient import TestClient
import server
import core

# Disable tight rate-limiting for rapid-fire tests
core._RATE_LIMIT_MAX = 1000


def _login(client):
    resp = client.post("/auth/login", json={"password": "testpass123"})
    assert resp.status_code == 200


def test_chat_without_attachments(client=None):
    client = client or TestClient(server.app)
    _login(client)
    resp = client.post("/api/chat", json={"message": "hello"})
    # 502/503 because no upstream is running, but body must still be parsed
    assert resp.status_code in (200, 502, 503, 504)


def test_chat_with_valid_attachments(client=None):
    client = client or TestClient(server.app)
    _login(client)
    payload = {
        "message": "describe these",
        "attachments": [
            {"url": "/uploads/cat.png", "markdown": "![cat](/uploads/cat.png)"},
            {"url": "/uploads/dog.png", "markdown": "![dog](/uploads/dog.png)"},
        ],
    }
    resp = client.post("/api/chat", json=payload)
    assert resp.status_code in (200, 502, 503, 504)


def test_chat_attachments_not_array(client=None):
    client = client or TestClient(server.app)
    _login(client)
    resp = client.post("/api/chat", json={"message": "x", "attachments": "bad"})
    assert resp.status_code == 400
    assert "array" in resp.json()["error"].lower()


def test_chat_attachment_not_object(client=None):
    client = client or TestClient(server.app)
    _login(client)
    resp = client.post("/api/chat", json={"message": "x", "attachments": ["not-object"]})
    assert resp.status_code == 400
    assert "object" in resp.json()["error"].lower()


def test_chat_attachment_missing_fields(client=None):
    client = client or TestClient(server.app)
    _login(client)
    resp = client.post("/api/chat", json={"message": "x", "attachments": [{"url": "/u.png"}]})
    assert resp.status_code == 400
    assert "markdown must be a string" in resp.json()["error"].lower()

    resp2 = client.post("/api/chat", json={"message": "x", "attachments": [{"markdown": "![a](/a.jpg)"}]})
    assert resp2.status_code == 400
    assert "missing or invalid url" in resp2.json()["error"].lower()


def test_chat_message_assembled_with_attachments():
    # We can't intercept the upstream call easily without patching httpx,
    # so we just verify validation passes for a correct payload.
    client = TestClient(server.app)
    _login(client)
    resp = client.post(
        "/api/chat",
        json={
            "message": "bottom text",
            "attachments": [
                {"url": "/a.jpg", "markdown": "![a](/a.jpg)"},
            ],
        },
    )
    # Should not 400 — the only failure mode here is upstream unreachable.
    assert resp.status_code != 400
