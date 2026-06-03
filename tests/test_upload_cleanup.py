"""Test upload tracking, eviction, and stats endpoint."""
import sys
import os
import time
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, "/home/hermes/apps/hermes-proxy")

import server
import core
from fastapi.testclient import TestClient


def _auth_cookie():
    token = core._make_token()
    return {"hermes-proxy-auth": token}


def test_uploads_stats_endpoint_requires_auth():
    """Unauthenticated requests to /api/uploads/stats should return 401."""
    client = TestClient(server.app)
    resp = client.get("/api/uploads/stats")
    assert resp.status_code == 401


def test_uploads_stats_returns_structure():
    """Authenticated /api/uploads/stats returns expected fields."""
    client = TestClient(server.app)
    resp = client.get("/api/uploads/stats", cookies=_auth_cookie())
    assert resp.status_code == 200
    data = resp.json()
    assert "total_files" in data
    assert "total_bytes" in data
    assert "oldest_timestamp" in data
    assert "ttl_days" in data
    assert data["ttl_days"] > 0


def test_eviction_deletes_old_files():
    """Files older than TTL are cleaned up when _evict_stale_uploads runs."""
    original_ttl = core._UPLOAD_TTL_DAYS
    original_uploads = core._UPLOADS_DIR

    # Use a temp dir so we don't pollute real uploads
    tmpdir = tempfile.mkdtemp()
    core._UPLOADS_DIR = Path(tmpdir)
    core._UPLOAD_MAX_SIZE = 1024 * 1024  # 1MB for test

    try:
        # Set a very short TTL for testing (0 days = immediate eviction)
        core._UPLOAD_TTL_DAYS = 0

        # Create a fake old upload in the DB
        old_time = time.time() - 86400  # 1 day ago
        with core._meta_db_conn() as conn:
            conn.execute(
                "INSERT INTO uploads (filename, size, mime_type, uploaded_at, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("old_test_file.png_12345.png", 100, "image/png", old_time, None),
            )
            conn.commit()

        # Create the file in the temp uploads dir
        old_file = Path(tmpdir) / "old_test_file.png_12345.png"
        old_file.write_bytes(b"\x89PNG\r\n" + b"\x00" * 94)

        assert old_file.exists(), "Setup: old file should exist"

        # Run eviction
        core._evict_stale_uploads()

        # File should be deleted
        assert not old_file.exists(), "Old file should be evicted"

        # DB row should be deleted
        with core._meta_db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM uploads WHERE filename = ?",
                ("old_test_file.png_12345.png",),
            ).fetchone()
            assert row["cnt"] == 0, "Old upload DB row should be deleted"

    finally:
        core._UPLOAD_TTL_DAYS = original_ttl
        core._UPLOADS_DIR = original_uploads
        core._UPLOAD_MAX_SIZE = 25 * 1024 * 1024
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_eviction_does_not_delete_recent_files():
    """Files within TTL are NOT deleted."""
    original_ttl = core._UPLOAD_TTL_DAYS

    try:
        core._UPLOAD_TTL_DAYS = 30  # 30 days default

        # Create a recent upload in the DB
        with core._meta_db_conn() as conn:
            conn.execute(
                "INSERT INTO uploads (filename, size, mime_type, uploaded_at, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("recent_file.png_99999.png", 50, "image/png", time.time(), None),
            )
            conn.commit()

        # Run eviction
        core._evict_stale_uploads()

        # DB row should still exist
        with core._meta_db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM uploads WHERE filename = ?",
                ("recent_file.png_99999.png",),
            ).fetchone()
            assert row["cnt"] == 1, "Recent upload should NOT be evicted"

        # Cleanup
        with core._meta_db_conn() as conn:
            conn.execute("DELETE FROM uploads WHERE filename = ?", ("recent_file.png_99999.png",))
            conn.commit()

    finally:
        core._UPLOAD_TTL_DAYS = original_ttl


if __name__ == "__main__":
    for f in [
        test_uploads_stats_endpoint_requires_auth,
        test_uploads_stats_returns_structure,
        test_eviction_deletes_old_files,
        test_eviction_does_not_delete_recent_files,
    ]:
        f()
        print(f"✓ {f.__name__}")
    print("\nAll tests passed.")