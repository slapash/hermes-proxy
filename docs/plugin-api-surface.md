# Plugin API Surface Design — Hermes Proxy

## Objective
Plugins currently poke DOM internals (`document.getElementById('msg-input')`,
`document.getElementById('thread')`, `localStorage.getItem('hermes-session-id')`).
This breaks whenever the core app renames an ID or moves state into a closure.

We will expose a **stable, versioned API** on `window.HermesProxy` so that
first-party and third-party plugins can be written against a contract rather than
against the DOM.

## Versioning
The API carries a monotonic integer version:
```js
window.HermesProxy.version // => 1
```
Plugins should guard on it:
```js
if (!window.HermesProxy || window.HermesProxy.version < 1) {
  console.warn('[my-plugin] HermesProxy v1 not available');
  return;
}
```

## Event bus (existing, stable)
```js
HermesProxy.on(eventName, handler)   // subscribe
HermesProxy.emit(eventName, ...args)  // publish (plugins should RARELY call this)
```

Emitted by core:
- `sessionListRendered` — `sessionList` (Element)
- `sessionChanged` — `sessionId` (string|null)
- `messageRendered` — `bubble` (Element), `{ role, content, ts }`
- `thinkingCreated` — `{ el, bubbleEl, startedAt, label }`
- `thinkingUpdated` — `{ el, bubbleEl, label, tool, raw }`
- `thinkingRemoved` — `{ el, bubbleEl, elapsedMs }`
- `beforeSend` — `text` (string)
- `themeChange` — `themeName` (string)
- `themeRegister` — `themeName, colors` (for theme plugins)

## Imperative APIs (new / to be hardened)

| API | Return | What it does |
|-----|--------|--------------|
| `HermesProxy.getSessionId()` | `string \| null` | Current active session id (stable accessor instead of `localStorage.getItem('hermes-session-id')`) |
| `HermesProxy.getInputValue()` | `string` | Current text in the message input |
| `HermesProxy.setInputValue(text)` | `void` | Set text in the message input and dispatch `input` event |
| `HermesProxy.clearInput()` | `void` | Clear the message input |
| `HermesProxy.focusInput()` | `void` | Focus the message input |
| `HermesProxy.newSession()` | `Promise<void>` | Trigger new-session (same as clicking the new-session button) |
| `HermesProxy.clearThread()` | `void` | Remove all messages from the thread |
| `HermesProxy.focusSearch()` | `void` | Focus the search bar |
| `HermesProxy.sendMessage(text, options?)` | `Promise<void>` | Send a message. Options: `{ skipUserAppend?: boolean }` |
| `HermesProxy.showToast(text, isError?)` | `void` | Display the toast banner |
| `HermesProxy.setTheme(name)` | `void` | Set `data-theme` attribute and emit `themeChange` |
| `HermesProxy.registerTheme(name, colors)` | `void` | Emit `themeRegister` |
| `HermesProxy.queueAttachments(files, meta?)` | `Promise<Attachment>[]` | Add files to the attachment queue |
| `HermesProxy.uploadAttachment(file)` | `Promise<Attachment>` | Upload a single file |

## What we deliberately do NOT expose
- Direct `localStorage` keys — plugins should not read/write `hermes-session-id`, `hermes-draft:*`, etc.
- `currentSessionId` closure variable — use `getSessionId()`
- `pendingAttachments` array — use `queueAttachments()`
- `document.getElementById('thread')` — use events (`messageRendered`) or `clearThread()`

## DOM hooks that remain acceptable
The following are **intentionally stable** because they are part of the visual contract:
- `document.getElementById('theme-toggle')` — added by the light-theme plugin itself; safe because it controls the element it creates.
- `querySelector('.session-item.active')` — CSS class contract; unlikely to change.

## Implementation plan (app.js)
1. Add `version: 1` to `HermesProxy` object in `hermes-proxy.js`.
2. Promote existing `setTheme` / `registerTheme` from `hermes-proxy.js` (already there).
3. Add thin wrappers in `app.js` around existing functions:
   ```js
   if (window.HermesProxy) {
     window.HermesProxy.getSessionId = () => currentSessionId;
     window.HermesProxy.getInputValue = () => msgInput.value;
     window.HermesProxy.setInputValue = (text) => { msgInput.value = text; msgInput.dispatchEvent(new Event('input', { bubbles: true })); };
     window.HermesProxy.clearInput = () => { msgInput.value = ''; msgInput.style.height = 'auto'; };
     window.HermesProxy.focusInput = () => msgInput.focus();
     window.HermesProxy.newSession = () => newSessionBtn && newSessionBtn.click();
     window.HermesProxy.clearThread = () => { while (thread.firstChild) thread.removeChild(thread.firstChild); };
     window.HermesProxy.focusSearch = () => { searchInput && searchInput.focus(); };
     window.HermesProxy.sendMessage = sendMessage;
     window.HermesProxy.showToast = _showToast;
     // queueAttachments / uploadAttachment already exposed
   }
   ```
4. Ensure every wrapper is wrapped in `try/catch` if called by plugins, or let the plugin crash be isolated (the core should not break because a plugin mis-used an API).

## Compatibility tests
A new test file `tests/test_plugin_api_surface.py` should verify that each API exists and behaves after the app has rendered. Since the frontend is a single-page JS app, the tests run against the live server via Playwright.

Test checklist:
1. `HermesProxy.version` is a positive integer.
2. `HermesProxy.getSessionId()` returns `null` before any session is created.
3. `HermesProxy.setInputValue('hello')` updates `#msg-input` value and height.
4. `HermesProxy.clearInput()` empties `#msg-input`.
5. `HermesProxy.focusSearch()` focuses `#search-input`.
6. `HermesProxy.clearThread()` removes all `.msg` elements.
7. `HermesProxy.newSession()` creates a new session (DOM or network observable).
8. `HermesProxy.showToast('hi')` makes `#toast` visible.
9. `HermesProxy.queueAttachments` and `uploadAttachment` exist and are functions.
10. Events `sessionChanged`, `messageRendered`, `beforeSend`, `thinkingCreated` fire with expected payloads.
11. **Stability contract**: after renaming an internal ID (e.g. `#msg-input` → `#message-input`), plugins using `HermesProxy.setInputValue()` still work. This is a design assertion, not an automated test.

## Migration of existing plugins
| Plugin | Current hack | New stable API |
|--------|--------------|----------------|
| `draft-autosave` | reads `localStorage.getItem('hermes-session-id')` | `HermesProxy.getSessionId()` |
| `slash-commands` | `document.getElementById('new-session-btn').click()` | `HermesProxy.newSession()` |
| `slash-commands` | clears thread by `while(thread.firstChild)…` | `HermesProxy.clearThread()` |
| `slash-commands` | `document.getElementById('search-input').focus()` | `HermesProxy.focusSearch()` |
| `light-theme` | already uses `HermesProxy.setTheme` | no change |
| `cute-thinking-progress` | already uses `HermesProxy.on` | no change |
| `image-paste-preview` | already uses `HermesProxy.queueAttachments` | no change |
| `file-drop-zone` | already uses `HermesProxy.queueAttachments` | no change |

## Acceptance criteria
- [x] `HermesProxy` object carries `version: 1`
- [x] All APIs in the table above are callable from console / plugins
- [x] No first-party plugin reads `localStorage` keys directly for session state
- [x] No first-party plugin queries hard-coded IDs that are not self-created
- [x] `tests/test_plugin_api_surface.py` passes and covers ≥ 10 assertions
