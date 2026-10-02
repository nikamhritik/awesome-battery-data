"""Qt desktop interface. Filesystem work and image decoding stay in jobs."""

import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from . import __version__
from .core import canonical_path, run_batch, scan_folder, search_records, select_batch, stat_item
from .images import load_qimage
from .ocr import Recognizer
from .selection import Selection

PAGE_SIZE = 48


class Job(QThread):
    result = Signal(object)
    problem = Signal(str)
    progress = Signal(object)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.function(self))
        except Exception as error:
            self.problem.emit(str(error) or type(error).__name__)


def validated_image(record, max_side=2048):
    before = stat_item(record.path, record.folder)
    if not record.matches(before):
        raise OSError("图片已改变，请重新识别后再打开")
    image = load_qimage(record.path, max_side)
    if not record.matches(stat_item(record.path, record.folder)):
        raise OSError("图片在读取期间发生变化，请重新识别")
    return image


def copy_image(image):
    mime = QMimeData()
    mime.setImageData(image)
    QApplication.clipboard().setMimeData(mime)


def validated_paths(records, cancel):
    paths = []
    for record in records:
        if cancel.is_set():
            return None
        if not record.matches(stat_item(record.path, record.folder)):
            raise OSError("所选图片已改变")
        paths.append(record.path)
    return paths


def copy_files(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
    QApplication.clipboard().setMimeData(mime)


class MainWindow(QMainWindow):
    def __init__(self, store, folders=(), selected="", batch_size=1000):
        super().__init__()
        self.store = store
        self.jobs = set()
        self.album_job = self.batch_job = self.search_job = self.page_job = None
        self.search_generation = 0
        self.page_generation = 0
        self.items = []
        self.hits = []
        self.selection = Selection()
        self.bulk_mode = False
        self.copy_job = None
        self.copy_generation = 0
        self.page = 0
        self.thumbnail_cache = OrderedDict()
        self.recognizer = None
        self.preview_job = None
        self.preview_dialog = None
        self.closing = False
        self.setWindowTitle(f"Where's My Meme · {__version__}")
        self.resize(1020, 760)
        self.setMinimumSize(720, 560)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 20, 24, 20)
        heading = QLabel("Where's My Meme")
        heading.setStyleSheet("font-size: 26px; font-weight: 600")
        layout.addWidget(heading)
        layout.addWidget(QLabel("用图片里的文字，找回想发的那张 meme。"))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._album_page()
        self._search_page()
        footer = QLabel("原图只读 · 离线中文识别 · 动图仅识别首帧")
        footer.setStyleSheet("color: #64748b; padding-top: 8px")
        layout.addWidget(footer)
        self.setCentralWidget(central)
        self.setStyleSheet("""
            QMainWindow { background: #f6f8fb; }
            QTabWidget::pane { border: 1px solid #dbe2ea; background: white; }
            QPushButton { padding: 8px 14px; }
            QLineEdit, QSpinBox, QComboBox { padding: 6px; }
            QProgressBar { min-height: 20px; }
            QListWidget { border: 1px solid #dbe2ea; }
        """)
        for folder, recurse in folders:
            self.albums.addItem(folder, (folder, recurse))
        self.albums.setCurrentIndex(max(0, self.albums.findText(selected)))
        self.batch_size.setValue(batch_size)
        self.albums.currentIndexChanged.connect(self._album_changed)
        self.recursive.toggled.connect(self._recursion_changed)
        QTimer.singleShot(0, self.refresh_album)

    def _job(self, function, success, *, progress=None, failure=None, finished=None):
        job = Job(function, self)
        self.jobs.add(job)
        job.result.connect(lambda value: None if self.closing else success(value))
        job.problem.connect(lambda message: None if self.closing else
                            (failure or self.show_error)(message))
        if progress:
            job.progress.connect(lambda value: None if self.closing else progress(value))
        if finished:
            job.finished.connect(finished)
        job.finished.connect(lambda: self._job_finished(job))
        job.start()
        return job

    def _job_finished(self, job):
        self.jobs.discard(job)
        job.deleteLater()
        if self.closing and not self.jobs:
            QTimer.singleShot(0, self.close)

    def show_error(self, message):
        QMessageBox.warning(self, "无法完成操作", message)

    def _album_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(20, 20, 20, 20)
        box.setSpacing(14)
        box.addWidget(QLabel("选择存放 meme 的文件夹"))
        row = QHBoxLayout()
        self.albums = QComboBox()
        self.albums.setMinimumContentsLength(20)
        row.addWidget(self.albums, 1)
        self.choose_button = QPushButton("选择文件夹")
        self.choose_button.clicked.connect(self.choose_folder)
        row.addWidget(self.choose_button)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_album)
        row.addWidget(self.refresh_button)
        box.addLayout(row)
        self.recursive = QCheckBox("包含子文件夹")
        box.addWidget(self.recursive)
        self.summary = QLabel("选择文件夹后，可分批识别其中的图片。")
        self.summary.setWordWrap(True)
        box.addWidget(self.summary)
        row = QHBoxLayout()
        row.addWidget(QLabel("每批图片数"))
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 10000)
        self.batch_size.setValue(1000)
        row.addWidget(self.batch_size)
        row.addStretch()
        box.addLayout(row)
        row = QHBoxLayout()
        self.start_button = QPushButton("开始识别本批")
        self.start_button.clicked.connect(self.start_batch)
        self.stop_button = QPushButton("停止本批")
        self.stop_button.clicked.connect(self.stop_batch)
        self.stop_button.setEnabled(False)
        self.retry_button = QPushButton("重试本相册失败项")
        self.retry_button.clicked.connect(lambda: self.start_batch(True))
        for button in (self.start_button, self.stop_button, self.retry_button):
            row.addWidget(button)
        box.addLayout(row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        box.addWidget(self.progress_bar)
        self.progress_label = QLabel("每张识别完成后都会保存，下次开始时跳过已完成的图片。")
        self.progress_label.setWordWrap(True)
        box.addWidget(self.progress_label)
        box.addStretch()
        self.tabs.addTab(page, "识别相册")

    def _search_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(20, 20, 20, 20)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入图片里的文字，例如：猫猫")
        self.query.returnPressed.connect(self.search)
        row.addWidget(self.query, 1)
        self.regex = QCheckBox("正则模式")
        row.addWidget(self.regex)
        self.search_button = QPushButton("查找图片")
        self.search_button.clicked.connect(self.search)
        row.addWidget(self.search_button)
        box.addLayout(row)
        self.search_status = QLabel("搜索所有已识别的相册；点击图片可预览、复制图片或路径。")
        self.search_status.setWordWrap(True)
        box.addWidget(self.search_status)
        row = QHBoxLayout()
        self.bulk_button = QPushButton("批量选择")
        self.bulk_button.setCheckable(True)
        self.bulk_button.toggled.connect(self.set_bulk_mode)
        self.selection_status = QLabel("已选 0 张")
        self.select_all_button = QPushButton("全选结果")
        self.select_all_button.clicked.connect(self.select_all_results)
        self.clear_selection_button = QPushButton("清空选择")
        self.clear_selection_button.clicked.connect(self.clear_selection)
        self.copy_selected_button = QPushButton("复制所选到剪贴板")
        self.copy_selected_button.clicked.connect(self.copy_selected)
        for control in (self.bulk_button, self.selection_status, self.select_all_button,
                        self.clear_selection_button, self.copy_selected_button):
            row.addWidget(control)
        row.addStretch()
        box.addLayout(row)
        self.results = QListWidget()
        self.results.setViewMode(QListView.ViewMode.IconMode)
        self.results.setResizeMode(QListView.ResizeMode.Adjust)
        self.results.setMovement(QListView.Movement.Static)
        self.results.setIconSize(QSize(142, 126))
        self.results.setGridSize(QSize(172, 166))
        self.results.setWordWrap(True)
        self.results.itemClicked.connect(lambda item: None if self.bulk_mode else self.preview(item))
        self.results.itemSelectionChanged.connect(self._selection_changed)
        box.addWidget(self.results, 1)
        row = QHBoxLayout()
        self.previous = QPushButton("上一页")
        self.previous.clicked.connect(lambda: self.show_page(self.page - 1))
        self.next = QPushButton("下一页")
        self.next.clicked.connect(lambda: self.show_page(self.page + 1))
        self.page_label = QLabel("")
        row.addWidget(self.previous)
        row.addWidget(self.page_label)
        row.addWidget(self.next)
        row.addStretch()
        box.addLayout(row)
        self.tabs.addTab(page, "搜索图片")
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self._selection_controls()

    def _selection_controls(self):
        busy = self.copy_job is not None
        self.bulk_button.setText("退出选择" if self.bulk_mode else "批量选择")
        self.bulk_button.setEnabled(bool(self.hits) and not busy)
        self.selection_status.setText("正在检查图片…" if busy else f"已选 {len(self.selection)} 张")
        self.selection_status.setVisible(self.bulk_mode)
        for button in (self.select_all_button, self.clear_selection_button, self.copy_selected_button):
            button.setVisible(self.bulk_mode)
        self.select_all_button.setEnabled(bool(self.hits) and not busy)
        self.clear_selection_button.setEnabled(bool(len(self.selection)) and not busy)
        self.copy_selected_button.setEnabled(bool(len(self.selection)) and not busy)
        self.results.setEnabled(not busy)

    def set_bulk_mode(self, enabled):
        self.bulk_mode = enabled
        if not enabled:
            self.selection.clear()
        self.results.blockSignals(True)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection if enabled else
                                      QAbstractItemView.SelectionMode.SingleSelection)
        self.results.clearSelection()
        for index in range(self.results.count()):
            item = self.results.item(index)
            item.setSelected(enabled and self.selection.contains(item.data(Qt.ItemDataRole.UserRole)))
        self.results.blockSignals(False)
        self._selection_controls()
        if self.hits:
            hint = "点击选择，翻页保留所选图片" if enabled else "点击预览和复制"
            self.search_status.setText(f"找到 {len(self.hits)} 张 · {hint}")

    def _selection_changed(self):
        if not self.bulk_mode:
            return
        for index in range(self.results.count()):
            item = self.results.item(index)
            self.selection.set_selected(item.data(Qt.ItemDataRole.UserRole), item.isSelected())
        self._selection_controls()

    def select_all_results(self):
        self.bulk_button.setChecked(True)
        self.selection.select_all()
        self.set_bulk_mode(True)

    def clear_selection(self):
        self.selection.clear()
        self.set_bulk_mode(self.bulk_mode)

    def copy_selected(self):
        records = self.selection.selected_records()
        if self.copy_job or not records:
            return
        generation = self.copy_generation
        def copied(paths):
            if generation != self.copy_generation or paths is None:
                return
            copy_files(paths)
            self.search_status.setText(f"已复制 {len(paths)} 张图片文件，可到支持图片文件粘贴的应用中粘贴。")
        def failed(message):
            if generation == self.copy_generation:
                self.search_status.setText(f"未复制：{message}。请重新搜索。")
        def finished():
            self.copy_job = None
            self._selection_controls()
        self.copy_job = self._job(lambda job: validated_paths(records, job.cancel), copied,
                                  failure=failed, finished=finished)
        self._selection_controls()

    def _selected(self):
        return self.albums.currentData()

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择 meme 文件夹")
        if not path:
            return
        path = canonical_path(path)
        recurse = self.recursive.isChecked()
        def registered(_):
            index = self.albums.findText(path)
            if index < 0:
                self.albums.addItem(path, (path, recurse))
                index = self.albums.count() - 1
            self.albums.setCurrentIndex(index)
            self.refresh_album()
        self._job(lambda job: self.store.add_folder(path, recurse), registered)

    def _album_changed(self):
        if self.batch_job:
            return
        self.refresh_album()

    def _recursion_changed(self, checked):
        selected = self._selected()
        if not selected or selected[1] == checked:
            return
        folder = selected[0]
        self.albums.setItemData(self.albums.currentIndex(), (folder, checked))
        self._job(lambda job: self.store.add_folder(folder, checked), lambda _: self.refresh_album())

    def _album_controls(self, enabled):
        for control in (self.albums, self.choose_button, self.refresh_button,
                        self.recursive, self.batch_size, self.start_button, self.retry_button):
            control.setEnabled(enabled)

    def refresh_album(self):
        if self.batch_job or self.album_job:
            return
        selected = self._selected()
        if not selected:
            self.start_button.setEnabled(False)
            self.retry_button.setEnabled(False)
            return
        folder, recurse = selected
        self.recursive.blockSignals(True)
        self.recursive.setChecked(recurse)
        self.recursive.blockSignals(False)
        self._album_controls(False)
        self.summary.setText("正在读取文件夹…")
        def read(job):
            self.store.set_setting("selected", folder)
            items = scan_folder(folder, recurse)
            records = self.store.records(folder)
            pending = len(select_batch(items, records, 10000))
            retry = len(select_batch(items, records, 10000, True))
            by_path = {record.path: record for record in records}
            done = sum(1 for i in items for r in [by_path.get(i.path)]
                       if r and r.matches(i) and r.status in ("DONE", "EMPTY"))
            return items, pending, retry, done
        def complete(value):
            self.album_job = None
            self.items, pending, retry, done = value
            self.summary.setText(f"本相册 {len(self.items)} 张 · 已完成 {done} · 待识别 {pending} · 失败 {retry}")
            self._album_controls(True)
        def failed(message):
            self.album_job = None
            self.items = []
            self.summary.setText(f"无法读取相册：{message}")
            self._album_controls(True)
            self.start_button.setEnabled(False)
            self.retry_button.setEnabled(False)
        self.album_job = self._job(read, complete, failure=failed)

    def start_batch(self, retry=False):
        if self.batch_job or self.album_job or not self._selected():
            return
        folder, recurse = self._selected()
        count = self.batch_size.value()
        self._album_controls(False)
        self.stop_button.setEnabled(True)
        self.progress_label.setText("正在准备本批图片…")
        def process(job):
            self.store.set_setting("batch_size", str(count))
            batch = select_batch(scan_folder(folder, recurse), self.store.records(folder), count, retry)
            if batch and not job.cancel.is_set() and self.recognizer is None:
                self.recognizer = Recognizer()
            return run_batch(batch, self.store, self.recognizer, job.cancel, job.progress.emit)
        def finished(value):
            self.batch_job = None
            self.stop_button.setEnabled(False)
            self._album_controls(True)
            self.update_progress(value, finished=True)
            self.refresh_album()
        def failed(message):
            self.batch_job = None
            self.stop_button.setEnabled(False)
            self._album_controls(True)
            self.progress_label.setText(f"本批已停止：{message}。已保存的结果可继续使用。")
            self.refresh_album()
        self.batch_job = self._job(process, finished, progress=self.update_progress, failure=failed)

    def stop_batch(self):
        if self.batch_job:
            self.batch_job.cancel.set()
            self.stop_button.setEnabled(False)
            self.progress_label.setText("正在停止，当前图片识别完成后保存结果。")

    def update_progress(self, progress, finished=False):
        self.progress_bar.setRange(0, max(1, progress.total))
        self.progress_bar.setValue(progress.processed)
        state = "已停止" if progress.cancelled else ("本批结束" if finished else "识别中")
        error = f"\n最近错误：{progress.error}" if progress.error else ""
        self.progress_label.setText(
            f"{state} · {progress.processed}/{progress.total}\n"
            f"有文字 {progress.succeeded} · 无文字 {progress.empty} · 失败 {progress.failed}{error}")

    def search(self):
        if self.search_job:
            return
        self.copy_generation += 1
        if self.copy_job:
            self.copy_job.cancel.set()
        self.page_generation += 1
        if self.page_job:
            self.page_job.cancel.set()
        self.selection.replace_results([])
        self.hits = []
        self.bulk_button.setChecked(False)
        self.results.clear()
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.page_label.setText("")
        self._selection_controls()
        query, regex = self.query.text(), self.regex.isChecked()
        if not query.strip():
            self.search_status.setText("请输入要找的文字。")
            return
        self.search_generation += 1
        generation = self.search_generation
        self.search_button.setEnabled(False)
        self.search_status.setText("正在查找图片…")
        def complete(hits):
            self.search_job = None
            self.search_button.setEnabled(True)
            if generation == self.search_generation:
                self.hits = hits
                self.selection.replace_results(hits)
                self._selection_controls()
                self.show_page(0)
        def failed(message):
            self.search_job = None
            self.search_button.setEnabled(True)
            self.search_status.setText(message)
        self.search_job = self._job(lambda job: search_records(self.store, query, regex), complete, failure=failed)

    def show_page(self, page):
        pages = max(1, (len(self.hits) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(max(0, page), pages - 1)
        self.page_generation += 1
        generation = self.page_generation
        if self.page_job:
            self.page_job.cancel.set()
        else:
            self._render_page(generation)
        self.previous.setEnabled(self.page > 0)
        self.next.setEnabled(self.page + 1 < pages)
        self.page_label.setText(f"{self.page + 1} / {pages}")
        hint = "点击选择，翻页保留所选图片" if self.bulk_mode else "点击预览和复制"
        self.search_status.setText(f"找到 {len(self.hits)} 张 · {hint}" if self.hits else
                                  "没有匹配图片。请试试较短的文字；搜索仅包含已识别、未改变的图片。")

    def _render_page(self, generation):
        if self.closing or generation != self.page_generation:
            return
        self.results.blockSignals(True)
        self.results.clear()
        records = self.hits[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]
        cached = dict(self.thumbnail_cache)
        for record in records:
            item = QListWidgetItem(Path(record.path).name)
            item.setData(Qt.ItemDataRole.UserRole, record)
            item.setToolTip(record.text[:400])
            self.results.addItem(item)
            item.setSelected(self.bulk_mode and self.selection.contains(record))
        self.results.blockSignals(False)
        def thumbnails(job):
            decoded = []
            for index, record in enumerate(records):
                if job.cancel.is_set():
                    break
                key = (record.path, record.size, record.modified_ns)
                try:
                    image = cached.get(key)
                    if image is None:
                        image = validated_image(record, 160)
                    decoded.append((index, key, image))
                except (OSError, ValueError):
                    continue
            return decoded
        def loaded(decoded):
            if generation != self.page_generation:
                return
            for index, key, image in decoded:
                self.thumbnail_cache[key] = image
                self.thumbnail_cache.move_to_end(key)
                while len(self.thumbnail_cache) > 96:
                    self.thumbnail_cache.popitem(last=False)
                item = self.results.item(index)
                if item:
                    item.setIcon(QIcon(QPixmap.fromImage(image)))
        def finished():
            self.page_job = None
            if generation != self.page_generation:
                self._render_page(self.page_generation)
        # Connect before starting: finished can already be queued when the user turns a page.
        self.page_job = self._job(thumbnails, loaded, failure=lambda _: None, finished=finished)

    def preview(self, item):
        if self.preview_job:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        if not record:
            return
        def loaded(image):
            self.preview_job = None
            self._preview_dialog(record, image)
        def failed(message):
            self.preview_job = None
            self.show_error(message)
        self.preview_job = self._job(lambda job: validated_image(record), loaded, failure=failed)

    def _preview_dialog(self, record, image):
        import shiboken6

        if self.preview_dialog and shiboken6.isValid(self.preview_dialog):
            self.preview_dialog.close()
        dialog = QDialog(self)
        self.preview_dialog = dialog
        dialog.setWindowTitle(Path(record.path).name)
        dialog.resize(760, 650)
        layout = QVBoxLayout(dialog)
        picture = QLabel()
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        picture.setPixmap(QPixmap.fromImage(image).scaled(710, 390, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(picture)
        text = QTextEdit()
        text.setPlainText(record.text)
        text.setReadOnly(True)
        layout.addWidget(text, 1)
        label = QLabel(record.path)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(label)
        status = QLabel("复制图片后可在聊天软件中粘贴；动图的图片数据为首帧。")
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        image_button, path_button = QPushButton("复制到剪贴板"), QPushButton("复制图片所在路径")
        row.addWidget(image_button)
        row.addWidget(path_button)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.close)
        row.addWidget(close)
        layout.addLayout(row)
        def copied(value, is_image):
            if not shiboken6.isValid(dialog):
                return
            if is_image:
                copy_image(value)
            else:
                QApplication.clipboard().setText(value.path)
            status.setText("已复制图片。" if is_image else "已复制图片所在路径。")
            image_button.setEnabled(True)
            path_button.setEnabled(True)
        def start_copy(is_image):
            image_button.setEnabled(False)
            path_button.setEnabled(False)
            def verify(job):
                if is_image:
                    return validated_image(record, None)
                current = stat_item(record.path, record.folder)
                if not record.matches(current):
                    raise OSError("图片已改变，请重新识别")
                return current
            def failed(message):
                if not shiboken6.isValid(dialog):
                    return
                status.setText(message)
                image_button.setEnabled(True)
                path_button.setEnabled(True)
            self._job(verify, lambda value: copied(value, is_image), failure=failed)
        image_button.clicked.connect(lambda: start_copy(True))
        path_button.clicked.connect(lambda: start_copy(False))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.show()

    def closeEvent(self, event):
        if self.jobs:
            self.closing = True
            for job in self.jobs:
                job.cancel.set()
            self.setEnabled(False)
            self.progress_label.setText("正在退出，等待当前图片保存…")
            event.ignore()
        else:
            event.accept()
