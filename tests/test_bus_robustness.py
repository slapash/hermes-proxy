"""Tests for HermesProxy bus robustness — error isolation

The HermesProxy emit method must isolate handlers: if one plugin throws,
the rest still run. This test currently represents the DESIRED behavior
(post-fix). Before fixing, we will run this to confirm it fails.
"""
import json
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')

def test_emit_isolates_handler_errors():
    """If one handler throws, others must still run."""
    # Extract HermesProxy script from hermes-proxy.js (the actual implementation)
    proxy_code = (APP_ROOT / "static" / "hermes-proxy.js").read_text()
    # Strip the leading comment line(s) if present to keep it clean
    proxy_code = proxy_code.strip()
    
    js = f"""
var window = {{}};
{proxy_code}

let firstRan = false;
let thirdRan = false;
window.HermesProxy.on('test', () => {{ firstRan = true; }});
window.HermesProxy.on('test', () => {{ throw new Error('boom'); }});
window.HermesProxy.on('test', () => {{ thirdRan = true; }});
try {{
  window.HermesProxy.emit('test');
}} catch (e) {{
  console.log(JSON.stringify({{
    pass: false,
    firstRan,
    thirdRan,
    error: e.message,
  }}));
  process.exit(0);
}}
console.log(JSON.stringify({{
  pass: firstRan && thirdRan,
  firstRan,
  thirdRan,
}}));
"""
    result = subprocess.run(
        ["node", "-e", js],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"JS exited {result.returncode}: {result.stderr}"
    # Parse last JSON line
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            try:
                data = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    else:
        raise AssertionError(f"No JSON found in output: {result.stdout}")
    
    assert data.get("pass"), f"Bus did NOT isolate errors: {data}"
    assert data.get("firstRan"), "First handler did not run"
    assert data.get("thirdRan"), "Third handler did not run after error"
