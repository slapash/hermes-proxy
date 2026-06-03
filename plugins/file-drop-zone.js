// file-drop-zone.js — drag/drop files into the core attachment queue
(function () {
  'use strict';
  if (window.__FileDropZoneInited) return;
  window.__FileDropZoneInited = true;

  const PLUGIN_NAME = 'file-drop-zone';

  function hasFileItems(e) {
    return Array.from(e.dataTransfer?.items || []).some(item => item.kind === 'file');
  }

  function getOverlay() {
    return document.getElementById('drag-overlay') || createFallbackOverlay();
  }

  function createFallbackOverlay() {
    let o = document.getElementById('file-drop-overlay');
    if (!o) {
      o = document.createElement('div');
      o.id = 'file-drop-overlay';
      o.style.cssText = `
        position:fixed;inset:0;z-index:200;display:none;align-items:center;justify-content:center;
        background:#0008;color:var(--text);font-size:18px;font-weight:bold;backdrop-filter:blur(4px);
        pointer-events:none;
      `;
      o.textContent = 'Drop files here';
      document.body.appendChild(o);
    }
    return o;
  }

  function showOverlay() {
    const overlay = getOverlay();
    if (overlay.id === 'drag-overlay') overlay.classList.add('active');
    else overlay.style.display = 'flex';
  }

  function hideOverlay() {
    const overlay = getOverlay();
    if (overlay.id === 'drag-overlay') overlay.classList.remove('active');
    else overlay.style.display = 'none';
  }

  function onDragOver(e) {
    if (!hasFileItems(e)) return;
    e.preventDefault();
    showOverlay();
  }

  function onDragLeave(e) {
    if (e.clientX === 0 || e.clientY === 0 || e.clientX >= window.innerWidth || e.clientY >= window.innerHeight) {
      hideOverlay();
    }
  }

  function onDrop(e) {
    if (e.__hermesAttachmentHandled || !e.dataTransfer?.files?.length) return;
    if (!window.HermesProxy?.queueAttachments) return;

    e.__hermesAttachmentHandled = true;
    e.preventDefault();
    hideOverlay();
    window.HermesProxy.queueAttachments(e.dataTransfer.files, { source: PLUGIN_NAME });
  }

  function init() {
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
