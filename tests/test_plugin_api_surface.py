"""Static source-analysis tests for the stable plugin API surface.

These tests do NOT run a browser. They read the JS source files and verify:
1. HermesProxy.version is declared in hermes-proxy.js
2. Stable API methods exist in app.js
3. First-party plugins no longer poke DOM internals directly
   (no direct localStorage.getItem('hermes-session-id'),
    no direct document.getElementById('new-session-btn'), etc.)
"""
import json
import re
from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')
STATIC = APP_ROOT / 'static'
PLUGINS = STATIC / '__plugins__'


def test_hermes_proxy_has_version():
    """hermes-proxy.js must expose a numeric version."""
    src = (STATIC / 'hermes-proxy.js').read_text()
    m = re.search(r'version\s*:\s*(\d+)', src)
    assert m, "HermesProxy.version not found in hermes-proxy.js"
    version = int(m.group(1))
    assert version >= 1


def test_app_js_exposes_stable_api_methods():
    """app.js (plus hermes-proxy.js) must attach the declared v1 API methods to HermesProxy."""
    src = (STATIC / 'hermes-proxy.js').read_text() + '\n' + (STATIC / 'app.js').read_text()
    methods = [
        'getSessionId','getInputValue','setInputValue','clearInput','focusInput',
        'newSession','clearThread','focusSearch','sendMessage','showToast',
        'uploadAttachment','queueAttachments','setTheme','registerTheme',
    ]
    missing = []
    for m in methods:
        if not (re.search(rf'HermesProxy\.{re.escape(m)}\s*=', src) or re.search(rf'\b{re.escape(m)}\s*\(', src)):
            missing.append(m)
    assert not missing, f"Missing API methods: {missing}"


def test_no_first_party_plugin_reads_hermes_session_id_directly():
    """First-party plugins should use HermesProxy.getSessionId() instead of
    directly calling localStorage.getItem('hermes-session-id') outside a helper."""
    offenders = []
    pattern = re.compile(r"localStorage\.getItem\(['\"]hermes-session-id['\"]\)")
    helper_pattern = re.compile(r"function\s+getCurrentSessionId\s*\(")
    for f in PLUGINS.glob('*.js'):
        src = f.read_text()
        matches = list(pattern.finditer(src))
        if not matches:
            continue
        all_in_helper = all(
            helper_pattern.search(src[:m.start()]) is not None
            for m in matches
        )
        if not all_in_helper:
            offenders.append(f.name)
    assert not offenders, (
        f"Plugins still reading localStorage 'hermes-session-id' directly outside helper: {offenders}"
    )


def test_no_first_party_plugin_clicks_new_session_btn_directly():
    """Plugins should call HermesProxy.newSession() instead of
    document.getElementById('new-session-btn').click()."""
    offenders = []
    pattern = re.compile(r"getElementById\(['\"]new-session-btn['\"]\).*\.click\(\)")
    for f in PLUGINS.glob('*.js'):
        if pattern.search(f.read_text()):
            offenders.append(f.name)
    assert not offenders, f"Plugins still clicking #new-session-btn directly: {offenders}"


def test_no_first_party_plugin_focuses_search_input_directly():
    """Plugins should call HermesProxy.focusSearch() instead of
    document.getElementById('search-input').focus()."""
    offenders = []
    pattern = re.compile(r"getElementById\(['\"]search-input['\"]\).*\.focus\(\)")
    for f in PLUGINS.glob('*.js'):
        if pattern.search(f.read_text()):
            offenders.append(f.name)
    assert not offenders, f"Plugins still focusing #search-input directly: {offenders}"


def test_no_first_party_plugin_clears_thread_by_id():
    """Plugins should call HermesProxy.clearThread() instead of manually
    removing children from document.getElementById('thread')."""
    offenders = []
    pattern = re.compile(r"getElementById\(['\"]thread['\"]\).*removeChild")
    for f in PLUGINS.glob('*.js'):
        if pattern.search(f.read_text()):
            offenders.append(f.name)
    assert not offenders, f"Plugins still clearing #thread by direct DOM manipulation: {offenders}"


def test_slash_commands_prefers_stable_api():
    """slash-commands.js must prefer HermesProxy.newSession / clearThread /
    focusSearch when available (graceful fallback to DOM is acceptable)."""
    src = (PLUGINS / '4_slash-commands.js').read_text()
    assert "hp.newSession" in src or "HermesProxy.newSession" in src
    assert "hp.clearThread" in src or "HermesProxy.clearThread" in src
    assert "hp.focusSearch" in src or "HermesProxy.focusSearch" in src
