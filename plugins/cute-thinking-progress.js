// cute-thinking-progress.js — playful kaomoji thinking/progress UI
(function () {
  'use strict';
  if (window.__CuteThinkingProgressInited) return;
  window.__CuteThinkingProgressInited = true;

  const PLUGIN_NAME = 'cute-thinking-progress';
  const states = new WeakMap();
  const faces = [
    '(｡•̀ᴗ-)✧',
    '( •̀ ω •́ )✧',
    '(づ｡◕‿‿◕｡)づ',
    "(ง'̀-'́)ง",
    '(￣▽￣)ノ',
    '(๑˃ᴗ˂)ﻭ',
  ];
  const sparkles = ['✦', '✧', '⋆', '·'];

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }
  function on(event, fn) { if (window.HermesProxy) window.HermesProxy.on(event, fn); }

  function prefersReducedMotion() {
    return safe(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches, false);
  }

  function injectCss() {
    if (document.getElementById('cute-thinking-progress-style')) return;
    const style = document.createElement('style');
    style.id = 'cute-thinking-progress-style';
    style.textContent = `
      .cute-thinking-bubble {
        display:inline-flex !important;
        align-items:center;
        gap:9px;
        padding:9px 13px !important;
        border-radius:999px !important;
        background:linear-gradient(135deg, color-mix(in srgb, var(--surface) 86%, var(--accent) 14%), var(--bg)) !important;
        border:1px solid color-mix(in srgb, var(--accent) 38%, var(--border)) !important;
        box-shadow:0 8px 22px rgba(0,0,0,.18), inset 0 1px 0 rgba(255,255,255,.05);
        color:var(--text);
        line-height:1.2;
        white-space:nowrap;
      }
      .cute-thinking-sparkle {
        color:var(--accent);
        font-size:13px;
        animation:cuteThinkTwinkle 1.2s ease-in-out infinite;
      }
      .cute-thinking-face {
        color:var(--accent);
        font-weight:700;
        font-size:13px;
        transform-origin:center;
        animation:cuteThinkBob 1.5s cubic-bezier(.2,.8,.2,1) infinite;
      }
      .cute-thinking-label {
        font-size:13px;
        color:var(--text);
      }
      .cute-thinking-dots {
        min-width:18px;
        color:var(--muted);
      }
      .cute-thinking-elapsed {
        color:var(--muted);
        font-size:12px;
        font-variant-numeric:tabular-nums;
        padding-left:2px;
      }
      @keyframes cuteThinkBob {
        0%,100% { transform:translateY(0) rotate(-1deg); }
        50% { transform:translateY(-2px) rotate(1deg); }
      }
      @keyframes cuteThinkTwinkle {
        0%,100% { opacity:.35; transform:scale(.85); }
        50% { opacity:1; transform:scale(1.12); }
      }
      @media (prefers-reduced-motion: reduce) {
        .cute-thinking-sparkle,
        .cute-thinking-face { animation:none !important; }
      }
    `;
    document.head.appendChild(style);
  }

  function captionFor(tool, label) {
    const raw = String(tool || label || '').toLowerCase();
    if (!raw) return 'thinking';
    if (raw.startsWith('browser') || raw.includes('web')) return 'peeking around';
    if (raw.includes('terminal') || raw.includes('shell')) return 'typing in the shell';
    if (raw.includes('patch') || raw.includes('write_file')) return 'stitching code';
    if (raw.includes('read_file') || raw.includes('search_files')) return 'reading carefully';
    if (raw.includes('search')) return 'looking it up';
    if (raw.includes('image')) return 'checking the picture';
    if (raw.includes('todo')) return 'organizing thoughts';
    return String(label || tool || 'thinking').replace(/[_-]+/g, ' ');
  }

  function render(payload) {
    if (!payload || !payload.el || !payload.bubbleEl) return;
    injectCss();

    const bubble = payload.bubbleEl;
    while (bubble.firstChild) bubble.removeChild(bubble.firstChild);
    bubble.classList.add('cute-thinking-bubble');

    const sparkle = document.createElement('span');
    sparkle.className = 'cute-thinking-sparkle';
    sparkle.textContent = '✦';

    const face = document.createElement('span');
    face.className = 'cute-thinking-face';
    face.textContent = faces[0];

    const label = document.createElement('span');
    label.className = 'thinking-text cute-thinking-label';
    label.textContent = captionFor(payload.tool, payload.label);

    const dots = document.createElement('span');
    dots.className = 'cute-thinking-dots';
    dots.textContent = '...';

    const elapsed = document.createElement('span');
    elapsed.className = 'thinking-elapsed cute-thinking-elapsed';
    elapsed.id = 'thinking-elapsed';
    elapsed.textContent = '0s';

    bubble.appendChild(sparkle);
    bubble.appendChild(face);
    bubble.appendChild(label);
    bubble.appendChild(dots);
    bubble.appendChild(elapsed);

    const state = { face, sparkle, dots, label, i: 0, timer: null };
    states.set(payload.el, state);

    if (!prefersReducedMotion()) {
      state.timer = window.setInterval(() => {
        state.i += 1;
        face.textContent = faces[state.i % faces.length];
        sparkle.textContent = sparkles[state.i % sparkles.length];
        dots.textContent = '.'.repeat((state.i % 3) + 1);
      }, 650);
    }
  }

  function update(payload) {
    if (!payload || !payload.el) return;
    const state = states.get(payload.el);
    if (!state) { render(payload); return; }
    state.label.textContent = captionFor(payload.tool, payload.label);
  }

  function cleanup(payload) {
    if (!payload || !payload.el) return;
    const state = states.get(payload.el);
    if (!state) return;
    if (state.timer) window.clearInterval(state.timer);
    states.delete(payload.el);
  }

  on('thinkingCreated', render);
  on('thinkingUpdated', update);
  on('thinkingRemoved', cleanup);

  console.log(`[${PLUGIN_NAME}] loaded`);
})();
