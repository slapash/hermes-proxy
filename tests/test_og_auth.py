"""Test that /api/og requires authentication."""
import sys

sys.path.insert(0, "/home/hermes/apps/hermes-proxy")

from core import _SIGNING_KEY, _make_token
from fastapi.testclient import TestClient
import server


def _auth_cookie():
    token = _make_token()
    return {"hermes-proxy-auth": token}


def test_og_requires_auth():
    """Unauthenticated requests to /api/og should return 401."""
    client = TestClient(server.app)
    resp = client.get("/api/og", params={"url": "https://example.com"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


def test_og_authenticated_returns_data():
    """Authenticated requests should reach the OG fetch logic (may fail on network, but not 401)."""
    client = TestClient(server.app)
    # Use an authenticated session
    resp = client.get(
        "/api/og",
        params={"url": "https://example.com"},
        cookies=_auth_cookie(),
    )
    # The request should not be rejected as unauthenticated.
    # It may return 200 (with OG data) or 500 (network error in test env),
    # but it must NOT return 401.
    assert resp.status_code != 401, f"Authenticated request returned 401: {resp.text}"


if __name__ == "__main__":
    for f in [test_og_requires_auth, test_og_authenticated_returns_data]:
        f()
        print(f"✓ {f.__name__}")
    print("\nAll tests passed.")