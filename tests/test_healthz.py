"""Test the /healthz health check endpoint."""
import sys

sys.path.insert(0, "/home/hermes/apps/hermes-proxy")

import server
import core
from fastapi.testclient import TestClient
import sqlite3
import os
import tempfile


def test_healthz_returns_ok_when_healthy():
    """When all deps are available, /healthz returns 200 with status=ok."""
    client = TestClient(server.app)
    resp = client.get("/healthz")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["status"] in ("ok", "degraded", "unhealthy"), f"Unexpected status: {data}"
    assert "checks" in data
    # state_db and meta_db should be present
    assert "state_db" in data["checks"]
    assert "meta_db" in data["checks"]
    assert "api_server" in data["checks"]
    # state_db should be True (we have a real state.db)
    assert data["checks"]["state_db"] is True, f"state_db should be healthy: {data}"
    # meta_db should be True
    assert data["checks"]["meta_db"] is True, f"meta_db should be healthy: {data}"


def test_healthz_returns_unhealthy_when_state_db_missing():
    """When state.db is unreachable, /healthz returns 503 with status=unhealthy."""
    original_db = core._STATE_DB_PATH
    # Point to a nonexistent path
    core._STATE_DB_PATH = "/tmp/__healthz_test_nonexistent__/state.db"
    try:
        client = TestClient(server.app)
        resp = client.get("/healthz")
        assert resp.status_code == 503, f"Expected 503, got {resp.status_code}"
        data = resp.json()
        assert data["status"] == "unhealthy", f"Expected unhealthy, got {data['status']}"
        assert data["checks"]["state_db"] is False
    finally:
        server._STATE_DB_PATH = original_db


def test_healthz_unauthenticated():
    """The /healthz endpoint must NOT require authentication."""
    client = TestClient(server.app)
    resp = client.get("/healthz")
    # Must NOT return 401
    assert resp.status_code != 401, f"/healthz should be unauthenticated, got {resp.status_code}"


if __name__ == "__main__":
    for f in [test_healthz_returns_ok_when_healthy,
              test_healthz_returns_unhealthy_when_state_db_missing,
              test_healthz_unauthenticated]:
        f()
        print(f"✓ {f.__name__}")
    print("\nAll tests passed.")