# Dogfood QA Report: `feature/plugins-suite`

## Run Date: 2026-04-22
## Tests: 10 collected, 9 passed, 1 failed

---

## Patches Applied During QA

### 1. `.env.example` — Added plugin documentation with `local:` prefix
**Before:** No plugin examples in `.env.example`
**After:** Added commented examples showing `local:/abs/path/...` and `https://...` formats

### 2. `README.md` — Added `HERMES_PROXY_PLUGIN_{N}` to env var table
**Before:** No plugin env vars in configuration table
**After:** Added row + explanation that bare filenames are rejected

### 3. `PR-PLUGIN-ENGINE.md` — Fixed bare filenames to `local:` prefixed paths
**Before:** `export HERMES_PROXY_PLUGIN_LIGHT_THEME=light-theme.js`
**After:** `export HERMES_PROXY_PLUGIN_0="local:/home/hermes/apps/hermes-proxy/plugins/light-theme.js"`

### 4. `plugins/session-favorites.js` — Fixed CSS trailing `}`
**Before:** `...opacity:0.6}`
**After:** `...opacity:0.6;`

---

## Test Results

| Test | Status |
|---|---|
| test_emit_isolates_handler_errors | **FAILED** (pre-existing: `HermesProxy` moved to external file, test still searches inline HTML) |
| test_csp_allows_static_plugins | PASSED |
| test_plugin_scripts_injected | PASSED |
| test_light_contrast_passes_wcag_aa | PASSED |
| test_light_code_blocks_not_forced_dark | PASSED |
| test_toggle_button_source_has_dual_insertion | PASSED |
| test_path_traversal_local_rejected | PASSED |
| test_allowed_local_path_accepted | PASSED |
| test_symlink_outside_safe_dir_rejected | PASSED |
| test_large_file_rejected | PASSED |

## Pre-existing Failures

- **`test_bus_robustness.py`** fails because `HermesProxy` was moved from inline HTML to `static/hermes-proxy.js` in commit `c012bc4`. The test still searches `index.html` with `html.find("window.HermesProxy = {")` which now returns -1.
- This failure is **not** introduced by the `feature/plugins-suite` branch.

---

## Plugin Manual Tests

| Plugin | Status | Method |
|---|---|---|
| light-theme | PASSED | Click ☀️/🌙 toggles theme |
| session-favorites | PASSED | Click ☆ toggles star + localStorage + reorder |
| slash-commands | UNABLE TO TEST | Requires keydown simulation |
| draft-autosave | UNABLE TO TEST | Requires page reload simulation |
| Others | UNTESTED | Need real messages / clipboard / drag-drop |

---

## Console Errors
**0 JavaScript errors** during all interactions.
