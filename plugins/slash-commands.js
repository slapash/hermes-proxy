// slash-commands.js — autocomplete /commands in msgInput
(function () {
  'use strict';
  if (window.__SlashCommandsInited) return;
  window.__SlashCommandsInited = true;

  const PLUGIN_NAME = 'slash-commands';
  const COMMANDS = [
    { name: 'new',   desc: 'Start a new chat session',         acts: true },
    { name: 'clear', desc: 'Clear messages in current session',acts: true },
    { name: 'search',desc: 'Focus the search bar',              acts: true },
    { name: 'theme', desc: 'Switch theme (light/dark)',         acts: true },
    { name: 'help',  desc: 'Show available commands',            acts: true },
  ];

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }

  let dropdown = null;
  let selectedIdx = -1;

  function hideDropdown() {
    if (dropdown) { dropdown.remove(); dropdown = null; selectedIdx = -1; }
  }

  function buildDropdown(input, matches) {
    hideDropdown();
    dropdown = document.createElement('ul');
    dropdown.className = 'slash-dropdown';
    dropdown.style.cssText = `
      position:absolute;z-index:50;background:var(--surface);border:1px solid var(--border);
      border-radius:6px;padding:4px 0;list-style:none;left:0;right:0;bottom:100%;margin-bottom:4px;
      max-height:200px;overflow-y:auto;box-shadow:0 4px 16px #0006;`;
    matches.forEach((cmd, i) => {
      const li = document.createElement('li');
      li.style.cssText = 'padding:6px 12px;cursor:pointer;font-size:13px;color:var(--text);display:flex;justify-content:space-between;gap:8px;';
      const b = document.createElement('b');
      b.textContent = '/' + cmd.name;
      const span = document.createElement('span');
      span.style.cssText = 'color:var(--muted);font-size:12px;';
      span.textContent = cmd.desc;
      li.appendChild(b);
      li.appendChild(span);
      li.addEventListener('click', () => { selectCmd(cmd, input); });
      li.addEventListener('mouseenter', () => { setSelected(i); });
      dropdown.appendChild(li);
    });
    input.parentNode.style.position = 'relative';
    input.parentNode.appendChild(dropdown);
    setSelected(0);
  }

  function setSelected(i) {
    selectedIdx = Math.max(0, Math.min(i, dropdown ? dropdown.children.length - 1 : 0));
    if (!dropdown) return;
    Array.from(dropdown.children).forEach((c, idx) => {
      c.style.background = idx === selectedIdx ? 'var(--surface2)' : 'transparent';
    });
  }

  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function selectCmd(cmd, input) {
    hideDropdown();
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));

    if (cmd.name === 'new') {
      const btn = document.getElementById('new-session-btn');
      if (btn) btn.click();
    } else if (cmd.name === 'clear') {
      const thread = document.getElementById('thread');
      if (thread) while (thread.firstChild) thread.removeChild(thread.firstChild);
    } else if (cmd.name === 'search') {
      const el = document.getElementById('search-input');
      if (el) { el.focus(); }
    } else if (cmd.name === 'theme') {
      const t = document.documentElement.getAttribute('data-theme');
      const next = t === 'light' ? 'dark' : 'light';
      if (window.HermesProxy) window.HermesProxy.setTheme(next);
    } else if (cmd.name === 'help') {
      const thread = document.getElementById('thread');
      if (thread) {
        const msg = document.createElement('div');
        msg.className = 'msg assistant';
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.textContent = 'Available commands: ' + COMMANDS.map(c => `/${c.name} - ${c.desc}`).join(' | ');
        msg.appendChild(bubble);
        thread.appendChild(msg);
        thread.scrollTop = thread.scrollHeight;
      }
    }
  }

  function onInput(e) {
    const val = e.target.value;
    const m = val.match(/^\/([a-zA-Z]*)$/);
    if (!m) { hideDropdown(); return; }
    const q = m[1].toLowerCase();
    const matches = COMMANDS.filter(c => c.name.startsWith(q));
    if (!matches.length) { hideDropdown(); return; }
    buildDropdown(e.target, matches);
  }

  function onKeydown(e) {
    if (!dropdown) return;
    if (e.key === 'ArrowUp')   { e.preventDefault(); setSelected(selectedIdx - 1); }
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelected(selectedIdx + 1); }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      const cmd = COMMANDS[selectedIdx];
      if (cmd) selectCmd(cmd, e.target);
      return;
    }
    if (e.key === 'Escape') { hideDropdown(); return; }
  }

  function onClickOutside(e) {
    if (dropdown && !dropdown.contains(e.target) && e.target !== msgInput) hideDropdown();
  }

  function init() {
    window.msgInput = document.getElementById('msg-input');
    if (!window.msgInput) return;
    window.msgInput.addEventListener('input', onInput);
    window.msgInput.addEventListener('keydown', onKeydown);
    document.addEventListener('click', onClickOutside);
    console.log(`[${PLUGIN_NAME}] loaded`);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
