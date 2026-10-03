import threading
import time
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QTextEdit
from PySide6.QtTest import QTest

from memeocr.core import canonical_path, stat_item
from memeocr.storage import Record, Store
from memeocr.ui import MainWindow, validated_image
from memeocr import ui


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
    assert not app.clipboard().mimeData().hasUrls()
    assert app.clipboard().mimeData().hasFormat('image/png')
    assert app.clipboard().text() == window.hits[0].path
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


def test_page_change_after_thread_ends_before_events_are_delivered(app, window):
    record = window.store.records()[0]
    window.hits = [replace(record, text=f"page {page}")
                   for page in range(3) for _ in range(48)]
    window.show_page(0)
    completed = window.page_job
    assert completed.wait(5000)
    assert completed.isFinished()
    assert window.page_job is completed
    window.show_page(2)
    wait(app, lambda: not window.jobs)
    assert window.page == 2
    assert window.page_job is None
    assert window.results.count() == 48
    for index in range(48):
        item = window.results.item(index)
        assert item.data(Qt.ItemDataRole.UserRole).text == "page 2"
        assert not item.icon().isNull()


def add_results(window, count):
    root = window.store.folders()[0][0]
    for index in range(count):
        path = Path(root) / f"extra {index:03}.png"
        Image.new("RGB", (24, 20), (index % 255, 50, 70)).save(path)
        item = stat_item(path, root)
        window.store.save(Record(item.path, item.size, item.modified_ns, root, "DONE", "猫猫"))


def search_results(app, window):
    window.tabs.setCurrentIndex(1)
    window.query.setText("猫猫")
    window.search()
    wait(app, lambda: not window.jobs)


def click_result(window, index):
    rect = window.results.visualItemRect(window.results.item(index))
    QTest.mouseClick(window.results.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, rect.center())


def test_native_selection_clicks_persist_across_pages_and_select_all(app, window):
    add_results(window, 55)
    search_results(app, window)
    window.bulk_button.click()
    assert not window.copy_selected_button.isEnabled()
    click_result(window, 0)
    assert window.selection.contains(window.hits[0])
    assert window.preview_dialog is None
    window.show_page(1)
    wait(app, lambda: not window.jobs)
    click_result(window, 0)
    assert len(window.selection) == 2
    assert "已选 2 张" == window.selection_status.text()
    window.show_page(0)
    wait(app, lambda: not window.jobs)
    assert window.results.item(0).isSelected()
    click_result(window, 0)
    assert len(window.selection) == 1
    window.select_all_button.click()
    assert len(window.selection) == 56
    assert len(window.results.selectedItems()) == 48
    window.clear_selection_button.click()
    assert len(window.selection) == 0
    assert not window.copy_selected_button.isEnabled()
    window.bulk_button.click()
    click_result(window, 0)
    wait(app, lambda: not window.jobs)
    assert window.preview_dialog is not None
    window.preview_dialog.close()


def test_bulk_clipboard_uses_independent_images_and_ordered_original_files(app, window):
    add_results(window, 1)
    search_results(app, window)
    records = window.hits[:]
    before = {r.path: hashlib.sha256(Path(r.path).read_bytes()).hexdigest() for r in records}
    window.bulk_button.click()
    window.results.item(1).setSelected(True)
    window.results.item(0).setSelected(True)
    window.copy_selected_button.click()
    wait(app, lambda: not window.jobs)
    mime = app.clipboard().mimeData()
    assert mime.hasUrls() and mime.hasHtml() and not mime.hasImage()
    assert [canonical_path(url.toLocalFile()) for url in mime.urls()] == [r.path for r in records]
    assert mime.text() == '\n'.join(record.path for record in records)
    from test_clipboard import paste_images

    receiver = QTextEdit()
    receiver.paste()
    received = paste_images(receiver)
    assert len(received) == 2
    assert [(image.width(), image.height()) for image in received] == [(24, 20), (100, 80)]
    assert "已复制 2 张" in window.search_status.text()
    assert before == {r.path: hashlib.sha256(Path(r.path).read_bytes()).hexdigest() for r in records}


@pytest.mark.parametrize("failure", ["changed", "deleted", "permission"])
@pytest.mark.parametrize('count', [1, 2])
def test_one_invalid_attachment_preserves_clipboard_and_does_not_copy_subset(app, window, monkeypatch, failure, count):
    add_results(window, count - 1)
    search_results(app, window)
    window.bulk_button.click()
    window.select_all_button.click()
    invalid = window.hits[-1]
    if failure == "changed":
        with open(invalid.path, "ab") as stream:
            stream.write(b"modified synthetic image")
    elif failure == "deleted":
        Path(invalid.path).unlink()
    else:
        original = ui.stat_item
        def inaccessible(path, folder):
            if path == invalid.path:
                raise PermissionError("照片权限已丢失")
            return original(path, folder)
        monkeypatch.setattr(ui, "stat_item", inaccessible)
    app.clipboard().setText("previous clipboard")
    window.copy_selected_button.click()
    wait(app, lambda: not window.jobs)
    assert app.clipboard().text() == "previous clipboard"
    assert not app.clipboard().mimeData().hasUrls()
    assert "未复制" in window.search_status.text()
    assert len(window.selection) == count
    assert window.copy_selected_button.isEnabled()


def test_single_bulk_copy_is_a_full_image_with_path_fallback(app, window):
    search_results(app, window)
    window.bulk_button.click()
    window.select_all_button.click()
    window.copy_selected_button.click()
    wait(app, lambda: not window.jobs)
    mime = app.clipboard().mimeData()
    assert mime.hasImage() and mime.hasHtml() and mime.hasFormat('image/png')
    assert not mime.hasUrls()
    assert mime.text() == window.hits[0].path
    image = app.clipboard().image()
    assert image.width() == 100 and image.height() == 80
    assert image.pixelColor(99, 79).red() == 255


@pytest.mark.parametrize('failure', ['decode', 'encode', 'too_large', 'changed_during_batch'])
def test_preparation_failures_preserve_the_entire_previous_clipboard(app, window, monkeypatch, failure):
    from memeocr import clipboard

    add_results(window, 1)
    search_results(app, window)
    window.bulk_button.click()
    window.select_all_button.click()
    if failure == 'decode':
        def unreadable(*args):
            raise OSError('图片解码失败')
        monkeypatch.setattr(ui, 'load_qimage', unreadable)
    elif failure == 'encode':
        def failed_encoder(*args):
            raise ValueError('PNG 编码失败')
        monkeypatch.setattr(clipboard, 'encode_png', failed_encoder)
    elif failure == 'too_large':
        monkeypatch.setattr(clipboard, 'MAX_CLIPBOARD_BYTES', 1)
    else:
        original = ui.validated_image
        def changed(record, max_side):
            image = original(record, max_side)
            if record == window.hits[-1]:
                with open(window.hits[0].path, 'ab') as stream:
                    stream.write(b'changed while another image was decoded')
            return image
        monkeypatch.setattr(ui, 'validated_image', changed)
    app.clipboard().setText('previous complete clipboard')
    window.copy_selected_button.click()
    wait(app, lambda: not window.jobs)
    assert app.clipboard().text() == 'previous complete clipboard'
    assert not app.clipboard().mimeData().hasHtml()
    assert '未复制' in window.search_status.text()
    if failure == 'too_large':
        assert '减少选择' in window.search_status.text()
    assert len(window.selection) == 2
    assert window.copy_selected_button.isEnabled()


def test_png_preparation_runs_off_the_gui_thread_and_cancel_keeps_clipboard(app, window, monkeypatch):
    from memeocr import clipboard

    search_results(app, window)
    window.bulk_button.click()
    window.select_all_button.click()
    entered, release = threading.Event(), threading.Event()
    threads = []
    original = clipboard.encode_png
    def delayed(image):
        threads.append(threading.get_ident())
        entered.set()
        release.wait(5)
        return original(image)
    monkeypatch.setattr(clipboard, 'encode_png', delayed)
    app.clipboard().setText('keep clipboard while cancelled')
    window.copy_selected_button.click()
    wait(app, entered.is_set)
    assert threads == [threads[0]] and threads[0] != threading.get_ident()
    assert not window.copy_selected_button.isEnabled()
    window.query.setText('missing')
    window.search()
    release.set()
    wait(app, lambda: not window.jobs)
    assert app.clipboard().text() == 'keep clipboard while cancelled'
    assert len(window.selection) == 0


@pytest.mark.parametrize("query, regex", [("missing", False), ("(?=猫)", True), ("", False)])
def test_new_search_and_invalid_queries_clear_selection(app, window, query, regex):
    search_results(app, window)
    window.bulk_button.click()
    window.select_all_button.click()
    window.query.setText(query)
    window.regex.setChecked(regex)
    window.search()
    assert len(window.selection) == 0
    wait(app, lambda: not window.jobs)
    assert window.results.count() == 0
    assert not window.bulk_mode
    assert not window.copy_selected_button.isEnabled()


def test_repeated_copy_and_new_search_before_queued_copy_result(app, window, monkeypatch):
    search_results(app, window)
    window.bulk_button.click()
    window.select_all_button.click()
    original = ui.validated_clipboard
    calls = []
    def counted(records, cancel):
        calls.append(len(records))
        return original(records, cancel)
    monkeypatch.setattr(ui, "validated_clipboard", counted)
    app.clipboard().setText("keep this clipboard")
    window.copy_selected()
    window.copy_selected()
    assert not window.copy_selected_button.isEnabled()
    assert window.copy_job.wait(5000)
    window.query.setText("missing")
    window.search()
    wait(app, lambda: not window.jobs)
    assert calls == [1]
    assert app.clipboard().text() == "keep this clipboard"
    assert len(window.selection) == 0


def test_closing_before_queued_copy_result_keeps_clipboard(app, window):
    search_results(app, window)
    window.bulk_button.click()
    window.select_all_button.click()
    app.clipboard().setText("keep clipboard on exit")
    window.copy_selected()
    assert window.copy_job.wait(5000)
    window.close()
    wait(app, lambda: not window.jobs and not window.isVisible())
    assert app.clipboard().text() == "keep clipboard on exit"
