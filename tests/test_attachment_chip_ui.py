from pathlib import Path

APP_ROOT = Path('/home/hermes/apps/hermes-proxy')


def test_attachment_ui_uses_horizontal_basename_pills_above_input():
    app_js = (APP_ROOT / 'static' / 'app.js').read_text()
    html = (APP_ROOT / 'static' / 'index.html').read_text()
    render_fn = app_js[app_js.index('function _renderAttachmentPreviews'):app_js.index('async function _uploadAttachment')]

    assert '<div id="attachment-previews"></div>\n      <input type="file"' in html
    assert 'id="file-input" accept="image/*"' not in html
    assert 'title="Attach file"' in html
    assert 'Drop files here' in html
    assert '<textarea id="msg-input"' in html

    assert 'function _displayAttachmentName' in app_js
    assert "const name = filename || 'file';" in app_js
    assert 'function _middleEllipsis' in app_js
    assert "return name || 'file';" in app_js
    assert "item.className = 'attachment-row'" in render_fn
    assert "icon.className = 'attachment-icon'" in render_fn
    assert "name.className = 'attachment-name'" in render_fn
    assert "name.textContent = _middleEllipsis(_displayAttachmentName(att.file.name));" in render_fn
    assert "remove.className = 'attachment-remove'" in render_fn
    assert "icon.textContent = '📎'" in render_fn

    # Pills flow horizontally, not as a vertical column.
    assert '#attachment-previews {' in html
    assert 'flex-direction: row;' in html
    assert 'flex-wrap: wrap;' in html
    assert 'border-radius: 999px;' in html
    assert 'display: inline-flex;' in html

    # Minimal pill only: no thumbnail, type badge, metadata, status/progress, or size text.
    assert 'document.createElement(\'img\')' not in render_fn
    assert 'attachment-chip' not in render_fn
    assert 'attachment-type' not in render_fn
    assert 'attachment-meta' not in render_fn
    assert 'attachment-progress' not in render_fn
    assert '_formatBytes' not in app_js
    assert '_fileTypeLabel' not in app_js

    assert '.attachment-row' in html
    assert '.attachment-icon' in html
    assert '.attachment-name' in html
    assert '.attachment-chip' not in html
    assert '.attachment-type' not in html
    assert '.attachment-meta' not in html
    assert '.attachment-progress' not in html
    assert '.attachment-thumb' not in html
