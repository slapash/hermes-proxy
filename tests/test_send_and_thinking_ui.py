"""Regression tests for compact send button and stable thinking indicator UI."""
from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')


def test_send_button_uses_accessible_icon_not_text_label():
    html = (APP_ROOT / 'static' / 'index.html').read_text()

    assert 'id="send-btn" title="Send" aria-label="Send"' in html
    assert '<svg viewBox="0 0 24 24"' in html
    assert '<button id="send-btn">Send</button>' not in html


def test_send_button_matches_composer_border_style():
    html = (APP_ROOT / 'static' / 'index.html').read_text()

    assert '#send-btn {' in html
    assert 'background: var(--surface2); color: var(--text); border: 1px solid var(--border); border-radius: 6px;' in html
    assert '#send-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent);' in html


def test_core_thinking_indicator_has_fixed_layout():
    html = (APP_ROOT / 'static' / 'index.html').read_text()

    assert 'width: 300px;' in html
    assert 'min-width: 300px;' in html
    assert 'max-width: 300px;' in html
    assert 'text-overflow: ellipsis;' in html
    assert 'flex: 0 0 38px;' in html


def test_cute_thinking_plugin_has_fixed_layout():
    src = (APP_ROOT / 'plugins' / 'cute-thinking-progress.js').read_text()

    assert 'width:320px;' in src
    assert 'min-width:320px;' in src
    assert 'max-width:320px;' in src
    assert 'text-overflow:ellipsis;' in src
    assert 'flex:0 0 38px;' in src
