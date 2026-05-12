import os
import sqlite3
import sys
import time

sys.path.insert(0, '/home/hermes/apps/hermes-proxy')
os.chdir('/home/hermes/apps/hermes-proxy')
os.environ.setdefault('HERMES_PROXY_SIGNING_KEY', '9d447d6c2c7a73365f2bd9ab2328ff689d5cf65f1c9773624db21765831b3f85')
os.environ.setdefault('HERMES_PROXY_PASSWORD', 'testpass123')
os.environ.setdefault('API_SERVER_KEY', 'testkey123')

from fastapi.testclient import TestClient
import server


def _make_state_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            parent_session_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            title TEXT,
            message_count INTEGER DEFAULT 0,
            model TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE messages (
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, source, started_at, title, message_count, model) "
        "VALUES ('sess-alpha', 'api_server', 1000.0, 'Alpha Session', 2, 'gpt')"
    )
    conn.execute(
        "INSERT INTO sessions (id, source, started_at, title, message_count, model) "
        "VALUES ('sess-beta', 'api_server', 2000.0, 'Beta Session', 1, 'gpt')"
    )
    conn.execute(
        "INSERT INTO sessions (id, source, started_at, title, message_count, model) "
        "VALUES ('sess-gamma', 'api_server', 3000.0, 'Gamma Session', 1, 'gpt')"
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES ('sess-alpha', 'user', 'hello alpha', 1001.0)"
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES ('sess-beta', 'user', 'hello beta', 2001.0)"
    )
    conn.commit()
    conn.close()


def _make_grouped_state_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            parent_session_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            end_reason TEXT,
            title TEXT,
            message_count INTEGER DEFAULT 0,
            model TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE messages (
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL
        )
        """
    )
    rows = [
        ("api-roota", None, 100.0),
        ("api-childold", "api-roota", 200.0),
        ("api-childnew", "api-roota", 300.0),
        ("api-rootb", None, 400.0),
    ]
    for session_id, parent_id, started_at in rows:
        conn.execute(
            "INSERT INTO sessions (id, source, parent_session_id, started_at, title, message_count, model) "
            "VALUES (?, 'api_server', ?, ?, ?, 1, 'gpt')",
            (session_id, parent_id, started_at, session_id),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
            (session_id, f"hello {session_id}", started_at + 1),
        )
    conn.commit()
    conn.close()


def _make_meta_db(path, include_uploads=False):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    if include_uploads:
        conn.execute(
            "CREATE TABLE uploads (filename TEXT PRIMARY KEY, size INTEGER NOT NULL, "
            "mime_type TEXT NOT NULL, uploaded_at REAL NOT NULL, session_id TEXT)"
        )
    conn.commit()
    conn.close()


def _authed_client(client):
    login = client.post("/auth/login", json={"password": "testpass123"})
    assert login.status_code == 200
    return client


# ── Archive tests ──

def test_archive_session(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)
    # Initialize meta db
    conn = sqlite3.connect(meta_db)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    _authed_client(client)

    # Archive sess-alpha
    resp = client.put("/api/sessions/sess-alpha/archive")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "archived": True}

    # Sessions list should now exclude the archived one
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    ids = [row["id"] for row in resp.json()]
    assert "sess-alpha" not in ids
    assert "sess-beta" in ids
    assert "sess-gamma" in ids


def test_unarchive_session(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)
    conn = sqlite3.connect(meta_db)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "INSERT INTO session_meta (session_id, custom_name, updated_at, archived) "
        "VALUES ('sess-alpha', 'Alpha', 100.0, 1)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    _authed_client(client)

    # Confirm it's hidden initially
    resp = client.get("/api/sessions")
    ids = [row["id"] for row in resp.json()]
    assert "sess-alpha" not in ids

    # Unarchive
    resp = client.put("/api/sessions/sess-alpha/unarchive")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "archived": False}

    # Now it should show up
    resp = client.get("/api/sessions")
    ids = [row["id"] for row in resp.json()]
    assert "sess-alpha" in ids


def test_archive_requires_auth(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)
    conn = sqlite3.connect(meta_db)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    # No login — should get 401
    resp = client.put("/api/sessions/sess-alpha/archive")
    assert resp.status_code == 401


def test_archive_grouped_child_archives_entire_conversation(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_grouped_state_db(state_db)
    _make_meta_db(meta_db)

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    _authed_client(client)

    before = [row["id"] for row in client.get("/api/sessions").json()]
    assert before == ["api-rootb", "api-childnew"]

    resp = client.put("/api/sessions/api-childnew/archive")
    assert resp.status_code == 200

    after = [row["id"] for row in client.get("/api/sessions").json()]
    assert after == ["api-rootb"]

    meta_conn = sqlite3.connect(meta_db)
    archived = dict(meta_conn.execute(
        "SELECT session_id, archived FROM session_meta WHERE session_id IN "
        "('api-roota', 'api-childold', 'api-childnew')"
    ).fetchall())
    meta_conn.close()
    assert archived == {"api-roota": 1, "api-childold": 1, "api-childnew": 1}


# ── Delete tests ──

def test_delete_session(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    upload_file = uploads_dir / "beta.png"
    upload_file.write_bytes(b"fake image bytes")
    _make_state_db(state_db)
    conn = sqlite3.connect(meta_db)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.execute(
        "CREATE TABLE uploads (filename TEXT PRIMARY KEY, size INTEGER NOT NULL, "
        "mime_type TEXT NOT NULL, uploaded_at REAL NOT NULL, session_id TEXT)"
    )
    conn.execute(
        "INSERT INTO uploads (filename, size, mime_type, uploaded_at, session_id) "
        "VALUES ('beta.png', 16, 'image/png', 123.0, 'sess-beta')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))
    monkeypatch.setattr(server, "_UPLOADS_DIR", uploads_dir)

    client = TestClient(server.app)
    _authed_client(client)

    # Delete sess-beta
    resp = client.delete("/api/sessions/sess-beta")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify it's gone from sessions list
    resp = client.get("/api/sessions")
    ids = [row["id"] for row in resp.json()]
    assert "sess-beta" not in ids
    assert "sess-alpha" in ids

    # Verify messages are gone
    state_conn = sqlite3.connect(str(state_db))
    msgs = state_conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = 'sess-beta'"
    ).fetchone()[0]
    assert msgs == 0
    sessions = state_conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE id = 'sess-beta'"
    ).fetchone()[0]
    assert sessions == 0
    state_conn.close()

    # Verify session-owned upload file and metadata are gone too
    assert not upload_file.exists()
    meta_conn = sqlite3.connect(str(meta_db))
    uploads = meta_conn.execute(
        "SELECT COUNT(*) FROM uploads WHERE session_id = 'sess-beta'"
    ).fetchone()[0]
    assert uploads == 0
    meta_conn.close()


def test_search_excludes_archived_session(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)

    state_conn = sqlite3.connect(state_db)
    state_conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
    beta_rowid = state_conn.execute(
        "SELECT rowid FROM messages WHERE session_id = 'sess-beta' LIMIT 1"
    ).fetchone()[0]
    state_conn.execute(
        "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)",
        (beta_rowid, "hello beta"),
    )
    state_conn.commit()
    state_conn.close()

    meta_conn = sqlite3.connect(meta_db)
    meta_conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    meta_conn.execute(
        "INSERT INTO session_meta (session_id, custom_name, updated_at, archived) "
        "VALUES ('sess-beta', 'Archived Beta', 123.0, 1)"
    )
    meta_conn.commit()
    meta_conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    _authed_client(client)

    resp = client.get("/api/sessions/search?q=beta")
    assert resp.status_code == 200
    assert resp.json() == []


def test_delete_grouped_child_deletes_entire_conversation(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    upload_file = uploads_dir / "root-upload.png"
    upload_file.write_bytes(b"fake image bytes")
    _make_grouped_state_db(state_db)
    _make_meta_db(meta_db, include_uploads=True)
    meta_conn = sqlite3.connect(meta_db)
    meta_conn.execute(
        "INSERT INTO uploads (filename, size, mime_type, uploaded_at, session_id) "
        "VALUES ('root-upload.png', 16, 'image/png', 123.0, 'api-roota')"
    )
    meta_conn.commit()
    meta_conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))
    monkeypatch.setattr(server, "_UPLOADS_DIR", uploads_dir)

    client = TestClient(server.app)
    _authed_client(client)

    before = [row["id"] for row in client.get("/api/sessions").json()]
    assert before == ["api-rootb", "api-childnew"]

    resp = client.delete("/api/sessions/api-childnew")
    assert resp.status_code == 200

    after = [row["id"] for row in client.get("/api/sessions").json()]
    assert after == ["api-rootb"]

    state_conn = sqlite3.connect(state_db)
    remaining = state_conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE id IN ('api-roota', 'api-childold', 'api-childnew')"
    ).fetchone()[0]
    messages = state_conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id IN ('api-roota', 'api-childold', 'api-childnew')"
    ).fetchone()[0]
    state_conn.close()
    assert remaining == 0
    assert messages == 0
    assert not upload_file.exists()


def test_delete_nonexistent_session(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)
    conn = sqlite3.connect(meta_db)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    _authed_client(client)

    resp = client.delete("/api/sessions/does-not-exist-at-all")
    assert resp.status_code == 404


def test_delete_requires_auth(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)
    conn = sqlite3.connect(meta_db)
    conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL, archived INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(server, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    resp = client.delete("/api/sessions/sess-alpha")
    assert resp.status_code == 401