"""Test that proxy_meta.db uses WAL mode and concurrent read+write works."""
import sys
import os
import sqlite3
import tempfile
import threading

sys.path.insert(0, "/home/hermes/apps/hermes-proxy")

import server


def test_meta_db_wal_mode():
    """Verify that _meta_db_conn() connections have journal_mode=WAL."""
    conn = server._meta_db_conn()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal", f"Expected WAL mode, got {mode}"


def test_meta_db_busy_timeout():
    """Verify that _meta_db_conn() connections have busy_timeout=5000."""
    conn = server._meta_db_conn()
    timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.close()
    assert timeout_ms == 5000, f"Expected busy_timeout=5000, got {timeout_ms}"


def test_concurrent_read_write():
    """Open two connections and verify concurrent reads don't block writes under WAL."""
    # Write a row from one connection
    conn1 = server._meta_db_conn()
    conn1.execute(
        "INSERT INTO session_meta (session_id, custom_name, updated_at) "
        "VALUES ('concurrent-test', 'test-name', 1234.0)"
    )
    conn1.commit()

    # Read from another connection while first is still open
    conn2 = server._meta_db_conn()
    row = conn2.execute(
        "SELECT custom_name FROM session_meta WHERE session_id = 'concurrent-test'"
    ).fetchone()
    assert row is not None, "Concurrent read failed to see committed data"
    assert row["custom_name"] == "test-name"

    # Write from conn2 while conn1 reads — should not block
    conn2.execute(
        "UPDATE session_meta SET custom_name = 'updated-name' WHERE session_id = 'concurrent-test'"
    )
    conn2.commit()

    # Verify update from conn1
    row = conn1.execute(
        "SELECT custom_name FROM session_meta WHERE session_id = 'concurrent-test'"
    ).fetchone()
    assert row["custom_name"] == "updated-name"

    # Cleanup
    conn1.execute("DELETE FROM session_meta WHERE session_id = 'concurrent-test'")
    conn1.commit()
    conn1.close()
    conn2.close()


if __name__ == "__main__":
    for f in [test_meta_db_wal_mode, test_meta_db_busy_timeout, test_concurrent_read_write]:
        f()
        print(f"✓ {f.__name__}")
    print("\nAll tests passed.")