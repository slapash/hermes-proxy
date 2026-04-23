"""Tests for plugin-extensible thinking/progress UI hooks."""
from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')


def test_core_exposes_thinking_lifecycle_hooks():
    """Core must emit lifecycle hooks so plugins can replace thinking UI behavior."""
    src = (APP_ROOT / 'static' / 'app.js').read_text()

    assert "emitThinking('thinkingCreated'" in src
    assert "emitThinking('thinkingUpdated'" in src
    assert "emitThinking('thinkingRemoved'" in src
    assert 'startedAt' in src
    assert 'elapsedMs' in src


def test_cute_thinking_plugin_listens_to_lifecycle_hooks():
    """Cute thinking plugin should be hook-driven, not a MutationObserver hack."""
    src = (APP_ROOT / 'plugins' / 'cute-thinking-progress.js').read_text()

    assert "on('thinkingCreated'" in src
    assert "on('thinkingUpdated'" in src
    assert "on('thinkingRemoved'" in src
    assert 'MutationObserver' not in src
    assert 'prefers-reduced-motion' in src
