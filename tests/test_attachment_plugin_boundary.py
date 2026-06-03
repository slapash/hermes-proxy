from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')


def test_core_exposes_attachment_queue_api_without_owning_paste_or_drop_events():
    app_js = (APP_ROOT / 'static' / 'app.js').read_text()

    assert 'queueAttachments(files' in app_js
    assert 'uploadAttachment(file' in app_js
    assert "document.addEventListener('dragover'" not in app_js
    assert "document.addEventListener('drop'" not in app_js
    assert "msgInput.addEventListener('paste'" not in app_js


def test_paste_and_drop_plugins_use_core_attachment_api_not_direct_uploads():
    paste = (APP_ROOT / 'plugins' / 'image-paste-preview.js').read_text()
    drop = (APP_ROOT / 'plugins' / 'file-drop-zone.js').read_text()

    assert 'HermesProxy.queueAttachments' in paste
    assert 'HermesProxy.queueAttachments' in drop
    assert "fetch('/api/attachments'" not in paste
    assert "fetch('/api/attachments'" not in drop
    assert 'insertAt(msgInput, data.markdown' not in drop
    assert 'insertAt(msgInput, md' not in paste


def test_plugins_mark_events_as_handled_to_prevent_future_duplicate_uploads():
    paste = (APP_ROOT / 'plugins' / 'image-paste-preview.js').read_text()
    drop = (APP_ROOT / 'plugins' / 'file-drop-zone.js').read_text()

    assert '__hermesAttachmentHandled' in paste
    assert '__hermesAttachmentHandled' in drop
