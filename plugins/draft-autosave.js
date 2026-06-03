// draft-autosave.js — per-session draft persistence
(function () {
  'use strict';
  if (window.__DraftAutosaveInited) return;
  window.__DraftAutosaveInited = true;

  const PLUGIN_NAME = 'draft-autosave';
  const STORAGE_KEY_PREFIX = 'hermes-draft';
  const DELAY = 2000;

  let _timer = null;

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }

  function draftKey(sessionId) {
    return sessionId ? `${STORAGE_KEY_PREFIX}:${sessionId}` : `${STORAGE_KEY_PREFIX}:global`;
  }

  function getCurrentSessionId() {
    if (window.HermesProxy && typeof window.HermesProxy.getSessionId === 'function') {
      return safe(() => window.HermesProxy.getSessionId(), null);
    }
    return safe(() => localStorage.getItem('hermes-session-id'), null);
  }

  function save(sessionId, text) {
    safe(() => {
      if (!text || !text.trim()) {
        localStorage.removeItem(draftKey(sessionId));
        return;
      }
      localStorage.setItem(draftKey(sessionId), text);
    });
  }

  function restore(sessionId) {
    return safe(() => localStorage.getItem(draftKey(sessionId)) || '', '');
  }

  function showIndicator(input) {
    safe(() => {
      let el = document.getElementById('draft-autosave-indicator');
      if (!el) {
        el = document.createElement('span');
        el.id = 'draft-autosave-indicator';
        el.style.cssText = 'opacity:0;transition:opacity .3s;font-size:11px;color:var(--muted);margin-left:8px;';
        input.parentNode.insertBefore(el, input.nextSibling);
      }
      el.textContent = 'Draft saved';
      el.style.opacity = '1';
      setTimeout(() => { el.style.opacity = '0'; }, 1500);
    });
  }

  function init() {
    const msgInput = document.getElementById('msg-input');
    if (!msgInput) return;

    const storedSession = getCurrentSessionId();
    const initialDraft = restore(storedSession);
    if (initialDraft) {
      msgInput.value = initialDraft;
      msgInput.dispatchEvent(new Event('input', { bubbles: true }));
    }

    msgInput.addEventListener('input', () => {
      clearTimeout(_timer);
      const sid = getCurrentSessionId();
      _timer = setTimeout(() => {
        save(sid, msgInput.value);
        showIndicator(msgInput);
      }, DELAY);
    });

    if (window.HermesProxy) {
      HermesProxy.on('sessionChanged', id => {
        const draft = restore(id);
        msgInput.value = draft || '';
        msgInput.dispatchEvent(new Event('input', { bubbles: true }));
      });
      HermesProxy.on('beforeSend', () => {
        const sid = getCurrentSessionId();
        safe(() => localStorage.removeItem(draftKey(sid)));
      });
    }

    console.log(`[${PLUGIN_NAME}] loaded`);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
