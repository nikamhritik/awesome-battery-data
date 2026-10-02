"""Exercise the shipped program on synthetic images, including the native clipboard."""

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path


def self_test(report_path, app):
    from PIL import Image
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtGui import QImage, QTextDocument
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton, QPlainTextEdit, QTextEdit

    from . import __version__
    from .core import run_batch, scan_folder, search_records, select_batch
    from .ocr import Recognizer
    from .storage import Store
    from .ui import MainWindow

    report_path = Path(report_path).resolve()
    if not getattr(sys, "frozen", False) and report_path.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError("自验文件须保存在源码目录之外")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"version": __version__, "platform": sys.platform,
              "qt_platform": app.platformName(), "frozen": bool(getattr(sys, "frozen", False)),
              "checks": {}, "success": False}
    window = None
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def deny_network(*args, **kwargs):
        raise RuntimeError("自验期间禁止网络连接")

    socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = deny_network

    def check(name, condition):
        report["checks"][name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    def wait_for(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise TimeoutError("等待后台任务完成超时")

    def paste_receivers(paths):
        rich = QTextEdit()
        rich.resize(1040, 650)
        QTest.keyClick(rich, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        images = []
        block = rich.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    resource = rich.document().resource(
                        QTextDocument.ResourceType.ImageResource, QUrl(fmt.toImageFormat().name()))
                    images.append(resource)
                iterator += 1
            block = block.next()
        plain = QPlainTextEdit()
        QTest.keyClick(plain, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        return rich, images, plain.toPlainText() == '\n'.join(paths)

    def external_clipboard(phase, paths):
        # Optional handshake for a separate native Linux receiver during Wine tests.
        location = os.environ.get('MEMEOCR_SELFTEST_EXTERNAL_CLIPBOARD_DIR')
        if not location:
            return
        directory = Path(location)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'ready.json').write_text(json.dumps(
            {'phase': phase, 'count': len(paths), 'paths': paths}, ensure_ascii=False), encoding='utf-8')
        wait_for(lambda: (directory / f'{phase}.ack').exists(), timeout=45)
        result = json.loads((directory / f'{phase}-receiver.json').read_text(encoding='utf-8'))
        report.setdefault('external_clipboard', {})[phase] = result
        check(f'external_{phase}_clipboard_paste', result['success'])

    def native_clipboard_bytes(format_id):
        import ctypes

        native = ctypes.windll.user32
        kernel = ctypes.windll.kernel32
        native.GetClipboardData.argtypes = [ctypes.c_uint]
        native.GetClipboardData.restype = ctypes.c_void_p
        kernel.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel.GlobalSize.argtypes = [ctypes.c_void_p]
        kernel.GlobalSize.restype = ctypes.c_size_t
        if not native.OpenClipboard(None):
            raise OSError('无法读取原生剪贴板')
        try:
            handle = native.GetClipboardData(format_id)
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                raise OSError('剪贴板数据为空')
            try:
                return ctypes.string_at(pointer, kernel.GlobalSize(handle))
            finally:
                kernel.GlobalUnlock(handle)
        finally:
            native.CloseClipboard()

    try:
        if os.name == "nt":
            check("native_windows_platform", app.platformName() == "windows")
        import onnxruntime

        onnxruntime.disable_telemetry_events()
        with tempfile.TemporaryDirectory(prefix="memeocr-selftest-", dir=report_path.parent) as temp:
            folder = Path(temp) / "图片"
            folder.mkdir()
            source = folder / "猫猫.png"
            shutil.copyfile(Path(__file__).parent / "assets" / "ocr-fixture.png", source)
            shutil.copyfile(source, folder / "猫猫 2.png")
            Image.new("RGB", (240, 160), "white").save(folder / "empty.png")
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
            store = Store(Path(temp) / "cache.sqlite3")
            from .core import canonical_path

            root = canonical_path(folder)
            store.add_folder(root)
            items = scan_folder(root)
            engine = Recognizer()
            progress = run_batch(select_batch(items, [], 1000), store, engine, threading.Event())
            check("offline_chinese_ocr", progress.succeeded == 2 and progress.empty == 1 and progress.failed == 0)
            records = store.records()
            report["recognized_text"] = [record.text for record in records if record.text]
            check("chinese_words", any("猫猫" in record.text and "开心" in record.text for record in records))
            check("completed_and_empty_skipped", not select_batch(items, records, 1000))
            reopened = Store(Path(temp) / "cache.sqlite3")
            check("cache_survives_reopen", len(reopened.records()) == 3)
            check("literal_search", len(search_records(reopened, "猫猫")) == 2)
            check("re2_search", len(search_records(reopened, "猫|狗", True)) == 2)
            try:
                search_records(reopened, "(?=猫)", True)
            except ValueError:
                check("unsupported_regex_rejected", True)
            else:
                check("unsupported_regex_rejected", False)
            window = MainWindow(reopened, reopened.folders(), root)
            errors = []
            window.show_error = errors.append
            window.show()
            wait_for(lambda: not window.jobs)
            window.tabs.setCurrentIndex(1)
            window.query.setText("猫猫")
            window.search()
            wait_for(lambda: not window.jobs)
            check("ui_search_and_thumbnail", window.results.count() == 2 and
                  not window.results.item(0).icon().isNull() and not errors)
            screenshot = report_path.parent / "windows-search.png"
            check("search_screenshot", window.grab().save(str(screenshot)))
            preview_item = next(window.results.item(index) for index in range(window.results.count())
                                if window.hits[index].path == canonical_path(source))
            window.preview(preview_item)
            wait_for(lambda: not window.jobs and window.preview_dialog is not None)
            dialog = window.preview_dialog
            check("preview_screenshot", dialog.grab().save(str(report_path.parent / "windows-preview.png")))
            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            buttons["复制到剪贴板"].click()
            wait_for(lambda: not window.jobs)
            copied = app.clipboard().image()
            check("clipboard_image_pixels", not copied.isNull() and copied.width() == 900 and copied.height() == 280)
            png = bytes(app.clipboard().mimeData().data('image/png'))
            check('clipboard_explicit_png', not QImage.fromData(png, 'PNG').isNull())
            check('preview_text_fallback', app.clipboard().text() == canonical_path(source))
            if os.name == "nt":
                import ctypes

                native = ctypes.windll.user32
                check("native_clipboard_bitmap", bool(native.IsClipboardFormatAvailable(8) or
                                                      native.IsClipboardFormatAvailable(17)))
                native.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
                native.RegisterClipboardFormatW.restype = ctypes.c_uint
                png_format = native.RegisterClipboardFormatW('PNG')
                check('native_clipboard_png', bool(native.IsClipboardFormatAvailable(png_format)))
                native_png = QImage.fromData(native_clipboard_bytes(png_format), 'PNG')
                check('native_png_decodes', not native_png.isNull() and native_png.width() == 900 and
                      native_png.height() == 280)
            buttons["复制图片所在路径"].click()
            wait_for(lambda: not window.jobs)
            check("clipboard_absolute_path", app.clipboard().text() == canonical_path(source))
            if os.name == "nt":
                check("native_clipboard_unicode_text", bool(native.IsClipboardFormatAvailable(13)))
            dialog.close()
            window.bulk_button.click()
            window.results.item(0).setSelected(True)
            window.copy_selected_button.click()
            wait_for(lambda: not window.jobs)
            check('bulk_single_image', app.clipboard().mimeData().hasImage() and
                  not app.clipboard().mimeData().hasUrls())
            single_paths = [record.path for record in window.selection.selected_records()]
            _, single_images, single_text = paste_receivers(single_paths)
            check('single_ctrl_v_and_text_fallback', len(single_images) == 1 and single_text)
            external_clipboard('single', single_paths)
            window.select_all_button.click()
            check("bulk_selection_count", len(window.selection) == 2)
            check("bulk_selection_screenshot", window.grab().save(str(report_path.parent / "windows-bulk-selection.png")))
            expected_paths = [record.path for record in window.selection.selected_records()]
            window.copy_selected_button.click()
            wait_for(lambda: not window.jobs)
            check("clipboard_multiple_files", [canonical_path(url.toLocalFile())
                  for url in app.clipboard().mimeData().urls()] == expected_paths)
            rich, pasted, text_matches = paste_receivers(expected_paths)
            check('multiple_ctrl_v_independent_images', len(pasted) == 2 and
                  all(not image.isNull() and image.width() == 900 and image.height() == 280 for image in pasted))
            check('multiple_plain_text_fallback', text_matches)
            rich.show()
            app.processEvents()
            check('rich_paste_screenshot', rich.grab().save(str(report_path.parent / 'windows-rich-paste.png')))
            rich.close()
            external_clipboard('multiple', expected_paths)
            if os.name == "nt":
                from ctypes import c_void_p, c_uint, c_wchar_p

                check("native_clipboard_hdrop", bool(native.IsClipboardFormatAvailable(15)))
                html_format = native.RegisterClipboardFormatW('HTML Format')
                check('native_clipboard_html', bool(native.IsClipboardFormatAvailable(html_format)))
                html_data = native_clipboard_bytes(html_format)
                check('native_html_contains_independent_images', html_data.count(b'<img ') == 2)
                check('native_multi_text_fallback', bool(native.IsClipboardFormatAvailable(13)))
                native.GetClipboardData.restype = c_void_p
                shell = ctypes.windll.shell32
                shell.DragQueryFileW.argtypes = [c_void_p, c_uint, c_wchar_p, c_uint]
                shell.DragQueryFileW.restype = c_uint
                check("native_clipboard_open", bool(native.OpenClipboard(None)))
                try:
                    handle = native.GetClipboardData(15)
                    count = shell.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
                    paths = []
                    for index in range(count):
                        length = shell.DragQueryFileW(handle, index, None, 0)
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        shell.DragQueryFileW(handle, index, buffer, length + 1)
                        paths.append(canonical_path(buffer.value))
                    check("native_clipboard_original_files", paths == expected_paths)
                finally:
                    native.CloseClipboard()
            window.clear_selection_button.click()
            check("bulk_clear_disables_copy", len(window.selection) == 0 and
                  not window.copy_selected_button.isEnabled())
            release = threading.Event()
            entered = threading.Event()
            def current_job(job):
                entered.set()
                release.wait(5)
            window._job(current_job, lambda _: None)
            wait_for(entered.is_set)
            window.close()
            check("close_waits_for_job", window.closing and window.isVisible())
            release.set()
            wait_for(lambda: not window.jobs and not window.isVisible())
            check("safe_thread_shutdown", True)
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
            check("source_images_unchanged", before == after)
            check("network_connections_blocked", True)
        report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if window:
            window.close()
            try:
                wait_for(lambda: not window.jobs, timeout=60)
            except Exception:
                report["success"] = False
                report["shutdown_error"] = traceback.format_exc()
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex
        socket.getaddrinfo = original_resolve
        # These clipboard URLs point to temporary fixtures, not user images.
        # Release the Qt-owned MIME object before QApplication is torn down.
        app.clipboard().clear()
        app.processEvents()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1
