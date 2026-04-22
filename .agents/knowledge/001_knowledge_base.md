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

## Reusable Snippet: Escaping HTML Without innerHTML

**DON'T:**
```javascript
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML
}
```

**DO:**
```javascript
function esc(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
```

The `innerHTML` from `textContent` is **NOT** safe in CSP-hardened environments and counts as `innerHTML` usage for security audits.

---

## Reusable Snippet: Clear DOM Without innerHTML

**DON'T:** `element.innerHTML = ''`

**DO:**
```javascript
while (element.firstChild) element.removeChild(element.firstChild);
```

---

## Common Mistakes to Avoid

- ❌ **Double event listeners** → Always use init-guard (`__NameInited`).
- ❌ **Forgetting `window.HermesProxy` may not exist** → Check with `window.HermesProxy && ...`.
- ❌ **Modifying `msgInput.value` without triggering `input` event** → Dispatch `input` after value change if auto-resize needed.
- ❌ **Using `innerHTML` ANYWHERE** → Even for escaping helpers or clearing nodes. Use pure DOM.
- ❌ **Not escaping HTML** → Use `textContent` for user-controlled strings.
- ❌ **Not handling empty states** → e.g. no session selected, empty input, no messages.
- ❌ **Forgetting to `preventDefault()` on drag/drop** → Browser opens file on drop.
- ❌ **Not wrapping `localStorage`** → Private browsing mode throws.
- ❌ **Touching DOM before ready** → Wait for `DOMContentLoaded`, but in plugins this is usually fine since they're loaded deferred.
- ❌ **Not cleaning up on plugin re-init** → If plugin is hot-reloaded, unbind old listeners.
- ❌ **Forgetting `encodeURIComponent`** when passing URLs to server endpoints.
- ❌ **Not handling `fetch` failures** → Always `.catch()` or `try/catch await`.
- ❌ **Referencing functions from app.js scope** → `openSidebar`, `scrollToBottom`, `esc`, etc. Do NOT exist inside plugin scope.
- ❌ **Using `(new URL(url)).hostname` without try/catch** → Invalid URLs throw.

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
| core-hooks | core-hooks-a, core-hooks-b | 8/10 | +init guard, +hook placement. Minor: used try/catch inside emit pattern |
| core-api-og | core-api-og-a | 9/10 | +timeout +scheme validation +regex robustness. Minor: no server-side cache |
| core-api-attachments | core-api-attachments-a | 9/10 | +whitelist +size gate +filename sanitization +static mount |
| auto-linkifier | task-linkify-1 | 10/10 | +init guard +safe wrapper +no innerHTML +TreeWalker approach |
| draft-autosave | task-autosave-1, task-autosave-2 | 9/10 | +debounced save +sessionChanged hook +indicator UI. Minor: had innerHTML in first version (fixed) |
| slash-commands | task-slash-1, task-slash-2 | 8/10 | +dropdown +keyboard nav +idempotent. -innerHTML in dropdown + help bubble + /clear (fixed) |
| session-favorites | task-fav-1 | 9/10 | +star toggle +sort +localStorage persist. Minor: init insert before title needed correction |
| export-chat-md | task-export-1 | 9/10 | +download logic +strip HTML +metadata header. -innerHTML in stripHtml (fixed) |
| open-graph-card | task-ogcard-1 | 9/10 | +cache +image skip +card creation +error isolation. -innerHTML in createCard (fixed) |
| image-paste-preview | task-imgpaste-1, task-imgpaste-2 | 9/10 | +paste intercept +upload on drop +preview strip |
| file-drop-zone | task-filedrop-1 | 9/10 | +overlay +multi-file +cursor insert +confirm dialog |

---

## Orchestrator Lessons Learned

1. **delegate_task() fails on credit/token limits with kimi-k2.6 configured as delegation model.** Need to configure a cheaper model (`openrouter/gpt-4o-mini` or `openrouter/llama-3.1-8b`) in `config.yaml` under `delegation.model` for subagents.
2. **Large context in subagent `context` field causes HTTP 402.** Keep context under 8k tokens. Provide file paths + task JSON instead of dumping full source code.
3. **Subagent toolsets need `terminal` + `file` minimum for JS plugin work.** `web` is optional but useful for fetching spec references.
4. **Self-coding is faster for small IIFE plugins (<120 lines).** Subagents add 10-20s latency.
5. **innerHTML is a recurring security anti-pattern.** Even `div.textContent ... div.innerHTML` is flagged. Use string replace or createElement.
6. **app.js functions are NOT in plugin scope.** `openSidebar`, `scrollToBottom`, `esc()`, `loadSessions` are private to the app.js IIFE. Plugins must call vanilla DOM APIs or HermesProxy hooks.
7. **TreeWalker is robust for scanning message text nodes** without breaking markdown-rendered HTML structures.
8. **`sessionListRendered` is already working in the deployed app.** But `sessionChanged` fires before messages load, which is fine for draft-autosave but might need an additional hook for post-load if other plugins need it.

---

## Next Steps / TODO

- [ ] Configure cheaper delegation model in `~/.hermes/config.yaml`
- [ ] Implement `/api/og` server-side cache in RAM (LRU, TTL 1hr)
- [ ] `/api/attachments` multi-file support (FormData array)
- [ ] Plugin manifest JSON (`plugins/manifest.json`) for auto-discovery
- [ ] Plugin hot-reload without server restart
- [ ] Add `messageRendered` hook for pre-existing messages on page load
- [ ] Guard subagent review with actual runtime testing in browser

### Suggested Cheaper Models for Subagent Delegation

| Model | Provider | Cost | Quality |
|---|---|---|---|
| `openrouter/gpt-4o-mini` | OpenRouter | ~$0.15/Mtok | High for JS |
| `openrouter/mistralai/mistral-7b-instruct` | OpenRouter | ~$0.06/Mtok | Adequate |
| `openrouter/meta-llama/llama-3.1-8b-instruct` | OpenRouter | ~$0.05/Mtok | OK for small tasks |
| `kimi-k2-6` (current) | Ollama/local | Free | Good but token-hungry |
