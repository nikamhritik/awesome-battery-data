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
    from PySide6.QtWidgets import QPushButton

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
            Image.new("RGB", (240, 160), "white").save(folder / "empty.png")
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}
            store = Store(Path(temp) / "cache.sqlite3")
            from .core import canonical_path

            root = canonical_path(folder)
            store.add_folder(root)
            items = scan_folder(root)
            engine = Recognizer()
            progress = run_batch(select_batch(items, [], 1000), store, engine, threading.Event())
            check("offline_chinese_ocr", progress.succeeded == 1 and progress.empty == 1 and progress.failed == 0)
            records = store.records()
            report["recognized_text"] = [record.text for record in records if record.text]
            check("chinese_words", any("猫猫" in record.text and "开心" in record.text for record in records))
            check("completed_and_empty_skipped", not select_batch(items, records, 1000))
            reopened = Store(Path(temp) / "cache.sqlite3")
            check("cache_survives_reopen", len(reopened.records()) == 2)
            check("literal_search", len(search_records(reopened, "猫猫")) == 1)
            check("re2_search", len(search_records(reopened, "猫|狗", True)) == 1)
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
            check("ui_search_and_thumbnail", window.results.count() == 1 and
                  not window.results.item(0).icon().isNull() and not errors)
            screenshot = report_path.parent / "windows-search.png"
            check("search_screenshot", window.grab().save(str(screenshot)))
            window.preview(window.results.item(0))
            wait_for(lambda: not window.jobs and window.preview_dialog is not None)
            dialog = window.preview_dialog
            check("preview_screenshot", dialog.grab().save(str(report_path.parent / "windows-preview.png")))
            buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
            buttons["复制到剪贴板"].click()
            wait_for(lambda: not window.jobs)
            copied = app.clipboard().image()
            check("clipboard_image_pixels", not copied.isNull() and copied.width() == 900 and copied.height() == 280)
            if os.name == "nt":
                import ctypes

                native = ctypes.windll.user32
                check("native_clipboard_bitmap", bool(native.IsClipboardFormatAvailable(8) or
                                                      native.IsClipboardFormatAvailable(17)))
            buttons["复制图片所在路径"].click()
            wait_for(lambda: not window.jobs)
            check("clipboard_absolute_path", app.clipboard().text() == canonical_path(source))
            if os.name == "nt":
                check("native_clipboard_unicode_text", bool(native.IsClipboardFormatAvailable(13)))
            dialog.close()
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
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["success"] else 1
