"""Test /api/og endpoint enhancements."""
import sys
import time

# Ensure server modules are importable
sys.path.insert(0, "/home/hermes/apps/hermes-proxy")

from core import _extract_favicon, _domain_favicon, _extract_title


def test_extract_favicon_absolute():
    html = '<link rel="icon" href="https://example.com/f.ico">'
    assert _extract_favicon(html, "https://example.com/") == "https://example.com/f.ico"


def test_extract_favicon_schemeless():
    html = '<link rel=\"icon\" href=\"//cdn.example.com/f.ico\">'
    assert _extract_favicon(html, "https://example.com/") == "https://cdn.example.com/f.ico"


def test_extract_favicon_relative():
    html = '<link rel=\"shortcut icon\" href=\"/static/favicon.ico\">'
    assert _extract_favicon(html, "https://example.com/") == "https://example.com/static/favicon.ico"


def test_extract_favicon_none():
    html = '<html><head></head></html>'
    assert _extract_favicon(html, "https://example.com/") == ""


def test_domain_favicon():
    assert _domain_favicon("https://example.com/page") == "https://example.com/favicon.ico"


def test_extract_title_basic():
    html = '<title>Hello World</title>'
    assert _extract_title(html) == "Hello World"


def test_extract_title_empty():
    html = '<html></html>'
    assert _extract_title(html) == ""


if __name__ == "__main__":
    for f in [
        test_extract_favicon_absolute,
        test_extract_favicon_schemeless,
        test_extract_favicon_relative,
        test_extract_favicon_none,
        test_domain_favicon,
        test_extract_title_basic,
        test_extract_title_empty,
    ]:
        f()
        print(f"✓ {f.__name__}")
    print("\nAll tests passed.")
