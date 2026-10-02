import base64
import hashlib
import io
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit

from memeocr import clipboard


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.clipboard().clear()
    application.processEvents()


def paste_images(editor):
    images = []
    block = editor.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fmt = fragment.charFormat()
            if fmt.isImageFormat():
                value = editor.document().resource(
                    QTextDocument.ResourceType.ImageResource, QUrl(fmt.toImageFormat().name()))
                images.append(value.toImage() if hasattr(value, 'toImage') else value)
            iterator += 1
        block = block.next()
    return images


def raster(color=(10, 20, 30, 40), width=40, height=20):
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    from PySide6.QtGui import QColor

    image.fill(QColor(*color))
    return image


def records(count=1):
    return [SimpleNamespace(path=f'/tmp/图片 & <猫> {index}.png') for index in range(count)]


@pytest.mark.parametrize('color', [(255, 0, 0, 255), (10, 20, 30, 40), (0, 0, 0, 0)])
def test_single_bitmap_png_and_html_keep_full_size_and_alpha(app, color):
    source = raster(color, 321, 79)
    payload = clipboard.prepare_images(records(), threading.Event(), lambda _: source)
    assert payload.bitmap == source
    with Image.open(io.BytesIO(payload.png)) as decoded:
        assert decoded.size == (321, 79)
        assert decoded.convert('RGBA').getpixel((0, 0)) == color
    encoded = re.findall(r'data:image/png;base64,([^" ]+)', payload.html)
    assert len(encoded) == 1 and base64.b64decode(encoded[0]) == payload.png
    clipboard.publish_images(payload)
    mime = app.clipboard().mimeData()
    assert mime.hasImage() and mime.hasHtml() and mime.hasText()
    assert not mime.hasUrls()
    assert bytes(mime.data('image/png')) == payload.png
    assert app.clipboard().image().pixelColor(0, 0).getRgb() == color
    assert mime.text() == payload.paths[0]


def test_multi_ctrl_v_inserts_each_independent_image_and_plain_paths(app):
    sources = [raster((200, 10, 20, 255), 31, 21), raster((20, 30, 220, 70), 41, 25)]
    chosen = records(2)
    payload = clipboard.prepare_images(chosen, threading.Event(),
                                       lambda record: sources[chosen.index(record)])
    assert payload.bitmap is None and payload.png == b''
    clipboard.publish_images(payload)
    mime = app.clipboard().mimeData()
    assert mime.hasHtml() and mime.hasUrls() and not mime.hasImage()
    assert [url.toLocalFile() for url in mime.urls()] == list(payload.paths)
    rich = QTextEdit()
    QTest.keyClick(rich, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    pasted = paste_images(rich)
    assert len(pasted) == 2
    for received, source in zip(pasted, sources):
        assert received.size() == source.size()
        # QTextDocument stores QPixmap resources, which use premultiplied alpha.
        expected = source.convertToFormat(received.format())
        assert received.pixelColor(0, 0) == expected.pixelColor(0, 0)
    for encoded, source in zip(re.findall(r'data:image/png;base64,([^" ]+)', payload.html), sources):
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as decoded:
            assert decoded.convert('RGBA').getpixel((0, 0)) == source.pixelColor(0, 0).getRgb()
    plain = QPlainTextEdit()
    QTest.keyClick(plain, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert plain.toPlainText() == '\n'.join(payload.paths)


def test_windows_png_registration_contains_the_actual_png(app, monkeypatch):
    # Replace this module's OS view, not the process-wide os.name/path semantics.
    monkeypatch.setattr(clipboard, 'os', SimpleNamespace(name='nt'))
    payload = clipboard.prepare_images(records(), threading.Event(), lambda _: raster())
    clipboard.publish_images(payload)
    mime = app.clipboard().mimeData()
    assert bytes(mime.data(clipboard.WINDOWS_PNG_MIME)) == payload.png
    assert mime.hasFormat('image/png')


def test_linux_file_transfer_flavor_escapes_spaces_unicode_and_delimiters(app, monkeypatch):
    monkeypatch.setattr(clipboard, 'os', SimpleNamespace(name='posix'))
    payload = clipboard.prepare_images(records(2), threading.Event(), lambda _: raster())
    clipboard.publish_images(payload)
    data = bytes(app.clipboard().mimeData().data('x-special/gnome-copied-files')).decode()
    lines = data.splitlines()
    assert lines[0] == 'copy' and len(lines) == 3
    assert '%20' in lines[1] and '%3C' in lines[1] and '%E5' in lines[1]
    assert [QUrl(line).toLocalFile() for line in lines[1:]] == list(payload.paths)


def test_empty_selection_is_rejected_before_loading():
    def unexpected(_):
        pytest.fail('Empty selection must not decode')
    with pytest.raises(ValueError, match='选择'):
        clipboard.prepare_images([], threading.Event(), unexpected)


def test_null_image_and_failed_encoder_are_rejected(app, monkeypatch):
    with pytest.raises(ValueError, match='剪贴板'):
        clipboard.prepare_images(records(), threading.Event(), lambda _: QImage())
    source = raster()
    monkeypatch.setattr(clipboard, 'encode_png', lambda _: (_ for _ in ()).throw(ValueError('encoder failure')))
    with pytest.raises(ValueError, match='encoder failure'):
        clipboard.prepare_images(records(), threading.Event(), lambda _: source)


def test_png_codec_failure_does_not_publish_new_clipboard(app, monkeypatch):
    source = raster()
    monkeypatch.setattr(QImage, 'save', lambda *args: False)
    app.clipboard().setText('clipboard before codec failure')
    with pytest.raises(ValueError, match='编码'):
        clipboard.prepare_images(records(), threading.Event(), lambda _: source)
    assert app.clipboard().text() == 'clipboard before codec failure'


@pytest.mark.parametrize('when', ['before', 'during_decode', 'after_encode'])
@pytest.mark.parametrize('count', [1, 2])
def test_cancel_returns_no_partial_payload(app, monkeypatch, when, count):
    cancel = threading.Event()
    if when == 'before':
        cancel.set()
    loaded = []
    def load(record):
        loaded.append(record.path)
        if when == 'during_decode':
            cancel.set()
        return raster()
    encoder = clipboard.encode_png
    def encode(image):
        value = encoder(image)
        if when == 'after_encode':
            cancel.set()
        return value
    monkeypatch.setattr(clipboard, 'encode_png', encode)
    assert clipboard.prepare_images(records(count), cancel, load) is None
    assert len(loaded) == (0 if when == 'before' else 1)


def test_one_failed_decode_never_returns_a_subset(app):
    loaded = []
    def load(record):
        loaded.append(record.path)
        if len(loaded) == 2:
            raise OSError('unreadable image')
        return raster()
    with pytest.raises(OSError, match='unreadable'):
        clipboard.prepare_images(records(3), threading.Event(), load)
    assert len(loaded) == 2


def test_size_limit_includes_base64_and_preserves_previous_clipboard(app, monkeypatch):
    source = raster()
    png = clipboard.encode_png(source)
    # Each compressed image fits alone; their combined HTML must exceed the limit.
    single = clipboard.prepare_images(records(), threading.Event(), lambda _: source)
    monkeypatch.setattr(clipboard, 'MAX_CLIPBOARD_BYTES', len(single.html.encode()) + len(single.paths[0].encode()) + 2)
    app.clipboard().setText('keep previous clipboard')
    with pytest.raises(ValueError, match='减少选择'):
        clipboard.prepare_images(records(2), threading.Event(), lambda _: source)
    assert app.clipboard().text() == 'keep previous clipboard'
    assert len(png) < clipboard.MAX_CLIPBOARD_BYTES


def test_9000_small_images_keep_order_without_retaining_all_bitmaps(app):
    chosen = records(9000)
    source = raster(width=1, height=1)
    visited = []
    def load(record):
        visited.append(record.path)
        return source.copy()
    payload = clipboard.prepare_images(chosen, threading.Event(), load)
    assert visited == [record.path for record in chosen]
    assert payload.paths == tuple(visited)
    assert payload.bitmap is None and payload.png == b''
    assert payload.html.count('<img ') == 9000
    assert len(payload.html.encode()) < clipboard.MAX_CLIPBOARD_BYTES


def test_image_files_are_read_only_and_individual_resources_remain_valid(app, tmp_path):
    from memeocr.images import load_qimage

    files = []
    for index, color in enumerate(['red', 'blue']):
        path = tmp_path / f'猫 {index}.png'
        Image.new('RGBA', (27 + index, 15), color).save(path)
        files.append(SimpleNamespace(path=str(path)))
    before = {record.path: hashlib.sha256(Path(record.path).read_bytes()).hexdigest() for record in files}
    payload = clipboard.prepare_images(files, threading.Event(), lambda record: load_qimage(record.path, None))
    clipboard.publish_images(payload)
    rich = QTextEdit()
    rich.paste()
    images = paste_images(rich)
    assert [image.width() for image in images] == [27, 28]
    assert [image.pixelColor(0, 0).name() for image in images] == ['#ff0000', '#0000ff']
    assert before == {record.path: hashlib.sha256(Path(record.path).read_bytes()).hexdigest() for record in files}
