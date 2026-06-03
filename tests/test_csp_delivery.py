"""Tests for CSP headers and plugin delivery"""
import os
import sys

sys.path.insert(0, '/home/hermes/apps/hermes-proxy')
os.chdir('/home/hermes/apps/hermes-proxy')
os.environ.setdefault('HERMES_PROXY_SIGNING_KEY', '9d447d6c2c7a73365f2bd9ab2328ff689d5cf65f1c9773624db21765831b3f85')
os.environ.setdefault('HERMES_PROXY_PASSWORD', 'testpass123')
os.environ.setdefault('API_SERVER_KEY', 'testkey123')

from fastapi.testclient import TestClient
from server import app
from core import _make_token


def _auth_cookie():
    token = _make_token()
    return {"hermes-proxy-auth": token}


client = TestClient(app)


def test_csp_allows_static_plugins():
    """CSP script-src must allow /static/__plugins__/ so local plugins load."""
    response = client.get("/", cookies=_auth_cookie())
    assert response.status_code == 200
    csp = response.headers.get("Content-Security-Policy", "")
    # script-src 'self' should cover same-origin /static/__plugins__/
    assert "script-src 'self'" in csp, f"Missing 'self' in script-src: {csp}"
    # Should not have restrictive nonce/hashes that break plugins
    assert "'unsafe-inline'" in csp, f"Missing 'unsafe-inline' in CSP: {csp}"


def test_plugin_scripts_injected():
    """Plugin script tags must appear before </head>."""
    response = client.get("/", cookies=_auth_cookie())
    html = response.text
    idx_head = html.find("</head>")
    idx_script = html.rfind("<script ", 0, idx_head)
    assert idx_script != -1, "No script found before </head>"
