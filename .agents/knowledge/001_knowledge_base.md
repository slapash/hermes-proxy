# Hermes Proxy Plugin Knowledge Base

> Living document updated by the Orchestrator after each task.
> This is the Coder's memory — it persists across tasks.

---

## Critical Constraints

1. **One file per plugin.** All code goes in `plugins/<name>.js`. No build step.
2. **Self-bootstrapping IIFE.** Plugin must work when loaded via `<script type="module" src="...">`.
3. **HermesProxy event bus.** Available hooks: `messageRendered`, `beforeSend`, `sessionListRendered`, `sessionChanged`.
4. **No external CDN deps.** Vanilla JS + DOM only.
5. **localStorage guards.** EVERY `localStorage` access wrapped in `try/catch`.
6. **Idempotent init.** Running twice must not double-bind events. Use `if (!window.__pluginNameInited)` guard.
7. **No `innerHTML` with untrusted data.** Use `textContent` or `DOMPurify` if available.
8. **No `eval` or `Function` constructor.** Not negotiable.
9. **Plugins target `/home/hermes/apps/hermes-proxy/`** as the working directory.
10. **Tests:** run with `cd /home/hermes/apps/hermes-proxy && source venv/bin/activate && pytest tests/ -q`.

---

## Reusable Snippet: Plugin Skeleton

```javascript
// plugins/YOUR-PLUGIN.js
(function () {
  'use strict';
  if (window.__<NameCamelCase>Inited) return;
	window.__<NameCamelCase>Inited = true;

  const PLUGIN_NAME = '<name>';
  const STORAGE_KEY = `hermes-${PLUGIN_NAME}`;

  /* helpers */
  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }
  function on(event, fn) { window.HermesProxy && HermesProxy.on(event, fn); }

  /* ----------------------------------------------------------------- */
  // Plugin logic here

  /* ----------------------------------------------------------------- */
  console.log(`[${PLUGIN_NAME}] loaded`);
})();
```

---

## Reusable Snippet: Debounced localStorage Save

```javascript
function saveDraft(sessionId, text) {
  try {
    const key = sessionId ? `hermes-draft:${sessionId}` : 'hermes-draft:global';
    if (!text || !text.trim()) {
      localStorage.removeItem(key);
      return;
    }
    localStorage.setItem(key, text);
  } catch (e) {
    console.warn('[autosave] localStorage quota exceeded:', e);
  }
}
```

---

## Reusable Snippet: Inserting Element After Bubble

```javascript
function insertAfterBubble(bubbleEl, cardEl) {
  const msgEl = bubbleEl.closest('.msg');
  if (!msgEl) return;
  msgEl.parentNode.insertBefore(cardEl, msgEl.nextSibling);
}
```

---

## Reusable Snippet: Is URL Inside Code Block?

```javascript
function isInsideCodeBlock(el) {
  let p = el;
  while (p) {
    if (p.tagName === 'PRE' || p.tagName === 'CODE') return true;
    p = p.parentElement;
  }
  return false;
}
```

---

## Common Mistakes to Avoid

- ❌ **Double event listeners** → Always use init-guard (`__NameInited`).
- ❌ **Forgetting `window.HermesProxy` may not exist** → Check with `window.HermesProxy && ...`.
- ❌ **Modifying `msgInput.value` without triggering `input` event** → Dispatch `input` after value change if auto-resize needed.
- ❌ **Not escaping HTML** → Use `textContent` for user-controlled strings.
- ❌ **Not handling empty states** → e.g. no session selected, empty input, no messages.
- ❌ **Forgetting to `preventDefault()` on drag/drop** → Browser opens file on drop.
- ❌ **Not wrapping `localStorage`** → Private browsing mode throws.
- ❌ **Touching DOM before ready** → Wait for `DOMContentLoaded`, but in plugins this is usually fine since they're loaded deferred.
- ❌ **Not cleaning up on plugin re-init** → If plugin is hot-reloaded, unbind old listeners.
- ❌ **Forgetting `encodeURIComponent`** when passing URLs to server endpoints.
- ❌ **Not handling `fetch` failures** → Always `.catch()` or `try/catch await`.

---

## API Reference

```
GET /api/messages/{session_id}
  { "messages": [{"id": "...", "role": "user|assistant", "content": "...", "ts": "..."}], "session_id": "..." }

POST /api/attachments
  multipart/form-data
  Returns: { "url": "/uploads/<filename>", "markdown": "![filename](url)" }

GET /api/og?url=<url>
  Returns: { "title": "...", "description": "...", "image": "...", "url": "..." }

POST /api/session
  JSON: {} -> create new session, returns { "session_id": "..." }

POST /api/rename
  JSON: { "session_id": "...", "name": "..." } -> renames session
```

---

## Scoring History

| Plugin | Task | Score | Notes |
|---|---|---|---|
| (none yet) | | | |
