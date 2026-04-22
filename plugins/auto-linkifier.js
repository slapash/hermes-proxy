// auto-linkifier.js — turn bare URLs in chat messages into clickable links
(function () {
  'use strict';
  if (window.__AutoLinkifierInited) return;
  window.__AutoLinkifierInited = true;

  const PLUGIN_NAME = 'auto-linkifier';
  const URL_RE = /https?:\/\/[^\s\)\]<>"{}|\\^`\]]+/g;
  const TRAILING_PUNCT_RE = /[.,;:!?\)\]>"]+$/;

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }

  function isInside(el) {
    let p = el;
    while (p) {
      if (p.tagName === 'A' || p.tagName === 'CODE' || p.tagName === 'PRE') return true;
      p = p.parentElement;
    }
    return false;
  }

  function linkifyBubble(bubble) {
    if (isInside(bubble)) return;
    const walker = document.createTreeWalker(bubble, NodeFilter.SHOW_TEXT, null);
    const toReplace = [];
    let node;
    while ((node = walker.nextNode())) {
      if (isInside(node.parentElement)) continue;
      const text = node.nodeValue;
      const matches = text.match(URL_RE);
      if (!matches) continue;
      toReplace.push({ node, matches, text });
    }
    for (const { node, matches, text } of toReplace) {
      const frag = document.createDocumentFragment();
      let cursor = 0;
      for (const url of matches) {
        const idx = text.indexOf(url, cursor);
        if (idx === -1) continue;
        if (idx > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, idx)));
        cursor = idx + url.length;
        let href = url;
        let label = url;
        const trimmed = url.replace(TRAILING_PUNCT_RE, '');
        if (trimmed !== url) {
          href = trimmed;
          label = trimmed;
          cursor -= (url.length - trimmed.length);
        }
        const a = document.createElement('a');
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = label;
        frag.appendChild(a);
      }
      const tail = text.slice(cursor);
      if (tail) frag.appendChild(document.createTextNode(tail));
      node.parentNode.replaceChild(frag, node);
    }
  }

  function init() {
    if (window.HermesProxy) {
      HermesProxy.on('messageRendered', bubble => {
        safe(() => linkifyBubble(bubble));
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
