// open-graph-card.js — rich link previews for assistant messages
(function () {
  'use strict';
  if (window.__OgCardInited) return;
  window.__OgCardInited = true;

  const PLUGIN_NAME = 'open-graph-card';
  const CACHE_KEY = 'hermes-og-cache';
  const CACHE_TTL = 3600_000; // 1 hour
  const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'];
  const URL_RE = /https?:\/\/[^\s\)\]\u003c\u003e"{}|\\^`\]]+/g;

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }

  function cacheGet(key) {
    return safe(() => {
      const raw = localStorage.getItem(`${CACHE_KEY}:${key}`);
      if (!raw) return null;
      const entry = JSON.parse(raw);
      if (Date.now() - entry.ts > CACHE_TTL) { localStorage.removeItem(key); return null; }
      return entry.data;
    }, null);
  }

  function cacheSet(key, data) {
    safe(() => { localStorage.setItem(`${CACHE_KEY}:${key}`, JSON.stringify({ ts: Date.now(), data })); });
  }

  function isImageUrl(u) {
    try {
      const p = new URL(u).pathname.toLowerCase();
      return IMAGE_EXTS.some(e => p.endsWith(e));
    } catch { return false; }
  }

  function isInsideCode(el) {
    let p = el;
    while (p) {
      if (p.tagName === 'PRE' || p.tagName === 'CODE') return true;
      p = p.parentElement;
    }
    return false;
  }

  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function createCard(data, url) {
    const card = document.createElement('div');
    card.className = 'og-card';
    card.style.cssText = 'padding:10px 14px;margin:4px 0 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);color:var(--text);font-size:13px;display:flex;gap:12px;align-items:flex-start;word-break:break-all;max-width:100%;box-shadow:0 2px 6px #0003;';

    const body = document.createElement('div');
    body.style.cssText = 'min-width:0;';

    if (data.title) {
      const t = document.createElement('div');
      t.style.cssText = 'font-weight:bold;margin-bottom:4px;';
      t.textContent = data.title;
      body.appendChild(t);
    }
    if (data.description) {
      const d = document.createElement('div');
      d.style.cssText = 'color:var(--muted);font-size:12px;line-height:1.3;';
      d.textContent = data.description;
      body.appendChild(d);
    }
    const a = document.createElement('a');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    a.style.cssText = 'color:var(--accent);font-size:12px;display:block;margin-top:4px;';
    a.textContent = new URL(url).hostname;
    body.appendChild(a);

    if (data.image) {
      const img = document.createElement('img');
      img.src = data.image;
      img.style.cssText = 'width:64px;height:64px;object-fit:cover;border-radius:6px;flex-shrink:0;background:var(--bg);';
      img.onerror = function() { this.style.display = 'none'; };
      card.appendChild(img);
    }
    card.appendChild(body);
    return card;
  }

  async function fetchCard(url, afterEl) {
    const cached = cacheGet(url);
    if (cached) {
      afterCard(afterEl, createCard(cached, url));
      return;
    }
    try {
      const res = await fetch('/api/og?url=' + encodeURIComponent(url));
      if (!res.ok) return;
      const data = await res.json();
      cacheSet(url, data);
      if (data.title || data.description || data.image) {
        afterCard(afterEl, createCard(data, url));
      }
    } catch (err) {
      console.warn('[og-card] fetch failed for', url, err);
    }
  }

  function afterCard(msg, card) {
    const existing = msg.querySelector('.og-card');
    if (existing) return;
    msg.appendChild(card);
  }

  function extractFirstUrl(bubble) {
    if (isInsideCode(bubble)) return null;
    const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (isInsideCode(n.parentElement)) continue;
      const m = n.nodeValue.match(URL_RE);
      if (!m) continue;
      const u = m[0].replace(/[.,;:!?)\]\u003e]+$/, '');
      if (isImageUrl(u)) continue;
      return u;
    }
    return null;
  }

  function init() {
    if (window.HermesProxy) {
      HermesProxy.on('messageRendered', (bubble, { role }) => {
        if (role !== 'assistant') return;
        safe(() => {
          const url = extractFirstUrl(bubble);
          if (!url) return;
          fetchCard(url, bubble.closest('.msg') || bubble);
        });
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
