"""Tests for plugin security

Path traversal:
- local:../../../etc/passwd must be rejected
- local:/tmp/test.js must only work if under SAFE_PLUGINS_DIR
- Symlinks pointing outside SAFE_PLUGINS_DIR must NOT be followed beyond safe dir
- Files >10MB must be rejected
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Patch the module before importing it
os.environ.setdefault('HERMES_PROXY_SIGNING_KEY', '9d447d6c2c7a73365f2bd9ab2328ff689d5cf65f1c9773624db21765831b3f85')
os.environ.setdefault('HERMES_PROXY_PASSWORD', 'testpass123')
os.environ.setdefault('API_SERVER_KEY', 'testkey123')

# Clear any pre-loaded HERMES_PROXY_PLUGIN_* vars to avoid side effects
for k in list(os.environ.keys()):
    if k.startswith('HERMES_PROXY_PLUGIN_'):
        del os.environ[k]

def test_path_traversal_local_rejected():
    """A local: path containing '..' must be blocked (path traversal)."""
    from server import _load_plugins, _PLUGIN_DIR
    
    with tempfile.TemporaryDirectory() as tmpdir:
        outside = Path(tmpdir) / "outside"
        outside.mkdir()
        evil_file = outside / "evil.js"
        evil_file.write_text("// evil\n")
        
        # Use .. to escape
        evil_traversal = Path(tmpdir) / "safe" / ".." / "outside" / "evil.js"
        os.environ["HERMES_PROXY_PLUGIN_0"] = f"local:{evil_traversal}"
        scripts, errors = _load_plugins(_PLUGIN_DIR)
        
        assert len(scripts) == 0, f"Expected 0 scripts, got {scripts}"
        assert any("traversal" in e.lower() for e in errors), \
            f"Expected path-traversal error in {errors}"


def test_allowed_local_path_accepted():
    """A local: path within a safe dir must be accepted."""
    from server import _load_plugins, _PLUGIN_DIR
    
    with tempfile.TemporaryDirectory() as tmpdir:
        safe = Path(tmpdir) / "safe"
        safe.mkdir()
        good = safe / "good.js"
        good.write_text("// good\n")
        
        os.environ["HERMES_PROXY_PLUGIN_0"] = f"local:{good}"
        # We need to set the plugin dir to the parent of safe so the file is under it
        plugin_dir = Path(tmpdir) / "plugins"
        plugin_dir.mkdir()
        
        scripts, errors = _load_plugins(plugin_dir)
        assert len(scripts) == 1
        assert len(errors) == 0


def test_symlink_outside_safe_dir_rejected():
    """Even if symlink is inside safe dir, target outside must be rejected."""
    from server import _load_plugins, _PLUGIN_DIR
    
    with tempfile.TemporaryDirectory() as tmpdir:
        safe = _PLUGIN_DIR  # or a temp subdir
        outside = Path(tmpdir) / "secret_file"
        outside.write_text("secret")
        
        sym = safe / "link.js"
        if sym.exists():
            sym.unlink()
        sym.symlink_to(outside)
        
        os.environ["HERMES_PROXY_PLUGIN_0"] = f"local:{sym}"
        try:
            scripts, errors = _load_plugins(safe)
            
            assert len(scripts) == 0
            assert any("symlink" in e.lower() or "outside" in e.lower() or "escapes" in e.lower() for e in errors)
        finally:
            if sym.exists():
                sym.unlink()


def test_large_file_rejected():
    """Files over 10MB must be rejected to prevent disk fill."""
    from server import _load_plugins, _PLUGIN_DIR
    
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_dir = _PLUGIN_DIR  # use real plugin dir
        big = plugin_dir / "big.js"
        # 12MB of zeros
        big.write_bytes(b'0' * (12 * 1024 * 1024))
        
        os.environ["HERMES_PROXY_PLUGIN_0"] = f"local:{big}"
        try:
            scripts, errors = _load_plugins(plugin_dir)
            
            assert len(scripts) == 0
            assert any("size" in e.lower() or "10mb" in e.lower() or "too large" in e.lower() for e in errors)
        finally:
            if big.exists():
                big.unlink()
