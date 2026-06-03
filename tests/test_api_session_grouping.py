import os
import sqlite3
import sys

sys.path.insert(0, '/home/hermes/apps/hermes-proxy')
os.chdir('/home/hermes/apps/hermes-proxy')
os.environ.setdefault('HERMES_PROXY_SIGNING_KEY', '9d447d6c2c7a73365f2bd9ab2328ff689d5cf65f1c9773624db21765831b3f85')
os.environ.setdefault('HERMES_PROXY_PASSWORD', 'testpass123')
os.environ.setdefault('API_SERVER_KEY', 'testkey123')

from fastapi.testclient import TestClient
import server
import core


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
    rows = [
        ("root-a", "api_server", None, 100.0, 200.0, "compression", None, 4, "gpt"),
        ("child-old", "api_server", "root-a", 201.0, None, None, None, 5, "gpt"),
        ("child-new", "api_server", "root-a", 250.0, None, None, None, 6, "gpt"),
        ("root-b", "api_server", None, 300.0, None, None, None, 2, "gpt"),
        ("cli-ignored", "cli", None, 400.0, None, None, None, 2, "gpt"),
    ]
    conn.executemany(
        "INSERT INTO sessions (id, source, parent_session_id, started_at, ended_at, end_reason, title, message_count, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    messages = [
        ("root-a", "user", "original duplicated prompt", 101.0),
        ("child-old", "user", "original duplicated prompt", 202.0),
        ("child-new", "user", "original duplicated prompt", 251.0),
        ("root-b", "user", "separate conversation", 301.0),
        ("cli-ignored", "user", "not api", 401.0),
    ]
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        messages,
    )
    conn.commit()
    conn.close()


def test_api_sessions_groups_compression_children_by_root_latest_leaf(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)
    meta_conn = sqlite3.connect(meta_db)
    meta_conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0)"
    )
    meta_conn.execute(
        "INSERT INTO session_meta (session_id, custom_name, updated_at) VALUES (?, ?, ?)",
        ("root-a", "Renamed root conversation", 0),
    )
    meta_conn.commit()
    meta_conn.close()

    monkeypatch.setattr(core, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(core, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    login = client.post("/auth/login", json={"password": "testpass123"})
    assert login.status_code == 200

    response = client.get("/api/sessions")
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()]

    assert ids == ["root-b", "child-new"]
    rows = {row["id"]: row for row in response.json()}
    assert rows["child-new"]["root_session_id"] == "root-a"
    assert rows["child-new"]["title"] == "Renamed root conversation"
    assert "root-a" not in ids
    assert "child-old" not in ids
    assert "cli-ignored" not in ids

    # Pagination headers are present
    assert response.headers["X-Total-Count"] == "2"  # 2 visible grouped conversations
    assert response.headers["X-Offset"] == "0"
    assert response.headers["X-Limit"] == "30"


def test_api_sessions_paginates_after_grouping_visible_conversations(tmp_path, monkeypatch):
    state_db = tmp_path / "state.db"
    meta_db = tmp_path / "proxy_meta.db"
    _make_state_db(state_db)
    meta_conn = sqlite3.connect(meta_db)
    meta_conn.execute(
        "CREATE TABLE session_meta (session_id TEXT PRIMARY KEY, custom_name TEXT NOT NULL, "
        "updated_at REAL NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0)"
    )
    meta_conn.commit()
    meta_conn.close()

    monkeypatch.setattr(core, "_STATE_DB_PATH", str(state_db))
    monkeypatch.setattr(core, "_PROXY_META_DB_PATH", str(meta_db))

    client = TestClient(server.app)
    login = client.post("/auth/login", json={"password": "testpass123"})
    assert login.status_code == 200

    page1 = client.get("/api/sessions?offset=0&limit=1")
    page2 = client.get("/api/sessions?offset=1&limit=1")
    page3 = client.get("/api/sessions?offset=2&limit=1")

    assert page1.status_code == 200
    assert page2.status_code == 200
    assert page3.status_code == 200
    assert page1.headers["X-Total-Count"] == "2"
    assert [row["id"] for row in page1.json()] == ["root-b"]
    assert [row["id"] for row in page2.json()] == ["child-new"]
    assert page3.json() == []
