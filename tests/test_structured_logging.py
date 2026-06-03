"""Test structured logging: JSON format, request ID propagation, access log."""
import json
import logging
import sys

sys.path.insert(0, "/home/hermes/apps/hermes-proxy")

import server
import core
from fastapi.testclient import TestClient


def test_json_formatter_outputs_valid_json():
    """_JSONFormatter produces parseable JSON with required fields."""
    fmt = core._JSONFormatter()
    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname="", lineno=0,
        msg="test message", args=(), exc_info=None,
    )
    output = fmt.format(record)
    data = json.loads(output)
    assert "ts" in data, f"Missing 'ts' in: {data}"
    assert data["level"] == "WARNING"
    assert data["msg"] == "test message"
    assert "request_id" in data
    assert "session_id" in data


def test_json_formatter_includes_extra_fields():
    """_JSONFormatter merges extra fields like method, path, status."""
    fmt = core._JSONFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="GET /api/chat 200", args=(), exc_info=None,
    )
    record.method = "GET"
    record.path = "/api/chat"
    record.status = 200
    record.duration_ms = 42.5
    record.ip = "127.0.0.1"
    output = fmt.format(record)
    data = json.loads(output)
    assert data["method"] == "GET"
    assert data["path"] == "/api/chat"
    assert data["status"] == 200
    assert data["duration_ms"] == 42.5
    assert data["ip"] == "127.0.0.1"


def test_request_id_in_response_headers():
    """Each response includes an X-Request-Id header."""
    client = TestClient(server.app)
    resp = client.get("/healthz")
    assert "X-Request-Id" in resp.headers, "Missing X-Request-Id header"
    rid = resp.headers["X-Request-Id"]
    assert len(rid) == 8, f"Expected 8-char request ID, got '{rid}'"
    # Should be hex
    int(rid, 16)


def test_text_formatter_output():
    """_TextFormatter includes request_id and session_id markers."""
    core._request_id.set("abcd1234")
    core._session_id_ctx.set("sess1")
    fmt = core._TextFormatter()
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="something broke", args=(), exc_info=None,
    )
    output = fmt.format(record)
    assert "[abcd1234|sess1]" in output
    assert "something broke" in output
    # Reset
    core._request_id.set("-")
    core._session_id_ctx.set("-")


if __name__ == "__main__":
    for f in [
        test_json_formatter_outputs_valid_json,
        test_json_formatter_includes_extra_fields,
        test_request_id_in_response_headers,
        test_text_formatter_output,
    ]:
        f()
        print(f"✓ {f.__name__}")
    print("\nAll tests passed.")