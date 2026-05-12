(() => {
  // ── State ──
  let currentSessionId = localStorage.getItem('hermes-session-id') || null;
  let streaming = false;
  let autoScrollLocked = true;
  let _thinkingTimer = null;
  let _thinkingStartedAt = 0;

  const ARTIFACT_LANGS = ['html', 'svg', 'mermaid', 'python', 'json', 'csv'];
  const _artifactStore = new Map();
  let _artifactMsgCounter = 0;

  // ── DOM refs ──
  const loginOverlay = document.getElementById('login-overlay');
  const appEl = document.getElementById('app');
  const pwInput = document.getElementById('pw-input');
  const loginBtn = document.getElementById('login-btn');
  const loginError = document.getElementById('login-error');
  const sessionList = document.getElementById('session-list');
  const thread = document.getElementById('thread');
  const msgInput = document.getElementById('msg-input');
  const sendBtn = document.getElementById('send-btn');
  const newSessionBtn = document.getElementById('new-session-btn');
  const hamburger = document.getElementById('hamburger');
  const sidebar = document.getElementById('sidebar');
  const sidebarOverlay = document.getElementById('sidebar-overlay');
  const sessionLostBanner = document.getElementById('session-lost-banner');
  const sessionLostDismiss = document.getElementById('session-lost-dismiss');
  const logoutBtn = document.getElementById('logout-btn');
  const searchInput = document.getElementById('search-input');
  const attachBtn = document.getElementById('attach-btn');
  const fileInput = document.getElementById('file-input');
  const attachmentPreviews = document.getElementById('attachment-previews');
  const dragOverlay = document.getElementById('drag-overlay');
  const loadMoreBtn = document.getElementById('load-more-btn');

  const pendingAttachments = [];

  // ── Utilities ──
  function closeSidebar() {
    sidebar.classList.remove('open');
    sidebarOverlay.classList.remove('open');
  }
  function openSidebar() {
    sidebar.classList.add('open');
    sidebarOverlay.classList.add('open');
  }
  hamburger.addEventListener('click', openSidebar);
  sidebarOverlay.addEventListener('click', closeSidebar);

  function isThreadNearBottom(threshold = 48) {
    return thread.scrollHeight - thread.scrollTop - thread.clientHeight <= threshold;
  }

  function scrollToBottom() {
    thread.scrollTop = thread.scrollHeight;
    autoScrollLocked = true;
  }

  function maybeScrollToBottom() {
    if (autoScrollLocked) scrollToBottom();
  }

  thread.addEventListener('scroll', () => {
    autoScrollLocked = isThreadNearBottom();
  }, { passive: true });

  function formatDate(ts) {
    if (!ts) return '';
    try {
      // ts can be a Unix float (from DB) or an ISO string
      const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts.includes('T') || ts.includes('Z') ? ts : ts + 'Z');
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    } catch { return ''; }
  }

  function dateGroupLabel(ts) {
    if (!ts) return 'Older';
    try {
      // ts can be a Unix float (from DB) or an ISO string
      const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts.includes('T') || ts.includes('Z') ? ts : ts * 1000);
      const now = new Date();
      const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const yesterday = new Date(today.getTime() - 86400000);
      const weekAgo = new Date(today.getTime() - 6 * 86400000);
      const sessionDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
      if (sessionDay.getTime() === today.getTime()) return 'Today';
      if (sessionDay.getTime() === yesterday.getTime()) return 'Yesterday';
      if (sessionDay >= weekAgo) return 'This Week';
      return 'Older';
    } catch { return 'Older'; }
  }

  // Auto-resize textarea
  msgInput.addEventListener('input', () => {
    // Capture scroll state before layout changes
    const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 10;
    msgInput.style.height = 'auto';
    msgInput.style.height = Math.min(msgInput.scrollHeight, 200) + 'px';
    // Restore bottom-pinned position after reflow
    if (atBottom) thread.scrollTop = thread.scrollHeight;
  });

  // Enter = send, Shift+Enter = newline
  msgInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!streaming) sendMessage();
    }
  });

  // ── Login ──
  async function checkAuth() {
    const res = await fetch('/auth/status');
    const data = await res.json();
    if (data.authenticated) {
      showApp();
    } else {
      loginOverlay.classList.remove('hidden');
    }
  }

  async function doLogin() {
    loginError.textContent = '';
    const pw = pwInput.value;
    const res = await fetch('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });
    if (res.ok) {
      loginOverlay.classList.add('hidden');
      // Fresh login always starts with a clean session slate.
      // Prevents the session-lost banner from firing when the server was
      // restarted (in-memory mapping cleared) but localStorage still holds
      // a stale session ID from a prior run.
      currentSessionId = null;
      localStorage.removeItem('hermes-session-id');
      // Reset any iOS zoom that may have been triggered by the password field
      const mv = document.querySelector('meta[name=viewport]');
      if (mv) {
        mv.content = 'width=device-width, initial-scale=1.0, viewport-fit=cover';
      }
      showApp();
    } else if (res.status === 401) {
      loginError.textContent = 'Wrong password';
    } else if (res.status === 429) {
      loginError.textContent = 'Too many attempts — wait 60 s';
    } else {
      loginError.textContent = 'Login failed';
    }
  }

  loginBtn.addEventListener('click', doLogin);
  pwInput.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

  function showApp() {
    loginOverlay.classList.add('hidden');
    appEl.classList.remove('hidden');
    loadSessions();
    validateSession();
  }

  async function validateSession() {
    // Only meaningful if the client thinks it has an active session.
    // If currentSessionId is null we're already in a clean state.
    if (!currentSessionId) return;
    try {
      const res = await fetch('/api/session/validate');
      if (!res.ok) return;
      const data = await res.json();
      if (!data.valid) {
        sessionLostBanner.classList.add('visible');
      }
    } catch {}
  }

  function dismissSessionLostBanner() {
    sessionLostBanner.classList.remove('visible');
  }

  sessionLostDismiss.addEventListener('click', dismissSessionLostBanner);

  logoutBtn.addEventListener('click', async () => {
    await fetch('/auth/logout', { method: 'POST' });
    localStorage.removeItem('hermes-session-id');
    location.reload();
  });

  // ── Sessions ──
  let sessionsTotalCount = 0;
  let sessionsLoadedCount = 0;
  let sessionsOffset = 0;

  async function loadSessions(append = false) {
    try {
      if (!append) sessionsOffset = 0;
      const url = '/api/sessions?offset=' + sessionsOffset + '&limit=30';
      const res = await fetch(url);
      if (!res.ok) return;
      const sessions = await res.json();
      sessionsTotalCount = parseInt(res.headers.get('X-Total-Count') || '0', 10);
      sessionsLoadedCount = sessions.length;
      // Don't overwrite active search results
      if (!searchInput.value.trim()) {
        if (append) {
          appendToSessionList(sessions);
        } else {
          renderSessions(sessions);
        }
      }
      // Always update the active highlight (works on whatever is currently in the list)
      updateActiveSession();
      updateLoadMoreButton();
    } catch {}
  }

  async function _renameSession(id, titleEl) {
    const currentName = titleEl.textContent;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'session-title-input';
    input.value = currentName;

    // Prevent taps on the input from bubbling to the session-item click listener
    input.addEventListener('click', e => e.stopPropagation());
    input.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });

    titleEl.replaceWith(input);
    input.focus();
    input.select();

    let committed = false;

    async function commit() {
      if (committed) return;
      committed = true;
      const newName = input.value.trim();
      if (newName && newName !== currentName) {
        try {
          await fetch(`/api/sessions/${encodeURIComponent(id)}/rename`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: newName }),
          });
          titleEl.textContent = newName;
        } catch {
          titleEl.textContent = currentName;
        }
      } else {
        titleEl.textContent = currentName;
      }
      input.replaceWith(titleEl);
    }

    function cancel() {
      if (committed) return;
      committed = true;
      titleEl.textContent = currentName;
      input.replaceWith(titleEl);
    }

    input.addEventListener('blur', commit);
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
    });
  }

  async function _archiveSession(sessionId, el) {
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/archive`, { method: 'PUT' });
      if (res.ok) {
        el.style.transition = 'opacity .2s';
        el.style.opacity = '0';
        setTimeout(() => {
          el.remove();
          _cleanupEmptyGroupHeaders();
          if (currentSessionId === sessionId) {
            currentSessionId = null;
            thread.innerHTML = '';
          }
          updateLoadMoreButton();
        }, 200);
      } else {
        _showToast('Archive failed', true);
      }
    } catch { _showToast('Archive failed', true); }
  }

  async function _deleteSession(sessionId, el) {
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
      if (res.ok) {
        el.style.transition = 'opacity .2s';
        el.style.opacity = '0';
        setTimeout(() => {
          el.remove();
          _cleanupEmptyGroupHeaders();
          if (currentSessionId === sessionId) {
            currentSessionId = null;
            thread.innerHTML = '';
          }
          updateLoadMoreButton();
        }, 200);
      } else if (res.status === 404) {
        _showToast('Session not found', true);
      } else {
        _showToast('Delete failed', true);
      }
    } catch { _showToast('Delete failed', true); }
  }

  function _addSessionActions(el, sessionId) {
    const actions = document.createElement('div');
    actions.className = 'session-actions';
    const menuBtn = document.createElement('button');
    menuBtn.className = 'session-action-btn';
    menuBtn.textContent = '⋮';
    menuBtn.title = 'More actions';
    menuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      // Toggle: if actions are already expanded, collapse them
      if (actions.dataset.expanded === '1') {
        actions.innerHTML = '';
        actions.appendChild(menuBtn);
        delete actions.dataset.expanded;
        return;
      }
      actions.dataset.expanded = '1';
      actions.innerHTML = '';
      const arBtn = document.createElement('button');
      arBtn.className = 'session-action-btn';
      arBtn.textContent = '📦';
      arBtn.title = 'Archive';
      arBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        _archiveSession(sessionId, el);
      });
      const delBtn = document.createElement('button');
      delBtn.className = 'session-action-btn danger';
      delBtn.textContent = '🗑';
      delBtn.title = 'Delete';
      let confirmTimer = null;
      delBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (delBtn.dataset.confirmed === '1') {
          // Confirmed — actually delete
          clearTimeout(confirmTimer);
          _deleteSession(sessionId, el);
          return;
        }
        // First click — ask for confirmation
        delBtn.textContent = '✓';
        delBtn.title = 'Confirm delete';
        delBtn.dataset.confirmed = '1';
        confirmTimer = setTimeout(() => {
          delBtn.textContent = '🗑';
          delBtn.title = 'Delete';
          delete delBtn.dataset.confirmed;
        }, 3000);
      });
      actions.appendChild(arBtn);
      actions.appendChild(delBtn);
      actions.appendChild(menuBtn);
      // Collapse menu when clicking elsewhere (deferred to avoid same-event capture)
      setTimeout(() => {
        const onOutside = (evt) => {
          if (!actions.contains(evt.target)) {
            actions.innerHTML = '';
            actions.appendChild(menuBtn);
            delete actions.dataset.expanded;
          }
          document.removeEventListener('click', onOutside, true);
        };
        document.addEventListener('click', onOutside, true);
      }, 0);
    });
    actions.appendChild(menuBtn);
    el.appendChild(actions);
  }

  function _cleanupEmptyGroupHeaders() {
    const headers = sessionList.querySelectorAll('.session-group-header');
    for (const h of headers) {
      const next = h.nextElementSibling;
      if (!next || (next.classList.contains('session-group-header') || next.id === 'load-more-btn')) {
        h.remove();
      }
    }
  }

  function renderSessions(sessions, searchMode = false) {
    sessionList.innerHTML = '';
    if (sessions.length === 0 && !searchMode) {
      const empty = document.createElement('div');
      empty.className = 'session-empty';
      empty.textContent = 'No conversations yet';
      sessionList.appendChild(empty);
      return;
    }
    if (sessions.length === 0 && searchMode) {
      const empty = document.createElement('div');
      empty.className = 'session-empty';
      empty.textContent = 'No results';
      sessionList.appendChild(empty);
      return;
    }
    let lastGroup = '';
    for (const s of sessions) {
      if (!searchMode) {
        const group = dateGroupLabel(s.started_at);
        if (group !== lastGroup) {
          const header = document.createElement('div');
          header.className = 'session-group-header';
          header.textContent = group;
          sessionList.appendChild(header);
          lastGroup = group;
        }
      }
      const el = document.createElement('div');
      el.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
      el.dataset.id = s.id;
      const title = s.title || s.id.slice(0, 16) + '…';
      el.title = title;
      el.innerHTML = `<div class="session-title">${esc(title)}</div>
        <div class="session-date">${esc(formatDate(s.started_at))}</div>`;
      if (searchMode && s.match_snippet) {
        el.innerHTML += `<div class="session-snippet">${esc(s.match_snippet)}</div>`;
      }
      el.addEventListener('click', () => {
        const anchor = searchMode ? (s.match_offset || null) : null;
        loadSession(s.id, anchor);
        if (searchMode) {
          searchInput.value = '';
          loadSessions();
        }
      });
      const titleEl = el.querySelector('.session-title');
      titleEl.addEventListener('dblclick', e => {
        e.stopPropagation();
        _renameSession(s.id, titleEl);
      });
      // Mobile: single tap on title of already-active session triggers rename.
      // Guard: skip if titleEl is already replaced by the rename input (dblclick fires
      // two click events on desktop -- the second would re-enter _renameSession).
      titleEl.addEventListener('click', e => {
        if (currentSessionId === s.id && titleEl.isConnected) {
          e.stopPropagation();
          _renameSession(s.id, titleEl);
        }
      });
      _addSessionActions(el, s.id);
      sessionList.appendChild(el);
    }
    sessionList.scrollTop = 0;
    // Emit plugin hook after DOM is fully populated
    if (window.HermesProxy) {
      try {
        window.HermesProxy.emit('sessionListRendered', sessionList);
      } catch (e) {
        console.error('Plugin error in sessionListRendered:', e);
      }
    }
  }

  function appendToSessionList(sessions) {
    // Determine last date group already in the list
    const headers = sessionList.querySelectorAll('.session-group-header');
    let lastGroup = headers.length ? headers[headers.length - 1].textContent : '';
    for (const s of sessions) {
      const group = dateGroupLabel(s.started_at);
      if (group !== lastGroup) {
        const header = document.createElement('div');
        header.className = 'session-group-header';
        header.textContent = group;
        sessionList.appendChild(header);
        lastGroup = group;
      }
      const el = document.createElement('div');
      el.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
      el.dataset.id = s.id;
      const title = s.title || s.id.slice(0, 16) + '…';
      el.title = title;
      el.innerHTML = `<div class="session-title">${esc(title)}</div>
        <div class="session-date">${esc(formatDate(s.started_at))}</div>`;
      el.addEventListener('click', () => { loadSession(s.id); });
      const titleEl = el.querySelector('.session-title');
      titleEl.addEventListener('dblclick', e => {
        e.stopPropagation();
        _renameSession(s.id, titleEl);
      });
      titleEl.addEventListener('click', e => {
        if (currentSessionId === s.id && titleEl.isConnected) {
          e.stopPropagation();
          _renameSession(s.id, titleEl);
        }
      });
      _addSessionActions(el, s.id);
      sessionList.appendChild(el);
    }
    if (window.HermesProxy) {
      try {
        window.HermesProxy.emit('sessionListRendered', sessionList);
      } catch (e) {
        console.error('Plugin error in sessionListRendered:', e);
      }
    }
  }

  function updateLoadMoreButton() {
    const loaded = sessionList.querySelectorAll('.session-item').length;
    const remaining = sessionsTotalCount - loaded;
    if (remaining > 0) {
      loadMoreBtn.style.display = 'block';
      loadMoreBtn.textContent = `Load more (${remaining} older)`;
    } else {
      loadMoreBtn.style.display = 'none';
    }
  }

  loadMoreBtn.addEventListener('click', () => {
    sessionsOffset = sessionList.querySelectorAll('.session-item').length;
    loadSessions(true);
  });

  function _addOptimisticSession(firstMsg) {
    // Remove any existing optimistic entry first
    const existing = document.getElementById('optimistic-session');
    if (existing) existing.remove();

    const el = document.createElement('div');
    el.id = 'optimistic-session';
    el.className = 'session-item active';
    el.innerHTML = `<div class="session-title">${esc(firstMsg.slice(0, 72))}</div>
      <div class="session-date">Just now</div>`;
    sessionList.prepend(el);
    sessionList.scrollTop = 0;
  }

  // ── Search ──
  let _searchTimer = null;
  function _onSearchInput() {
    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(async () => {
      const q = searchInput.value.trim();
      if (!q) {
        loadSessions();
        return;
      }
      try {
        const res = await fetch(`/api/sessions/search?q=${encodeURIComponent(q)}`);
        if (!res.ok) return;
        const sessions = await res.json();
        renderSessions(sessions, true);
      } catch {}
    }, 300);
  }
  searchInput.addEventListener('input', _onSearchInput);
  searchInput.addEventListener('search', _onSearchInput);

  async function loadSession(id, anchorTs = null) {
    currentSessionId = id;
    // Emit plugin hook when switching sessions
    if (window.HermesProxy) {
      try {
        window.HermesProxy.emit('sessionChanged', id);
      } catch (e) {
        console.error('Plugin error in sessionChanged:', e);
      }
    }
    dismissSessionLostBanner();
    closeSidebar();
    updateActiveSession();
    // Reset any iOS zoom that may have been triggered by the search input
    const mv = document.querySelector('meta[name=viewport]');
    if (mv) mv.content = 'width=device-width, initial-scale=1.0, viewport-fit=cover';
    thread.innerHTML = '';
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages`);
      if (!res.ok) return;
      const messages = await res.json();
      for (const m of messages) {
        appendMessage(m.role, m.content, m.timestamp);
      }
      scrollToBottom();
      if (anchorTs) {
        const target = thread.querySelector(`[data-ts-raw="${anchorTs}"]`);
        if (target) {
          target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      }
    } catch {}
  }

  function updateActiveSession() {
    document.querySelectorAll('.session-item').forEach(el => {
      el.classList.toggle('active', el.dataset.id === currentSessionId);
    });
  }

  newSessionBtn.addEventListener('click', () => {
    currentSessionId = null;
    localStorage.removeItem('hermes-session-id');
    dismissSessionLostBanner();
    thread.innerHTML = '';
    updateActiveSession();
    closeSidebar();
    msgInput.focus();
  });

  // ── Message rendering ──
  function _enhanceCodeBlocks(bubble) {
    bubble.querySelectorAll('pre').forEach(pre => {
      pre.style.position = 'relative';
      const code = pre.querySelector('code');
      if (code && window.hljs && !code.dataset.highlighted) {
        try { window.hljs.highlightElement(code); } catch {}
      }
      if (code && !pre.querySelector('.code-lang-label')) {
        const cls = Array.from(code.classList).find(c => c.startsWith('language-'));
        const lang = cls ? cls.replace('language-', '') : '';
        if (lang) {
          const label = document.createElement('span');
          label.className = 'code-lang-label';
          label.textContent = lang;
          pre.appendChild(label);
        }
      }
      if (!pre.querySelector('.copy-btn')) {
        const btn = document.createElement('button');
        btn.className = 'copy-btn';
        btn.textContent = 'Copy';
        btn.addEventListener('click', async () => {
          const text = code ? code.innerText : pre.innerText;
          try {
            await navigator.clipboard.writeText(text);
            btn.textContent = 'Copied!';
            btn.classList.add('copied');
            setTimeout(() => {
              btn.textContent = 'Copy';
              btn.classList.remove('copied');
            }, 2000);
          } catch {
            btn.textContent = 'Error';
            setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
          }
        });
        pre.appendChild(btn);
      }
    });
  }

  function _attachCopyButtons(bubble) {
    _enhanceCodeBlocks(bubble);
  }

  /* ── Artifact helpers ── */

  function _scanArtifacts(text, msgId) {
    const regex = /```\s*(html|svg|mermaid|python|json|csv)(?:[^\n]*)\n([\s\S]*?)```/g;
    let idx = 0;
    for (const match of text.matchAll(regex)) {
      const key = `${msgId}_${idx}`;
      const lang = match[1];
      const code = match[2];
      if (!_artifactStore.has(key)) {
        _artifactStore.set(key, { lang, code });
        if (window.HermesProxy) {
          try {
            window.HermesProxy.emit('artifactDetected', { messageId: msgId, index: idx, lang, code });
          } catch (e) {
            console.error('Plugin error in artifactDetected:', e);
          }
        }
      } else {
        // Update code in place as streaming fills out the block. Re-emit so
        // buttons can be restored after markdown re-renders replace <pre> nodes.
        _artifactStore.get(key).code = code;
        if (window.HermesProxy) {
          try {
            window.HermesProxy.emit('artifactDetected', { messageId: msgId, index: idx, lang, code });
          } catch (e) {
            console.error('Plugin error in artifactDetected:', e);
          }
        }
      }
      idx++;
    }
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatTime(ts) {
    if (!ts) return '';
    try {
      // ts can be a Unix float (from DB) or ms timestamp (from Date.now())
      const ms = ts > 1e11 ? ts : ts * 1000;
      const d = new Date(ms);
      return d.toLocaleString(undefined, {
        month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit',
      });
    } catch { return ''; }
  }

  function appendMessage(role, content, ts = null) {
    // Increment counter for each message — used as the message ID for artifact keys
    const msgId = ++_artifactMsgCounter;

    const msg = document.createElement('div');
    msg.className = `msg ${role}`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.dataset.rawContent = content || '';
    if (role === 'assistant') {
      bubble.innerHTML = content ? DOMPurify.sanitize(marked.parse(content)) : '';
      _enhanceCodeBlocks(bubble);
      // Scan for artifact code blocks after rendering
      if (content) {
        _scanArtifacts(content, msgId);
      }
    } else {
      bubble.textContent = content || '';
    }
    if (ts) {
      bubble.dataset.ts = formatTime(ts);
      bubble.dataset.tsRaw = ts;  // raw value for anchor lookup
    }
    msg.appendChild(bubble);
    thread.appendChild(msg);

    // Build a stable ref-id from the counter so plugins can locate the bubble
    msg.dataset.msgRef = String(msgId);
    bubble.dataset.msgRef = String(msgId);

    _attachMsgActions(msg, bubble, role, content || '');
    if (window.HermesProxy) window.HermesProxy.emit('messageRendered', bubble, { role, content, ts });
    scrollToBottom();
    return bubble;
  }

  function _showToast(text, isError = false) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = text;
    toast.classList.toggle('error', isError);
    toast.classList.add('visible');
    setTimeout(() => toast.classList.remove('visible'), 2500);
  }

  function _copyMessage(bubble) {
    const text = bubble.innerText;
    navigator.clipboard.writeText(text).then(() => {
      _showToast('Copied to clipboard');
    }).catch(() => {
      _showToast('Copy failed', true);
    });
  }

  function _regenerateMessage(msgEl) {
    const threadEl = document.getElementById('thread');
    const msgs = Array.from(threadEl.querySelectorAll('.msg'));
    const idx = msgs.indexOf(msgEl);
    if (idx <= 0) return;
    let prevUserText = '';
    for (let i = idx - 1; i >= 0; i--) {
      if (msgs[i].classList.contains('user')) {
        const bubble = msgs[i].querySelector('.bubble');
        if (bubble) {
          prevUserText = bubble.dataset.rawContent || bubble.innerText;
        }
        break;
      }
    }
    if (!prevUserText) return;
    for (let i = msgs.length - 1; i >= idx; i--) {
      msgs[i].remove();
    }
    sendMessage({ text: prevUserText, skipUserAppend: true });
  }

  function _startEdit(msgEl, bubble, content) {
    if (bubble.querySelector('.edit-textarea')) return;
    const originalContent = content || '';
    const ta = document.createElement('textarea');
    ta.className = 'edit-textarea';
    ta.value = originalContent;
    ta.rows = Math.min(10, Math.max(2, originalContent.split('\\n').length));

    const submit = () => {
      const newText = ta.value.trim();
      if (!newText) {
        ta.remove();
        return;
      }
      bubble.textContent = newText;
      bubble.dataset.rawContent = newText;
      _attachMsgActions(msgEl, bubble, 'user', newText);
      const threadEl = document.getElementById('thread');
      const msgs = Array.from(threadEl.querySelectorAll('.msg'));
      const idx = msgs.indexOf(msgEl);
      for (let i = msgs.length - 1; i > idx; i--) {
        msgs[i].remove();
      }
      sendMessage({ text: newText, skipUserAppend: true });
    };

    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submit();
      }
      if (e.key === 'Escape') {
        ta.remove();
      }
    });
    ta.addEventListener('blur', submit);

    bubble.innerHTML = '';
    bubble.appendChild(ta);
    ta.focus();
  }

  function _attachMsgActions(msg, bubble, role, content) {
    if (msg.querySelector('.msg-actions')) return;
    const actions = document.createElement('div');
    actions.className = 'msg-actions';

    if (role === 'user') {
      const editBtn = document.createElement('button');
      editBtn.className = 'msg-action-btn';
      editBtn.textContent = 'Edit';
      editBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _startEdit(msg, bubble, content);
      });
      actions.appendChild(editBtn);
    } else {
      const copyBtn = document.createElement('button');
      copyBtn.className = 'msg-action-btn';
      copyBtn.textContent = 'Copy';
      copyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _copyMessage(bubble);
      });
      actions.appendChild(copyBtn);

      const regenBtn = document.createElement('button');
      regenBtn.className = 'msg-action-btn';
      regenBtn.textContent = 'Regenerate';
      regenBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        _regenerateMessage(msg);
      });
      actions.appendChild(regenBtn);
    }

    msg.appendChild(actions);
  }

  // Global click-away to dismiss visible action menus on mobile
  document.addEventListener('click', (e) => {
    const visible = document.querySelector('.msg.actions-visible');
    if (visible && !visible.contains(e.target)) {
      visible.classList.remove('actions-visible');
    }
  });

  // Mobile long-press to show actions
  let _lpTimer = null;
  thread.addEventListener('touchstart', (e) => {
    const msg = e.target.closest('.msg');
    if (!msg || msg.id === 'thinking-indicator') return;
    _lpTimer = setTimeout(() => {
      document.querySelectorAll('.msg.actions-visible').forEach(m => m.classList.remove('actions-visible'));
      msg.classList.add('actions-visible');
    }, 500);
  }, { passive: true });
  thread.addEventListener('touchend', () => {
    if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
  }, { passive: true });
  thread.addEventListener('touchmove', () => {
    if (_lpTimer) { clearTimeout(_lpTimer); _lpTimer = null; }
  }, { passive: true });

  function emitThinking(event, payload) {
    try {
      window.HermesProxy && window.HermesProxy.emit(event, payload);
    } catch (e) {
      console.error('Plugin error in', event, ':', e);
    }
  }

  function cleanThinkingLabel(label) {
    return label ? String(label).split(' ')[0].replace(/[_/].*$/, '') : 'Thinking';
  }

  function showThinking(label = null) {
    const existing = document.getElementById('thinking-indicator');
    if (existing) removeThinking();

    _thinkingStartedAt = Date.now();
    const msg = document.createElement('div');
    msg.id = 'thinking-indicator';
    msg.className = 'msg assistant';

    const bubble = document.createElement('div');
    bubble.className = 'bubble thinking-bubble';
    bubble.id = 'thinking-content';

    const pulse = document.createElement('span');
    pulse.className = 'thinking-pulse';

    const text = document.createElement('span');
    text.className = 'thinking-text';
    text.textContent = cleanThinkingLabel(label);

    const elapsed = document.createElement('span');
    elapsed.className = 'thinking-elapsed';
    elapsed.id = 'thinking-elapsed';
    elapsed.textContent = '0s';

    bubble.appendChild(pulse);
    bubble.appendChild(text);
    bubble.appendChild(elapsed);
    msg.appendChild(bubble);
    thread.appendChild(msg);
    scrollToBottom();

    _thinkingTimer = setInterval(() => {
      const elTime = document.getElementById('thinking-elapsed');
      if (!elTime) return;
      const sec = Math.round((Date.now() - _thinkingStartedAt) / 1000);
      elTime.textContent = sec + 's';
    }, 1000);

    emitThinking('thinkingCreated', {
      el: msg,
      bubbleEl: bubble,
      startedAt: _thinkingStartedAt,
      label: text.textContent,
    });
    return msg;
  }

  function updateThinking(label, raw = null) {
    const el = document.getElementById('thinking-indicator');
    if (!el) return;
    const bubbleEl = document.getElementById('thinking-content');
    const textEl = document.querySelector('#thinking-content .thinking-text, #thinking-indicator .thinking-text');
    const nextLabel = cleanThinkingLabel(label);
    if (textEl) textEl.textContent = nextLabel;
    emitThinking('thinkingUpdated', {
      el,
      bubbleEl,
      label: nextLabel,
      tool: raw && raw.tool ? raw.tool : label,
      raw,
    });
  }

  function removeThinking() {
    const el = document.getElementById('thinking-indicator');
    if (_thinkingTimer) { clearInterval(_thinkingTimer); _thinkingTimer = null; }
    if (!el) return;
    const bubbleEl = document.getElementById('thinking-content');
    const elapsedMs = _thinkingStartedAt ? Date.now() - _thinkingStartedAt : 0;
    emitThinking('thinkingRemoved', { el, bubbleEl, elapsedMs });
    el.remove();
    _thinkingStartedAt = 0;
  }

  // ── Attachments ──
  function _displayAttachmentName(filename) {
    const name = filename || 'image';
    const dot = name.lastIndexOf('.');
    return dot > 0 ? name.slice(0, dot) : name;
  }

  function _middleEllipsis(name, max = 28) {
    if (!name || name.length <= max) return name || 'image';
    const front = Math.ceil((max - 1) * 0.66);
    const back = Math.max(3, max - 1 - front);
    return `${name.slice(0, front)}…${name.slice(-back)}`;
  }

  function _renderAttachmentPreviews() {
    attachmentPreviews.innerHTML = '';
    pendingAttachments.forEach((att, idx) => {
      const item = document.createElement('div');
      item.className = 'attachment-row';
      item.title = att.error ? `${att.file.name} · ${att.error}` : att.file.name;

      const icon = document.createElement('span');
      icon.className = 'attachment-icon';
      icon.setAttribute('aria-hidden', 'true');
      icon.textContent = '📎';
      item.appendChild(icon);

      const name = document.createElement('span');
      name.className = 'attachment-name';
      name.textContent = _middleEllipsis(_displayAttachmentName(att.file.name));
      item.appendChild(name);

      const remove = document.createElement('button');
      remove.className = 'attachment-remove';
      remove.type = 'button';
      remove.textContent = '×';
      remove.title = 'Remove attachment';
      remove.setAttribute('aria-label', `Remove ${att.file.name}`);
      remove.addEventListener('click', () => {
        const [removed] = pendingAttachments.splice(idx, 1);
        if (removed && removed.previewUrl) URL.revokeObjectURL(removed.previewUrl);
        _renderAttachmentPreviews();
      });
      item.appendChild(remove);

      attachmentPreviews.appendChild(item);
    });
  }

  async function _uploadAttachment(att) {
    const form = new FormData();
    form.append('file', att.file);
    try {
      att.progress = 35;
      _renderAttachmentPreviews();
      const res = await fetch('/api/attachments', { method: 'POST', body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || 'Upload failed');
      att.url = data.url;
      att.markdown = data.markdown;
      att.uploaded = true;
      att.progress = 100;
    } catch (err) {
      att.error = err.message || 'Upload failed';
    } finally {
      _renderAttachmentPreviews();
    }
  }

  function _queueFiles(files) {
    const images = Array.from(files || []).filter(f => f.type && f.type.startsWith('image/'));
    for (const file of images) {
      const att = { file, previewUrl: URL.createObjectURL(file), uploaded: false, progress: 0, error: '' };
      pendingAttachments.push(att);
      _uploadAttachment(att);
    }
    _renderAttachmentPreviews();
  }

  if (attachBtn && fileInput) {
    attachBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      _queueFiles(fileInput.files);
      fileInput.value = '';
    });
  }

  document.addEventListener('dragover', (e) => {
    if (!Array.from(e.dataTransfer?.items || []).some(i => i.kind === 'file')) return;
    e.preventDefault();
    if (dragOverlay) dragOverlay.classList.add('active');
  });
  document.addEventListener('dragleave', (e) => {
    if (e.clientX === 0 || e.clientY === 0 || e.clientX >= window.innerWidth || e.clientY >= window.innerHeight) {
      if (dragOverlay) dragOverlay.classList.remove('active');
    }
  });
  document.addEventListener('drop', (e) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    if (dragOverlay) dragOverlay.classList.remove('active');
    _queueFiles(e.dataTransfer.files);
  });

  msgInput.addEventListener('paste', (e) => {
    const files = Array.from(e.clipboardData?.files || []).filter(f => f.type && f.type.startsWith('image/'));
    if (files.length) _queueFiles(files);
  });

  // ── Send ──
  sendBtn.addEventListener('click', sendMessage);

  async function sendMessage(options = {}) {
    const explicitText = typeof options === 'string' ? options : options.text;
    const skipUserAppend = Boolean(options && options.skipUserAppend);
    const text = (explicitText !== undefined ? explicitText : msgInput.value).trim();
    const readyAttachments = pendingAttachments.filter(a => a.uploaded && !a.error).map(a => ({ url: a.url, markdown: a.markdown }));
    const uploading = pendingAttachments.some(a => !a.uploaded && !a.error);
    if (streaming || (!text && readyAttachments.length === 0)) return;
    if (uploading) {
      _showToast('Still uploading images…', true);
      return;
    }
    const errored = pendingAttachments.filter(a => a.error);
    if (errored.length && readyAttachments.length === 0 && !text) {
      _showToast('Remove failed uploads before sending', true);
      return;
    }

    try {
      window.HermesProxy && window.HermesProxy.emit('beforeSend', text);
    } catch (e) {
      console.error('Plugin error in beforeSend:', e);
    }

    streaming = true;
    sendBtn.disabled = true;
    msgInput.value = '';
    msgInput.style.height = 'auto';
    dismissSessionLostBanner();

    const displayText = text || '(image attachment)';
    if (!currentSessionId && !searchInput.value.trim()) {
      _addOptimisticSession(displayText);
    }
    autoScrollLocked = true;
    if (!skipUserAppend) {
      const attachmentText = readyAttachments.map(a => a.markdown).join('\n');
      appendMessage('user', attachmentText ? `${attachmentText}\n\n${text}`.trim() : text, Date.now());
    }

    // Clear successfully queued attachments after they are included in the request.
    for (const att of pendingAttachments.splice(0)) {
      if (att.previewUrl) URL.revokeObjectURL(att.previewUrl);
    }
    _renderAttachmentPreviews();
    showThinking();

    let assistantBubble = null;
    let assistantContent = '';
    let streamedSessionId = null;

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: currentSessionId, attachments: readyAttachments }),
      });

      if (!res.ok) {
        removeThinking();
        appendMessage('assistant', '_(Error contacting server)_');
        return;
      }

      const newSessionId = res.headers.get('X-Hermes-Session-Id');
      if (newSessionId) {
        const wasNew = !currentSessionId;
        currentSessionId = newSessionId;
        localStorage.setItem('hermes-session-id', currentSessionId);
        if (wasNew) await loadSessions();
        updateActiveSession();
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      const processEvent = async (eventType, dataLine) => {
        if (!dataLine || dataLine === '[DONE]') return;
        let json;
        try { json = JSON.parse(dataLine); } catch { return; }
        if (eventType === 'session' && json.hermes_session_id) {
          streamedSessionId = json.hermes_session_id;
          currentSessionId = json.hermes_session_id;
          localStorage.setItem('hermes-session-id', currentSessionId);
          await loadSessions();
          updateActiveSession();
          return;
        }
        if (eventType === 'hermes.tool.progress' && json.tool) {
          updateThinking(json.label || json.tool, json);
          return;
        }
        const delta = json?.choices?.[0]?.delta?.content;
        if (!delta) return;
        if (!assistantBubble) {
          removeThinking();
          const msg = document.createElement('div');
          msg.className = 'msg assistant';
          const msgId = ++_artifactMsgCounter;
          msg.dataset.msgRef = String(msgId);
          assistantBubble = document.createElement('div');
          assistantBubble.className = 'bubble';
          assistantBubble.dataset.ts = formatTime(Date.now());
          assistantBubble.dataset.msgRef = String(msgId);
          assistantBubble.dataset.rawContent = '';
          msg.appendChild(assistantBubble);
          thread.appendChild(msg);
        }
        assistantContent += delta;
        assistantBubble.dataset.rawContent = assistantContent;
        assistantBubble.innerHTML = DOMPurify.sanitize(marked.parse(assistantContent));
        _enhanceCodeBlocks(assistantBubble);
        _scanArtifacts(assistantContent, Number(assistantBubble.dataset.msgRef));
        maybeScrollToBottom();
      };

      const drainBuffer = async (final = false) => {
        const parts = buffer.split('\n\n');
        buffer = final ? '' : parts.pop();
        for (const part of final ? parts.filter(Boolean) : parts) {
          const lines = part.split('\n');
          let eventType = 'message';
          const dataLines = [];
          for (const line of lines) {
            if (line.startsWith('event:')) eventType = line.slice(6).trim();
            else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
          }
          await processEvent(eventType, dataLines.join('\n'));
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        await drainBuffer(false);
      }
      if (buffer) await drainBuffer(true);

      // Final render (ensure complete markdown and restore buttons after stream re-renders)
      if (assistantBubble && assistantContent) {
        assistantBubble.dataset.rawContent = assistantContent;
        assistantBubble.innerHTML = DOMPurify.sanitize(marked.parse(assistantContent));
        _enhanceCodeBlocks(assistantBubble);
        _scanArtifacts(assistantContent, Number(assistantBubble.dataset.msgRef));
        _attachMsgActions(assistantBubble.parentElement, assistantBubble, 'assistant', assistantContent);
        if (window.HermesProxy) window.HermesProxy.emit('messageRendered', assistantBubble, { role: 'assistant', content: assistantContent, ts: Date.now() });
      }

      if (newSessionId || streamedSessionId) await loadSessions();

    } catch (err) {
      removeThinking();
      if (!assistantBubble) {
        appendMessage('assistant', '_(Stream error)_');
      }
    } finally {
      removeThinking();
      streaming = false;
      sendBtn.disabled = false;
      msgInput.focus();
    }
  }

  // ── Boot ──
  checkAuth();

  /* ── Artifact panel ── */
  (function () {
    const panel = document.getElementById('artifact-panel');
    const titleEl = document.getElementById('artifact-panel-title');
    const contentEl = document.getElementById('artifact-panel-content');
    if (!panel || !contentEl) return;

    let currentCode = '';
    let currentLang = '';

    // Hook close button
    const closeBtn = document.getElementById('artifact-close');
    if (closeBtn) closeBtn.addEventListener('click', closePanel);

    // Hook copy button
    const copyBtn = document.getElementById('artifact-copy');
    if (copyBtn) {
      copyBtn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(currentCode);
          copyBtn.textContent = 'Copied!';
          setTimeout(() => (copyBtn.textContent = 'Copy'), 1500);
        } catch {
          copyBtn.textContent = 'Failed';
          setTimeout(() => (copyBtn.textContent = 'Copy'), 1500);
        }
      });
    }

    // Hook download button
    const dlBtn = document.getElementById('artifact-download');
    if (dlBtn) {
      dlBtn.addEventListener('click', () => {
        const map = { html: 'html', svg: 'svg', mermaid: 'mmd', python: 'py', json: 'json', csv: 'csv' };
        const ext = map[currentLang] || 'txt';
        const blob = new Blob([currentCode], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `artifact.${ext}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      });
    }

    // Hook run button (Python only)
    const runBtn = document.getElementById('artifact-run');
    if (runBtn) {
      runBtn.addEventListener('click', async () => {
        if (!currentCode || currentLang !== 'python') return;
        runBtn.disabled = true;
        runBtn.textContent = 'Running…';
        try {
          const res = await fetch('/api/run-python', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: currentCode }),
          });
          const data = await res.json();
          if (data.error) {
            setContent(`
              <div class="artifact-code-wrap">
                <pre style="color:#ff4466"><code>${esc(data.error)}</code></pre>
              </div>
            `, currentCode, currentLang);
          } else {
            setContent(`
              <div class="artifact-code-wrap">
                <pre><code>${esc(data.output || '(no output)')}</code></pre>
              </div>
            `, currentCode, currentLang);
          }
        } catch (e) {
          setContent(`
            <div class="artifact-code-wrap">
              <pre style="color:#ff4466"><code>Run failed: ${esc(e.message)}</code></pre>
            </div>
          `, currentCode, currentLang);
        } finally {
          runBtn.disabled = false;
          runBtn.textContent = 'Run';
        }
      });
    }

    function closePanel() {
      panel.classList.remove('open');
    }

    function openPanel() {
      panel.classList.add('open');
    }

    // Toggle Run button visibility based on language
    function updateRunButton(lang) {
      if (runBtn) runBtn.style.display = lang === 'python' ? 'inline-block' : 'none';
    }

    function setContent(html, rawCode, lang) {
      currentCode = rawCode || '';
      currentLang = lang || '';
      contentEl.innerHTML = html;
      updateRunButton(lang);
    }

    function renderArtifact({ lang, code }) {
      if (titleEl) titleEl.textContent = `Artifact · ${lang}`;
      openPanel();

      if (lang === 'html') {
        const blob = new Blob([code], { type: 'text/html' });
        const src = URL.createObjectURL(blob);
        setContent(`
          <div class="artifact-iframe-wrap">
            <iframe sandbox="allow-scripts" src="${src}"></iframe>
          </div>
        `, code, lang);
      } else if (lang === 'svg') {
        const blob = new Blob([code], { type: 'image/svg+xml' });
        const src = URL.createObjectURL(blob);
        setContent(`
          <div class="artifact-iframe-wrap">
            <iframe sandbox="allow-scripts" src="${src}"></iframe>
          </div>
        `, code, lang);
      } else if (lang === 'mermaid') {
        try {
          if (window.mermaid) {
            const id = 'mm-' + Math.random().toString(36).slice(2);
            setContent(`
              <div class="mermaid" id="${id}">${esc(code)}</div>
            `, code, lang);
            mermaid.init(undefined, '#' + id);
          } else {
            setContent('<div class="artifact-empty">Mermaid library not loaded</div>', code, lang);
          }
        } catch (e) {
          setContent('<div class="artifact-empty">Mermaid render error: ' + esc(e.message) + '</div>', code, lang);
        }
      } else {
        setContent(`
          <div class="artifact-code-wrap">
            <pre><code class="language-${lang}">${esc(code)}</code></pre>
          </div>
        `, code, lang);
      }
    }

    // Expose a lightweight API on window so plugins / "Open in Artifact" buttons can trigger it
    window.HermesArtifacts = {
      open: renderArtifact,
      close: closePanel,
    };
  })();

  // Inject "Open in Artifact" inline buttons on every assistant bubble after streaming ends
  (function listenArtifactClicks() {
    if (!window.HermesProxy) return;
    window.HermesProxy.on('artifactDetected', ({ messageId, index, lang }) => {
      // Find the assistant bubble that carries this msgRef
      const bubble = document.querySelector(`.msg.assistant .bubble[data-msg-ref="${messageId}"]`);
      if (!bubble) return;
      // Find the <code> block with this specific language inside the bubble
      const pre = bubble.querySelectorAll('pre')[index];
      if (!pre) return;
      // Skip if button already injected
      if (pre.querySelector('.open-artifact-btn')) return;
      const btn = document.createElement('button');
      btn.className = 'open-artifact-btn';
      btn.textContent = 'Open in Artifact';
      btn.style.cssText = 'position:absolute; top:4px; right:72px; background:transparent; border:1px solid var(--accent); color:var(--accent); border-radius:3px; padding:2px 8px; font-size:11px; font-family:inherit; cursor:pointer;';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const key = `${messageId}_${index}`;
        if (!_artifactStore.has(key)) return;
        const { lang: l, code } = _artifactStore.get(key);
        window.HermesArtifacts.open({ lang: l, code });
      });
      pre.style.position = 'relative';
      pre.appendChild(btn);
    });
  })();
})();