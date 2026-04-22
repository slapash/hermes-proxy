"""Tests for light-theme.js correctness"""
import json
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')

def _run_js(code: str) -> dict:
    """Run JS via node -e and return parsed JSON from last JSON line."""
    result = subprocess.run(
        ["node", "-e", code],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"JS error: {result.stderr}\nstdout: {result.stdout}"
    for line in reversed(result.stdout.strip().split("\n")):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"No JSON in output: {result.stdout}")

def test_light_contrast_passes_wcag_aa():
    """Light theme accent-on-background must be readable (>= 4.5:1)."""
    js = """
function luminance(r, g, b) {
  const a = [r, g, b].map(v => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
}
function contrast(hexBg, hexFg) {
  const p = h => [parseInt(h.slice(1,3),16), parseInt(h.slice(3,5),16), parseInt(h.slice(5,7),16)];
  const [r1,g1,b1]=p(hexBg), [r2,g2,b2]=p(hexFg);
  const l1 = luminance(r1,g1,b1)+0.05, l2=luminance(r2,g2,b2)+0.05;
  return Math.max(l1,l2)/Math.min(l1,l2);
}
const src = require('fs').readFileSync('/home/hermes/apps/hermes-proxy/plugins/light-theme.js','utf8');
const css = src.slice(src.indexOf('`')+1, src.lastIndexOf('`'));
const bg = css.match(/\\-\\-bg:\\s*(#[0-9a-fA-F]+)/)[1];
const accent = css.match(/\\-\\-accent:\\s*(#[0-9a-fA-F]+)/)[1];
const ratio = contrast(bg, accent);
console.log(JSON.stringify({pass: ratio>=4.5, bg, accent, ratio: +ratio.toFixed(2)}));
"""
    data = _run_js(js)
    assert data["pass"], f"Contrast {data['ratio']} fails WCAG AA for {data['accent']} on {data['bg']}"

def test_light_code_blocks_not_forced_dark():
    """Light theme pre/code blocks must NOT be hardcoded to dark backgrounds."""
    js = """
const src = require('fs').readFileSync('/home/hermes/apps/hermes-proxy/plugins/light-theme.js','utf8');
const css = src.slice(src.indexOf('`')+1, src.lastIndexOf('`'));
const preBg = css.match(/:root\[data-theme="light"\]\\s+pre\\s*\\{[^}]*background:\\s*(#[0-9a-fA-F]+)/);
const preColor = css.match(/:root\[data-theme="light"\]\\s+pre\\s*\\{[^}]*color:\\s*(#[0-9a-fA-F]+)/);
console.log(JSON.stringify({
  hasPreBg: !!preBg,
  preBg: preBg ? preBg[1] : null,
  hasPreColor: !!preColor,
  preColor: preColor ? preColor[1] : null,
  notDark: preBg ? parseInt(preBg[1].slice(1), 16) > 0xdddddd : false,
}));
"""
    data = _run_js(js)
    assert data["hasPreBg"], "Missing light-theme pre background rule"
    assert data["notDark"], f"pre still has dark background: {data['preBg']}"
    assert data["hasPreColor"], "Missing light-theme pre color rule"

def test_toggle_button_source_has_dual_insertion():
    """Toggle button must support both mobile (topbar) and desktop (sidebar-header)."""
    js = """
const src = require('fs').readFileSync('/home/hermes/apps/hermes-proxy/plugins/light-theme.js','utf8');
const hasTopbar = /topbar/.test(src);
const hasSidebarHeader = /sidebar-header/.test(src);
const hasDisplayCheck = /getComputedStyle/.test(src);
console.log(JSON.stringify({
  hasTopbar,
  hasSidebarHeader,
  hasDisplayCheck,
  dualInsertion: hasTopbar && hasSidebarHeader && hasDisplayCheck,
}));
"""
    data = _run_js(js)
    assert data["dualInsertion"], "Toggle button must support both mobile topbar and desktop sidebar-header"
