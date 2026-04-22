// export-chat-md.js — download current session as markdown
(function () {
  'use strict';
  if (window.__ExportChatMdInited) return;
  window.__ExportChatMdInited = true;

  const PLUGIN_NAME = 'export-chat-md';

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }

  function stripHtml(html) {
    const div = document.createElement('div');
    div.innerHTML = html;
    return div.textContent || div.innerText || '';
  }

  async function exportSession() {
    const currentId = safe(() => localStorage.getItem('hermes-session-id'), null);
    if (!currentId) {
      alert('Select a session first');
      return;
    }
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(currentId)}/messages`);
      if (!res.ok) throw new Error('Failed to fetch messages');
      const messages = await res.json();
      const now = new Date().toISOString().slice(0, 10);
      const title = document.querySelector('.session-item.active .session-title')?.textContent?.trim() || currentId.slice(0, 8);

      let md = `# Chat Export — ${title}\n`;
      md += `## ${now}\n\n`;
      for (const m of messages) {
        if (m.role === 'user') {
          md += `**User:** ${m.content}\n\n`;
        } else if (m.role === 'assistant') {
          md += `**Assistant:** ${stripHtml(m.content)}\n\n`;
        }
      }

      const blob = new Blob([md], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `hermes-export-${title.replace(/[^\w_-]+/g, '-')}-${now}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('[export-chat-md] export failed:', err);
      alert('Export failed. See console.');
    }
  }

  function init() {
    const header = document.querySelector('.sidebar-header');
    if (!header) return;
    let btn = document.getElementById('export-md-btn');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = 'export-md-btn';
      btn.textContent = '⬇';
      btn.title = 'Export as markdown';
      btn.style.cssText = 'background:none;border:1px solid var(--border);color:var(--muted);border-radius:4px;padding:6px 8px;cursor:pointer;font-size:12px;flex-shrink:0;';
      btn.addEventListener('click', exportSession);
      header.appendChild(btn);
    }
    console.log(`[${PLUGIN_NAME}] loaded`);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
