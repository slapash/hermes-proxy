// image-paste-preview.js — paste images into the core attachment queue
(function () {
  'use strict';
  if (window.__ImagePastePreviewInited) return;
  window.__ImagePastePreviewInited = true;

  const PLUGIN_NAME = 'image-paste-preview';

  function insertAt(el, text) {
    const start = el.selectionStart || 0;
    const end = el.selectionEnd || 0;
    const val = el.value;
    el.value = val.slice(0, start) + text + val.slice(end);
    el.selectionStart = el.selectionEnd = start + text.length;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function imageFilesFromClipboard(data) {
    return Array.from(data?.items || [])
      .filter(item => item.type && item.type.startsWith('image/'))
      .map(item => item.getAsFile())
      .filter(Boolean);
  }

  function onPaste(e) {
    if (e.__hermesAttachmentHandled) return;
    const files = imageFilesFromClipboard(e.clipboardData);
    if (!files.length) return;

    const msgInput = document.getElementById('msg-input');
    if (!msgInput || !window.HermesProxy?.queueAttachments) return;

    e.__hermesAttachmentHandled = true;
    e.preventDefault();

    const text = e.clipboardData.getData('text/plain');
    if (text) insertAt(msgInput, text + '\n');
    window.HermesProxy.queueAttachments(files, { source: PLUGIN_NAME });
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
