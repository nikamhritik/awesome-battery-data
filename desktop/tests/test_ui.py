import threading
import time

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QPushButton, QTextEdit

from memeocr.core import canonical_path, stat_item
from memeocr.storage import Record, Store
from memeocr.ui import MainWindow, validated_image


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    application.clipboard().clear()
    application.processEvents()


def wait(app, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("UI task did not finish")


@pytest.fixture
def window(app, tmp_path):
    folder = tmp_path / "图片"
    folder.mkdir()
    path = folder / "猫.png"
    Image.new("RGB", (100, 80), "red").save(path)
    root = canonical_path(folder)
    item = stat_item(path, root)
    store = Store(tmp_path / "cache.sqlite3")
    store.add_folder(root)
    store.save(Record(item.path, item.size, item.modified_ns, root, "DONE", "猫猫 ABC\n开心"))
    gui = MainWindow(store, store.folders(), root)
    gui.show_error = lambda message: pytest.fail(message)
    gui.show()
    wait(app, lambda: not gui.jobs)
    yield gui
    gui.close()
    wait(app, lambda: not gui.jobs)
    app.processEvents()


def test_ui_summary_and_default_batch(window):
    assert "已完成 1" in window.summary.text()
    assert "待识别 0" in window.summary.text()
    assert window.batch_size.value() == 1000
    assert window.batch_size.minimum() == 1
    assert window.batch_size.maximum() == 10000


def test_search_thumbnails_preview_and_both_clipboard_actions(app, window):
    window.query.setText("猫猫")
    window.search()
    wait(app, lambda: not window.jobs)
    assert window.results.count() == 1
    assert not window.results.item(0).icon().isNull()
    window.preview(window.results.item(0))
    wait(app, lambda: not window.jobs)
    dialog = window.preview_dialog
    assert dialog.findChild(QTextEdit).toPlainText() == window.hits[0].text
    buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
    buttons["复制到剪贴板"].click()
    wait(app, lambda: not window.jobs)
    image = app.clipboard().image()
    assert image.width() == 100 and image.height() == 80
    assert image.pixelColor(0, 0).red() == 255
    buttons["复制图片所在路径"].click()
    wait(app, lambda: not window.jobs)
    assert app.clipboard().text() == window.hits[0].path
    dialog.close()


def test_regex_error_clears_results_without_dialog(app, window):
    window.query.setText("猫")
    window.search()
    wait(app, lambda: not window.jobs)
    assert window.results.count() == 1
    window.regex.setChecked(True)
    window.query.setText("(?=猫)")
    window.search()
    wait(app, lambda: not window.jobs)
    assert window.results.count() == 0
    assert "正则" in window.search_status.text()


def test_preview_rejects_file_changed_after_search(window):
    record = window.store.records()[0]
    with open(record.path, "ab") as stream:
        stream.write(b"changed")
    with pytest.raises(OSError, match="改变"):
        validated_image(record)


def test_preview_rejects_deletion(window):
    record = window.store.records()[0]
    from pathlib import Path

    Path(record.path).unlink()
    with pytest.raises(OSError):
        validated_image(record)


def test_close_waits_for_active_job(app, window):
    entered, release = threading.Event(), threading.Event()
    def slow(job):
        entered.set()
        release.wait(3)
        return None
    window._job(slow, lambda value: None)
    wait(app, entered.is_set)
    window.close()
    assert window.closing
    assert window.isVisible()
    assert all(job.cancel.is_set() for job in window.jobs)
    release.set()
    wait(app, lambda: not window.jobs and not window.isVisible())


def test_rapid_paging_remains_bounded(app, window):
    window.query.setText("猫")
    window.search()
    wait(app, lambda: not window.jobs)
    record = window.hits[0]
    window.hits = [record] * 9000
    for page in (1, 5, 20, 10, 100, 0):
        window.show_page(page)
    wait(app, lambda: not window.jobs)
    assert window.results.count() == 48
    assert len(window.thumbnail_cache) <= 96
    assert window.page == 0
