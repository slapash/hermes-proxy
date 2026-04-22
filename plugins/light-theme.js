// light-theme.js — Blue-ish white light theme for hermes-proxy
// Author: slapash
// Hooks: HermesProxy.setTheme, HermesProxy.on('themeChange')

(function () {
  const STORAGE_KEY = 'hermes-proxy-theme';

  // ── CSS injection ──
  const lightCSS = `
    :root[data-theme="light"] {
      --bg: #f4f7fa;
      --surface: #ffffff;
      --surface2: #eef2f7;
      --accent: #0066cc;
      --accent-glow: #00d4ff;
      --text: #1a2a3a;
      --muted: #6b7b8a;
      --border: #d0dbe5;
      --user-bubble: #e0efff;
    }
    /* Topbar accent text */
    :root[data-theme="light"] #topbar {
      background: linear-gradient(90deg, #00d4ff 0%, #0088ff 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    /* Send button, login button — neon blue on buttons (white text on neon bg) */
    :root[data-theme="light"] #send-btn,
    :root[data-theme="light"] #login-btn {
      background: #00d4ff !important;
      color: #fff !important;
      border-color: #00a8e0 !important;
      box-shadow: 0 0 8px #00d4ff55;
    }
    /* Active session */
    :root[data-theme="light"] .session-item.active {
      border-color: #0066cc;
      background: #e0f8ff;
      box-shadow: inset 3px 0 0 #0066cc;
    }
    /* Sidebar */
    :root[data-theme="light"] #sidebar {
      background: linear-gradient(180deg, #ffffff 0%, #f0f6ff 100%);
      border-right: 1px solid #d0dbe5;
    }
    /* Code blocks — light theme matching */
    :root[data-theme="light"] pre {
      background: #eef2f7 !important;
      color: #1a2a3a !important;
      border: 1px solid #d0dbe5 !important;
      box-shadow: 0 2px 8px #00000008;
    }
    :root[data-theme="light"] .bubble code {
      background: #e0e8f0 !important;
      border-color: #d0dbe5 !important;
      color: #1a2a3a !important;
    }
    /* Scrollbar */
    :root[data-theme="light"] ::-webkit-scrollbar-thumb {
      background: #0066cc;
      border-radius: 6px;
    }
  `;

  // inject <style>
  const style = document.createElement('style');
  style.textContent = lightCSS;
  document.head.appendChild(style);

  // ── Toggle button ──
  function addToggleBtn() {
    if (document.getElementById('theme-toggle')) return;
    const topbar = document.getElementById('topbar');
    const btn = document.createElement('button');
    btn.id = 'theme-toggle';
    btn.textContent = getTheme() === 'light' ? '🌙' : '☀️';
    btn.style.cssText = 'margin-left:auto;background:none;border:none;cursor:pointer;font-size:18px;padding:4px 8px;';
    btn.addEventListener('click', () => {
      const now = getTheme() === 'light' ? 'dark' : 'light';
      window.HermesProxy.setTheme(now);
      saveTheme(now);
      document.querySelectorAll('#theme-toggle').forEach(b => b.textContent = now === 'light' ? '🌙' : '☀️');
    });
    // Mobile: append to topbar (flex row, pushes right via margin-left:auto)
    if (topbar && getComputedStyle(topbar).display !== 'none') {
      topbar.appendChild(btn);
      return;
    }
    // Desktop: append to sidebar header
    const header = document.querySelector('.sidebar-header');
    if (header) {
      btn.style.marginLeft = '8px';
      header.appendChild(btn);
    }
  }

  function getTheme() {
    try { return localStorage.getItem(STORAGE_KEY) || 'dark'; }
    catch { return 'dark'; }
  }
  function saveTheme(name) {
    try { localStorage.setItem(STORAGE_KEY, name); }
    catch { /* incognito or quota exceeded */ }
  }

  // ── Boot ──
  const t = getTheme();
  if (t === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  }
  addToggleBtn();
  // Expose so other plugins can query
  window.HermesProxy.savedTheme = getTheme;
})();
