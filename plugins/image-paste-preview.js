// image-paste-preview.js — paste images to upload with previews
(function () {
  'use strict';
  if (window.__ImagePastePreviewInited) return;
  window.__ImagePastePreviewInited = true;

  const PLUGIN_NAME = 'image-paste-preview';

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }
  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function insertAt(el, text) {
    const start = el.selectionStart || 0;
    const end = el.selectionEnd || 0;
    const val = el.value;
    el.value = val.slice(0, start) + text + val.slice(end);
    el.selectionStart = el.selectionEnd = start + text.length;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function showPreview(blob) {
    const wrap = getWrap();
    if (!wrap) return null;

    const objectUrl = URL.createObjectURL(blob);
    const img = document.createElement('img');
    img.src = objectUrl;
    img.alt = 'Pasted image preview';
    img.style.cssText = 'width:48px;height:48px;object-fit:cover;border-radius:4px;border:1px solid var(--border);background:var(--surface2);';

    const fallback = document.createElement('div');
    fallback.textContent = '🖼️';
    fallback.title = 'Image preview unavailable';
    fallback.style.cssText = 'width:48px;height:48px;display:none;align-items:center;justify-content:center;border-radius:4px;border:1px solid var(--border);background:var(--surface2);font-size:22px;';
    img.addEventListener('error', () => {
      img.style.display = 'none';
      fallback.style.display = 'flex';
    }, { once: true });

    const el = document.createElement('div');
    el.style.cssText = 'display:flex;align-items:center;gap:6px;';
    const spinner = document.createElement('span');
    spinner.textContent = '⏳';
    el.appendChild(img);
    el.appendChild(fallback);
    el.appendChild(spinner);
    wrap.appendChild(el);

    function remove() {
      URL.revokeObjectURL(objectUrl);
      if (el.parentNode) el.remove();
    }

    return { el, spinner, remove };
  }

  function getWrap() {
    let w = document.getElementById('paste-preview-wrap');
    if (!w) {
      const input = document.getElementById('msg-input');
      if (!input) return null;
      w = document.createElement('div');
      w.id = 'paste-preview-wrap';
      w.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px;';
      input.parentNode.insertBefore(w, input);
    }
    return w;
  }

  function clearWrap() {
    const w = document.getElementById('paste-preview-wrap');
    if (w) w.remove();
  }

  async function upload(blob, type) {
    const form = new FormData();
    const ext = type.split('/')[1] || 'png';
    form.append('file', blob, `paste.${ext}`);
    const res = await fetch('/api/attachments', { method: 'POST', body: form });
    if (!res.ok) throw new Error('Upload failed: ' + res.status);
    const data = await res.json();
    return data.markdown;
  }

  function onPaste(e) {
    if (!e.clipboardData || !e.clipboardData.items) return;
    let hasImage = false;
    for (const it of e.clipboardData.items) {
      if (it.type.startsWith('image/')) hasImage = true;
    }
    if (!hasImage) return;

    const msgInput = document.getElementById('msg-input');
    if (!msgInput) return;

    e.preventDefault();
    const text = e.clipboardData.getData('text/plain');
    if (text) insertAt(msgInput, text + '\n');

    for (const it of e.clipboardData.items) {
      if (!it.type.startsWith('image/')) continue;
      const blob = it.getAsFile();
      if (!blob) continue;
      const preview = showPreview(blob);
      if (!preview) continue;
      const { spinner, remove } = preview;
      upload(blob, it.type)
        .then(md => {
          spinner.textContent = '✅';
          insertAt(msgInput, md + '\n');
          setTimeout(remove, 2000);
        })
        .catch(err => {
          console.error('[paste]', err);
          spinner.textContent = '❌';
        });
    }
  }

  function init() {
    const msgInput = document.getElementById('msg-input');
    if (!msgInput) return;
    msgInput.addEventListener('paste', onPaste);
    console.log(`[${PLUGIN_NAME}] loaded`);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
