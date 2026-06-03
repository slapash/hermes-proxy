"""Test that /api/chat has rate limiting that returns 429.

Uses low RPM/burst env vars so we don't need to send 30+ requests.
We override the module-level config by patching the globals.
"""
import sys
import os
import time

sys.path.insert(0, "/home/hermes/apps/hermes-proxy")

from fastapi.testclient import TestClient
import server
import core


def _auth_cookie():
    """Return a valid signed auth cookie."""
    token = core._make_token()
    return {"hermes-proxy-auth": token}


def test_chat_rate_limit_returns_429():
    """After exceeding burst, /api/chat returns 429 with Retry-After header."""
    # Override rate limit config for testing: 3 req/min, burst=2
    original_rpm = core._CHAT_RPM
    original_burst = core._CHAT_BURST
    original_limits = core._CHAT_RATE_LIMITS.copy()
    core._CHAT_RPM = 3
    core._CHAT_BURST = 2
    core._CHAT_RATE_LIMITS.clear()

    try:
        client = TestClient(server.app)
        # Use the SAME auth cookie for all requests so they share a rate limit key
        token = core._make_token()
        cookies = {"hermes-proxy-auth": token}

        # Consume burst (2 requests) — these should succeed (or get 500 from
        # no upstream, but NOT 429)
        for _ in range(2):
            resp = client.post(
                "/api/chat",
                json={"message": "test"},
                cookies=cookies,
            )
            assert resp.status_code != 429, f"Should not be rate-limited yet, got {resp.status_code}"

        # 3rd request should be rate-limited
        resp = client.post(
            "/api/chat",
            json={"message": "test"},
            cookies=cookies,
        )
        assert resp.status_code == 429, f"Expected 429, got {resp.status_code}"
        assert "retry_after" in resp.json()
        assert "Retry-After" in resp.headers
    finally:
        core._CHAT_RPM = original_rpm
        core._CHAT_BURST = original_burst
        core._CHAT_RATE_LIMITS.clear()
        core._CHAT_RATE_LIMITS.update(original_limits)


def test_rate_limiter_refills_over_time():
    """After waiting, tokens refill and requests succeed again."""
    core._CHAT_RPM = 60  # 1/sec
    core._CHAT_BURST = 1
    core._CHAT_RATE_LIMITS.clear()

    try:
        limiter = core._SlidingWindowRateLimiter(rpm=60, burst=1)
        # Consume the single token
        assert limiter.allow() is True
        # Immediately, should be denied
        assert limiter.allow() is False
        # Wait 1.1 seconds for token refill
        time.sleep(1.1)
        assert limiter.allow() is True
    finally:
        core._CHAT_RATE_LIMITS.clear()


if __name__ == "__main__":
    for f in [test_chat_rate_limit_returns_429, test_rate_limiter_refills_over_time]:
        f()
        print(f"✓ {f.__name__}")
    print("\nAll tests passed.")