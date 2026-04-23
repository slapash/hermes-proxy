"""Regression checks for streaming scroll behavior."""
from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')


def test_streaming_uses_scroll_lock_instead_of_unconditional_bottom_jump():
    """Streaming token updates must not yank the viewport down after user scrolls up."""
    src = (APP_ROOT / 'static' / 'app.js').read_text()

    assert 'let autoScrollLocked = true;' in src
    assert 'function isThreadNearBottom' in src
    assert 'function maybeScrollToBottom' in src
    assert "thread.addEventListener('scroll'" in src
    assert 'maybeScrollToBottom();' in src

    stream_section = src[src.index('while (true) {'):src.index('// Final render')]
    assert 'scrollToBottom();' not in stream_section
