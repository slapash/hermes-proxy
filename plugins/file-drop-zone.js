// file-drop-zone.js — drag and drop file upload with overlay
(function () {
  'use strict';
  if (window.__FileDropZoneInited) return;
  window.__FileDropZoneInited = true;

  const PLUGIN_NAME = 'file-drop-zone';
  const UPLOAD_MAX_MB = 5;

  function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }
  function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  function insertAt(el, text) {
    const ss = el.selectionStart || 0;
    const se = el.selectionEnd || 0;
    el.value = el.value.slice(0, ss) + text + el.value.slice(se);
    el.selectionStart = el.selectionEnd = ss + text.length;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function getOverlay() {
    let o = document.getElementById('file-drop-overlay');
    if (!o) {
      o = document.createElement('div');
      o.id = 'file-drop-overlay';
      o.style.cssText = `
        position:fixed;inset:0;z-index:200;display:none;align-items:center;justify-content:center;
        background:#0008;color:var(--text);font-size:18px;font-weight:bold;backdrop-filter:blur(4px);
        pointer-events:none;
      `;
      o.textContent = 'Drop files here to upload';
      document.body.appendChild(o);
    }
    return o;
  }

  function showOverlay() {
    getOverlay().style.display = 'flex';
  }
  function hideOverlay() {
    getOverlay().style.display = 'none';
  }

  async function uploadFile(file, msgInput) {
    if (file.size > UPLOAD_MAX_MB * 1024 * 1024) {
      alert(`File too large (max ${UPLOAD_MAX_MB} MB)`);
      return;
    }
    const ok = file.type.startsWith('image/') || file.type.startsWith('text/') || file.type.startsWith('application/pdf');
    if (!ok) {
      if (!confirm(`Upload ${esc(file.name)} (${file.type})?`)) return;
    }
    const form = new FormData();
    form.append('file', file, file.name);
    try {
      const res = await fetch('/api/attachments', { method: 'POST', body: form });
      if (!res.ok) throw new Error('Upload failed: ' + res.status);
      const data = await res.json();
      insertAt(msgInput, data.markdown + '\n');
    } catch (e) {
      console.error('[file-drop]', e);
      alert('Upload failed for ' + file.name);
    }
  }

  function onDragEnter(e) {
    e.preventDefault();
    e.stopPropagation();
    showOverlay();
  }
  function onDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
    showOverlay();
  }
  function onDragLeave(e) {
    e.preventDefault();
    e.stopPropagation();
    if (e.relatedTarget && !e.relatedTarget.closest('#file-drop-overlay')) hideOverlay();
  }
  function onDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    hideOverlay();
    const msgInput = document.getElementById('msg-input');
    if (!msgInput) return;
    const files = e.dataTransfer?.files;
    if (!files) return;
    for (const file of files) uploadFile(file, msgInput);
  }

  function init() {
    document.addEventListener('dragenter', onDragEnter);
    document.addEventListener('dragover', onDragOver);
    document.addEventListener('dragleave', onDragLeave);
    document.addEventListener('drop', onDrop);
    console.log(`[${PLUGIN_NAME}] loaded`);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
