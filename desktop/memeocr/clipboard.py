"""Prepare independent clipboard images in a job, then publish them on the UI thread."""

import base64
import os
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QIODevice, QMimeData, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

MAX_CLIPBOARD_BYTES = 64 * 1024 * 1024
WINDOWS_PNG_MIME = 'application/x-qt-windows-mime;value="PNG"'
_HTML_START = '<html><head><meta charset="utf-8"></head><body><!--StartFragment-->'
_HTML_END = '<!--EndFragment--></body></html>'


@dataclass(frozen=True)
class ClipboardImages:
    paths: tuple[str, ...]
    html: str
    bitmap: QImage | None = None
    png: bytes = b''


def encode_png(image):
    buffer = QBuffer()
    if image.isNull() or not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ValueError('无法准备剪贴板图片')
    try:
        if not image.save(buffer, 'PNG'):
            raise ValueError('无法将图片编码为 PNG')
        return bytes(buffer.data())
    finally:
        buffer.close()


def prepare_images(records, cancel, load_image):
    """Decode one image at a time; retain a bitmap only for a single-image copy."""
    if not records:
        raise ValueError('请先选择图片')
    paths = tuple(record.path for record in records)
    size = len(_HTML_START) + len(_HTML_END) + len('\n'.join(paths).encode('utf-8'))
    fragments = []
    bitmap, single_png = None, b''
    for record in records:
        if cancel.is_set():
            return None
        image = load_image(record)
        if cancel.is_set():
            return None
        png = encode_png(image)
        # Check the base64 expansion before allocating it or accumulating more images.
        size += 4 * ((len(png) + 2) // 3) + len('<img src="data:image/png;base64,"> ')
        if size > MAX_CLIPBOARD_BYTES:
            raise ValueError('所选图片的剪贴板内容超过 64 MiB，请减少选择后重试')
        fragments.append('<img src="data:image/png;base64,' +
                         base64.b64encode(png).decode('ascii') + '">')
        if len(records) == 1:
            bitmap, single_png = image, png
        del image, png
    if cancel.is_set():
        return None
    return ClipboardImages(paths, _HTML_START + ' '.join(fragments) + _HTML_END,
                           bitmap, single_png)


def publish_images(payload):
    """Offer image/HTML, original files and explicit text fallback in one clipboard item."""
    mime = QMimeData()
    if payload.bitmap is not None:
        mime.setImageData(payload.bitmap)
        mime.setData('image/png', payload.png)
        if os.name == 'nt':
            # Wine maps the registered Windows PNG format to the desktop image/png target.
            mime.setData(WINDOWS_PNG_MIME, payload.png)
    mime.setHtml(payload.html)
    if len(payload.paths) > 1:
        urls = [QUrl.fromLocalFile(path) for path in payload.paths]
        mime.setUrls(urls)
        if os.name != 'nt':
            mime.setData('x-special/gnome-copied-files',
                         ('copy\n' + '\n'.join(url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
                                              for url in urls)).encode('utf-8'))
    mime.setText('\n'.join(payload.paths))
    QApplication.clipboard().setMimeData(mime)
