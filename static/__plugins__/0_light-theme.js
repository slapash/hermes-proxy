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
      --accent: #00d4ff;
      --text: #1a2a3a;
      --muted: #6b7b8a;
      --border: #d0dbe5;
      --user-bubble: #e0efff;
    }
    /* Topbar, button accents */
    :root[data-theme="light"] #topbar {
      background: linear-gradient(90deg, #00d4ff 0%, #0088ff 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    /* Send button, login button */
    :root[data-theme="light"] #send-btn,
    :root[data-theme="light"] #login-btn {
      background: #00d4ff !important;
      color: #fff !important;
      border-color: #00a8e0 !important;
      box-shadow: 0 0 8px #00d4ff55;
    }
    /* Active session */
    :root[data-theme="light"] .session-item.active {
      border-color: #00d4ff;
      background: #e0f8ff;
      box-shadow: inset 3px 0 0 #00d4ff;
    }
    /* Sidebar */
    :root[data-theme="light"] #sidebar {
      background: linear-gradient(180deg, #ffffff 0%, #f0f6ff 100%);
      border-right: 1px solid #d0dbe5;
    }
    /* Code blocks */
    :root[data-theme="light"] pre {
      background: #1e1e2e !important;
      color: #e0e0ff !important;
      box-shadow: 0 2px 8px #00000015;
    }
    /* Scrollbar */
    :root[data-theme="light"] ::-webkit-scrollbar-thumb {
      background: #00d4ff;
      border-radius: 6px;
    }
  `;

  // inject <style>
  const style = document.createElement('style');
  style.textContent = lightCSS;
  document.head.appendChild(style);

  // ── Toggle button ──
  function addToggleBtn() {
    const topbar = document.getElementById('topbar');
    if (!topbar || document.getElementById('theme-toggle')) return;
    const btn = document.createElement('button');
    btn.id = 'theme-toggle';
    btn.textContent = getTheme() === 'light' ? '🌙' : '☀️';
    btn.style.cssText = 'margin-left:auto;background:none;border:none;cursor:pointer;font-size:18px;padding:4px 8px;';
    btn.addEventListener('click', () => {
      const now = getTheme() === 'light' ? 'dark' : 'light';
      window.HermesProxy.setTheme(now);
      saveTheme(now);
      btn.textContent = now === 'light' ? '🌙' : '☀️';
    });
    topbar.appendChild(btn);
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
