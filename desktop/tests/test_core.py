"""core 模块的测试：normalize、scan、select_batch、run_batch、search。"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

from memeocr.core import (
    SUPPORTED_EXTENSIONS,
    Progress,
    canonical_path,
    normalize,
    run_batch,
    scan_folder,
    search_records,
    select_batch,
    stat_item,
)
from memeocr.storage import Item, Record, Store


def _write_image(path: Path, content: bytes = b"IMG") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _item_for(path: Path, folder: Path) -> Item:
    st = path.stat()
    return Item(
        path=canonical_path(path),
        size=st.st_size,
        modified_ns=st.st_mtime_ns,
        folder=canonical_path(folder),
    )


def _record_of(item: Item, status: str, text: str = "", error: str = "") -> Record:
    return Record(item.path, item.size, item.modified_ns, item.folder,
                  status, text, error)


def _silent_publish(_progress: Progress) -> None:
    pass


# =========================================================================
# normalize
# =========================================================================

class TestNormalize:
    def test_fullwidth_ascii_to_halfwidth(self):
        # FF01-FF5E 逐个映射
        full = "".join(chr(c) for c in range(0xFF01, 0xFF5F))
        half = "".join(chr(c - 0xFEE0) for c in range(0xFF01, 0xFF5F))
        assert normalize(full) == normalize(half)
        assert normalize("Ｈｅｌｌｏ，Ｗｏｒｌｄ！") == "hello,world!"

    def test_ideographic_space_becomes_ascii_space(self):
        assert normalize("a\u3000b") == "a b"
        assert normalize("\u3000Ａ\u3000") == "a"

    def test_whitespace_strip_and_collapse(self):
        assert normalize("  a \t b\n c  ") == "a b c"
        assert normalize("   \t\n ") == ""
        assert normalize("") == ""

    def test_lowercases(self):
        assert normalize("AbC 喵") == "abc 喵"

    def test_idempotent(self):
        samples = ["Ａｂｃ\u3000ＤＥＦ", "  x  Y  ", "emoji \U0001F63B 喵", "ＡＡ\t\tＢ\u3000Ｂ"]
        for s in samples:
            once = normalize(s)
            assert normalize(once) == once

    def test_emoji_and_cjk_untouched(self):
        text = "\U0001F63B 梗图 emoji_🐱"
        assert normalize(text) == text

    def test_special_symbols(self):
        # 引号、破折号、省略号等非 ASCII 标点保持原样
        assert normalize("“中文”—…") == "“中文”—…"
        # 全角波浪线 FF5E 在范围内，半角化
        assert normalize("～") == "~"


# =========================================================================
# canonical_path / stat_item
# =========================================================================

class TestPaths:
    def test_canonical_path_is_absolute(self, tmp_path):
        p = canonical_path(tmp_path / "a.PNG")
        assert os.path.isabs(p)
        assert p == canonical_path(tmp_path / "." / "a.PNG")

    def test_supported_extensions(self):
        assert ".jpg" in SUPPORTED_EXTENSIONS and ".png" in SUPPORTED_EXTENSIONS
        assert ".webp" in SUPPORTED_EXTENSIONS and ".tif" in SUPPORTED_EXTENSIONS
        assert ".txt" not in SUPPORTED_EXTENSIONS and ".pdf" not in SUPPORTED_EXTENSIONS

    def test_stat_item(self, tmp_path):
        img = _write_image(tmp_path / "a.png")
        item = stat_item(img, str(tmp_path))
        st = img.stat()
        assert item.path == canonical_path(img)
        assert item.size == st.st_size
        assert item.modified_ns == st.st_mtime_ns
        assert item.folder == canonical_path(tmp_path)

    def test_stat_item_rejects_directory(self, tmp_path):
        with pytest.raises(OSError):
            stat_item(tmp_path, str(tmp_path))

    @pytest.mark.skipif(os.name == "nt", reason="POSIX chmod permission test")
    def test_stat_item_rejects_unreadable(self, tmp_path):
        img = _write_image(tmp_path / "a.png")
        img.chmod(0o000)
        try:
            with pytest.raises(OSError):
                stat_item(img, str(tmp_path))
        finally:
            img.chmod(0o644)

    def test_stat_item_rejects_missing(self, tmp_path):
        with pytest.raises(OSError):
            stat_item(tmp_path / "nope.png", str(tmp_path))


# =========================================================================
# scan_folder
# =========================================================================

class TestScanFolder:
    def test_scans_sorted_images_only(self, tmp_path):
        _write_image(tmp_path / "b.png")
        _write_image(tmp_path / "a.jpg")
        _write_image(tmp_path / "c.txt")
        _write_image(tmp_path / "d.webp")
        items = scan_folder(tmp_path)
        assert [os.path.basename(i.path) for i in items] == ["a.jpg", "b.png", "d.webp"]

    def test_all_supported_extensions(self, tmp_path):
        exts = ["jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"]
        for e in exts:
            _write_image(tmp_path / f"f.{e}")
        assert len(scan_folder(tmp_path)) == len(exts)

    def test_ignores_symlinks_and_non_regular(self, tmp_path):
        real = _write_image(tmp_path / "real.png")
        link = tmp_path / "link.png"
        link.symlink_to(real)
        assert [i.path for i in scan_folder(tmp_path)] == [canonical_path(real)]

    def test_non_recursive_by_default(self, tmp_path):
        _write_image(tmp_path / "top.png")
        _write_image(tmp_path / "sub" / "deep.png")
        items = scan_folder(tmp_path)
        assert [os.path.basename(i.path) for i in items] == ["top.png"]

    def test_recursive(self, tmp_path):
        _write_image(tmp_path / "top.png")
        _write_image(tmp_path / "sub" / "deep.png")
        _write_image(tmp_path / "sub" / "deeper" / "deepest.jpg")
        items = scan_folder(tmp_path, recurse=True)
        names = [os.path.basename(i.path) for i in items]
        assert names == ["deep.png", "deepest.jpg", "top.png"]
        assert all(i.folder == canonical_path(tmp_path) for i in items)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX chmod permission test")
    def test_unreadable_subdir_raises_when_recursive(self, tmp_path):
        _write_image(tmp_path / "top.png")
        sub = tmp_path / "sub"
        _write_image(sub / "x.png")
        sub.chmod(0o000)
        try:
            with pytest.raises(PermissionError):
                scan_folder(tmp_path, recurse=True)
        finally:
            sub.chmod(0o755)

    def test_unreadable_subdir_ignored_when_not_recursive(self, tmp_path):
        _write_image(tmp_path / "top.png")
        sub = tmp_path / "sub"
        _write_image(sub / "x.png")
        sub.chmod(0o000)
        try:
            items = scan_folder(tmp_path, recurse=False)
            assert [os.path.basename(i.path) for i in items] == ["top.png"]
        finally:
            sub.chmod(0o755)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX chmod permission test")
    def test_root_permission_error_raises(self, tmp_path):
        secret = tmp_path / "gallery"
        _write_image(secret / "x.png")
        secret.chmod(0o000)
        try:
            with pytest.raises(PermissionError):
                scan_folder(secret)
        finally:
            secret.chmod(0o755)

    def test_missing_root_raises(self, tmp_path):
        with pytest.raises(OSError):
            scan_folder(tmp_path / "nope")

    def test_single_file_race_is_skipped(self, tmp_path):
        _write_image(tmp_path / "keep.png")
        gone = _write_image(tmp_path / "gone.png")
        items_before = scan_folder(tmp_path)
        gone.unlink()  # 之后再次扫描不会报错
        items_after = scan_folder(tmp_path)
        assert len(items_before) == 2 and len(items_after) == 1

    def test_never_writes_into_folder(self, tmp_path):
        _write_image(tmp_path / "a.png")
        scan_folder(tmp_path, recurse=True)
        assert [p.name for p in tmp_path.iterdir()] == ["a.png"]


# =========================================================================
# select_batch
# =========================================================================

def _mk_items(paths, folder="/pics"):
    return [Item(p, 1, 100, folder) for p in paths]


class TestSelectBatch:
    def test_limit_bounds(self):
        with pytest.raises(ValueError):
            select_batch([], [], 0)
        with pytest.raises(ValueError):
            select_batch([], [], 10001)
        assert select_batch([], [], 1) == []
        assert select_batch([], [], 10000) == []

    def test_new_and_version_changed_selected(self):
        items = _mk_items(["/a", "/b", "/c"])
        changed = Item("/b", 2, 200, "/pics")  # 记录是旧版本
        records = [_record_of(changed, "DONE", "old")]
        selected = select_batch(items, records, 10)
        assert [i.path for i in selected] == ["/a", "/b", "/c"]

    def test_done_empty_skipped_same_version(self):
        items = _mk_items(["/a", "/b"])
        records = [
            _record_of(items[0], "DONE", "text"),
            _record_of(items[1], "EMPTY"),
        ]
        assert select_batch(items, records, 10) == []

    def test_failed_same_version_only_with_retry(self):
        items = _mk_items(["/a"])
        records = [_record_of(items[0], "FAILED", "", "boom")]
        assert select_batch(items, records, 10) == []
        assert [i.path for i in select_batch(items, records, 10, retry=True)] == ["/a"]

    def test_failed_version_changed_selected_without_retry(self):
        # FAILED 记录版本 (2,200)，当前 item 版本 (1,100)：版本已变化，
        # 即使不 retry 也应重做。
        items = [Item("/a", 1, 100, "/pics")]
        old = Item("/a", 2, 200, "/pics")
        records = [_record_of(old, "FAILED", "", "boom")]
        assert [i.path for i in select_batch(items, records, 10)] == ["/a"]
        # 同版本 FAILED 仅显式重试（由上方 test_failed_same_version 覆盖）

    def test_retry_does_not_reprocess_done(self):
        items = _mk_items(["/a", "/b"])
        records = [
            _record_of(items[0], "FAILED", "", "e"),
            _record_of(items[1], "DONE", "ok"),
        ]
        selected = select_batch(items, records, 10, retry=True)
        assert [i.path for i in selected] == ["/a"]

    def test_limit_truncates_in_order(self):
        items = _mk_items([f"/p{i}" for i in range(10)])
        selected = select_batch(items, [], 3)
        assert [i.path for i in selected] == ["/p0", "/p1", "/p2"]

    def test_path_dedup(self):
        items = [Item("/a", 1, 100, "/pics"), Item("/a", 1, 100, "/pics")]
        assert [i.path for i in select_batch(items, [], 10)] == ["/a"]

    def test_limit_9000(self):
        items = _mk_items([f"/p{i}" for i in range(9500)])
        assert len(select_batch(items, [], 9000)) == 9000


# =========================================================================
# run_batch
# =========================================================================

class TestRunBatch:
    def test_success_empty_and_progress(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"AAA")
        b = _write_image(folder / "b.png", b"BBB")
        store = Store(tmp_path / "db.sqlite")
        items = [_item_for(a, folder), _item_for(b, folder)]

        texts = {"a.png": "梗图文字", "b.png": "　 \n"}  # 全角空格 => 空
        def recognize(item: Item) -> str:
            return texts[os.path.basename(item.path)]

        progress = run_batch(items, store, recognize, threading.Event(), _silent_publish)
        assert progress == Progress(total=2, processed=2, succeeded=1, empty=1)
        ra = store.get(canonical_path(a))
        rb = store.get(canonical_path(b))
        assert ra.status == "DONE" and ra.text == "梗图文字"
        assert rb.status == "EMPTY" and rb.text == ""

    def test_empty_text_means_empty_status(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        store = Store(tmp_path / "db.sqlite")
        progress = run_batch([_item_for(a, folder)], store,
                             lambda item: "\u3000\t ", threading.Event())
        assert progress.empty == 1
        assert store.get(canonical_path(a)).status == "EMPTY"

    def test_ocr_exception_marked_failed_and_continues(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        b = _write_image(folder / "b.png", b"B")
        store = Store(tmp_path / "db.sqlite")

        def recognize(item: Item) -> str:
            if os.path.basename(item.path) == "a.png":
                raise RuntimeError("ocr exploded")
            return "ok"

        progress = run_batch([_item_for(a, folder), _item_for(b, folder)],
                             store, recognize, threading.Event())
        assert progress.processed == 2
        assert progress.failed == 1 and progress.succeeded == 1
        ra = store.get(canonical_path(a))
        assert ra.status == "FAILED" and "ocr exploded" in ra.error
        assert store.get(canonical_path(b)).status == "DONE"

    def test_save_failure_bubbles_and_not_counted(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        store = Store(tmp_path / "db.sqlite")

        class BrokenStore:
            def save(self, record):
                raise sqlite3.OperationalError("disk full")

        def recognize(item: Item) -> str:
            return "text"

        with pytest.raises(sqlite3.OperationalError):
            run_batch([_item_for(a, folder)], BrokenStore(), recognize,
                      threading.Event())
        assert store.records() == []  # 没有误存

    def test_stat_change_before_recognize_marks_failed(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        store = Store(tmp_path / "db.sqlite")
        item = _item_for(a, folder)

        def recognize(rec_item: Item) -> str:
            a.write_bytes(b"changed-content")  # 识别开始前已被改
            return "should not be trusted"

        progress = run_batch([item], store, recognize, threading.Event())
        rec = store.get(canonical_path(a))
        assert progress.failed == 1
        assert rec.status == "FAILED" and rec.size == item.size
        # 旧成功结果没有保存成 DONE
        assert rec.text == "" and rec.status != "DONE"

    def test_delete_after_recognize_marks_failed(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        store = Store(tmp_path / "db.sqlite")
        item = _item_for(a, folder)

        def recognize(rec_item: Item) -> str:
            os.remove(a)  # 识别完成后文件消失
            return "text"

        progress = run_batch([item], store, recognize, threading.Event())
        rec = store.get(canonical_path(a))
        assert progress.failed == 1
        assert rec.status == "FAILED" and "删除" in rec.error

    def test_missing_before_recognize_marks_failed(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        store = Store(tmp_path / "db.sqlite")
        item = _item_for(a, folder)
        os.remove(a)  # 批次开始前就没了
        progress = run_batch([item], store, lambda i: "x", threading.Event())
        assert progress.failed == 1
        assert store.get(canonical_path(a)).status == "FAILED"

    def test_cancel_before_start(self, tmp_path):
        store = Store(tmp_path / "db.sqlite")
        cancel = threading.Event()
        cancel.set()
        progress = run_batch([], store, lambda i: "t", cancel)
        assert progress.cancelled and progress.total == 0

    def test_cancel_during_batch_saves_current_then_stops(self, tmp_path):
        folder = tmp_path / "pics"
        paths = [_write_image(folder / f"p{i:02d}.png", bytes([i])) for i in range(5)]
        store = Store(tmp_path / "db.sqlite")
        items = [_item_for(p, folder) for p in paths]
        cancel = threading.Event()
        seen = []

        def recognize(rec_item: Item) -> str:
            seen.append(rec_item.path)
            if len(seen) == 3:
                cancel.set()  # 第 3 张识别中请求取消
            return f"text-{len(seen)}"

        progress = run_batch(items, store, recognize, cancel)
        assert progress.cancelled is True
        assert progress.processed == 3  # 第 3 张完成后才停
        assert len(seen) == 3
        # 已识别的 3 张全部落库，可续做时不再重复
        done_paths = [r.path for r in store.records() if r.status == "DONE"]
        assert len(done_paths) == 3
        remaining = select_batch(items, store.records(), 100)
        assert len(remaining) == 2

    def test_resume_with_new_store_instance(self, tmp_path):
        folder = tmp_path / "pics"
        paths = [_write_image(folder / f"p{i}.png", bytes([i])) for i in range(4)]
        items = [_item_for(p, folder) for p in paths]
        db = tmp_path / "db.sqlite"

        store1 = Store(db)
        run_batch(items[:2], store1, lambda i: "第一轮", threading.Event())
        del store1

        store2 = Store(db)  # 重开续做
        remaining = select_batch(items, store2.records(), 1000)
        progress = run_batch(remaining, store2, lambda i: "第二轮", threading.Event())
        assert progress.processed == 2  # 只处理剩下两张
        recs = {r.path: r for r in store2.records()}
        assert recs[canonical_path(paths[0])].text == "第一轮"
        assert recs[canonical_path(paths[3])].text == "第二轮"

    def test_publish_called_each_item(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        b = _write_image(folder / "b.png", b"B")
        store = Store(tmp_path / "db.sqlite")
        events = []
        run_batch([_item_for(a, folder), _item_for(b, folder)], store,
                  lambda i: "t", threading.Event(), events.append)
        assert [p.processed for p in events] == [0, 1, 2]
        assert events[-1].total == 2

    @pytest.mark.skipif(os.name == "nt", reason="POSIX chmod; Windows permission semantics are tested with simulated denial")
    def test_root_permission_lost_stops_batch(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        b = _write_image(folder / "b.png", b"B")
        store = Store(tmp_path / "db.sqlite")
        items = [_item_for(a, folder), _item_for(b, folder)]
        calls = []

        def recognize(rec_item: Item) -> str:
            calls.append(rec_item.path)
            if len(calls) == 1:
                folder.chmod(0o000)  # 整个根目录权限丢失
            return "t"

        try:
            progress = run_batch(items, store, recognize, threading.Event())
            assert progress.error != ""
            assert progress.processed == 1  # 第 2 张前停止
        finally:
            folder.chmod(0o755)
        assert len(calls) == 1

    def test_single_file_permission_error_only_fails_that_file(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        b = _write_image(folder / "b.png", b"B")
        store = Store(tmp_path / "db.sqlite")
        items = [_item_for(a, folder), _item_for(b, folder)]

        def recognize(rec_item: Item) -> str:
            if os.path.basename(rec_item.path) == "a.png":
                a.chmod(0o000)  # 单张不可读，但根目录仍可访问
                raise PermissionError(13, " Permission denied")
            return "t"

        try:
            progress = run_batch(items, store, recognize, threading.Event())
        finally:
            a.chmod(0o644)
        assert progress.failed == 1 and progress.succeeded == 1
        assert progress.processed == 2  # 单图失败不阻止其他图片

    def test_never_writes_into_image_folder(self, tmp_path):
        folder = tmp_path / "pics"
        a = _write_image(folder / "a.png", b"A")
        store = Store(tmp_path / "db.sqlite")
        run_batch([_item_for(a, folder)], store, lambda i: "t",
                  threading.Event())
        assert [p.name for p in folder.iterdir()] == ["a.png"]

    def test_9000_items_single_pass(self, tmp_path):
        """9000 张分 9 批（每批 1000），每图恰好识别一次。"""
        folder = tmp_path / "pics"
        paths = [_write_image(folder / f"p{i:05d}.png", bytes([i % 251]))
                 for i in range(9000)]
        items = [_item_for(p, folder) for p in paths]
        store = Store(tmp_path / "db.sqlite")
        counts = []

        def recognize(rec_item: Item) -> str:
            counts.append(rec_item.path)
            return f"文字 {os.path.basename(rec_item.path)}"

        for start in range(0, 9000, 1000):
            records = store.records()
            batch = select_batch(items, records, 1000)
            progress = run_batch(batch, store, recognize, threading.Event(),
                                 _silent_publish)
            assert progress.processed == len(batch)

        assert len(counts) == 9000
        assert len(set(counts)) == 9000  # 每图恰好一次
        # 全部完成后不再选任何图
        assert select_batch(items, store.records(), 1000) == []


# =========================================================================
# search_records
# =========================================================================

class TestSearchRecords:
    def _setup(self, tmp_path, recurse=False):
        folder = tmp_path / "pics"
        imgs = {
            "a.png": _write_image(folder / "a.png", b"A"),
            "b.png": _write_image(folder / "b.png", b"B"),
            "c.png": _write_image(folder / "c.png", b"C"),
        }
        store = Store(tmp_path / "db.sqlite")
        store = Store(tmp_path / "db.sqlite")
        store.add_folder(canonical_path(folder), recurse=recurse)
        items = [_item_for(p, folder) for p in imgs.values()]
        texts = {
            canonical_path(imgs["a.png"]): "今天也要加油鸭！Ｇｏ！",
            canonical_path(imgs["b.png"]): "Hello World",
            canonical_path(imgs["c.png"]): "第二行\n猫猫图\n第三行",
        }
        run_batch(items, store, lambda i: texts[i.path], threading.Event())
        return folder, store, imgs, items

    def test_literal_basic_and_normalize(self, tmp_path):
        folder, store, imgs, items = self._setup(tmp_path)
        # 全角查询命中半角正文
        hits = search_records(store, "加油鸭！ｇｏ")
        assert [os.path.basename(h.path) for h in hits] == ["a.png"]
        # 大小写不敏感
        assert [h.path for h in search_records(store, "hello world")] == \
            [canonical_path(imgs["b.png"])]
        # whitespace 折叠匹配
        assert search_records(store, "hello   world")

    def test_literal_empty_query_returns_empty(self, tmp_path):
        folder, store, _, _ = self._setup(tmp_path)
        assert search_records(store, "") == []
        assert search_records(store, "   \u3000 ") == []

    def test_literal_metacharacters_are_literal(self, tmp_path):
        folder, store, imgs, items = self._setup(tmp_path)
        # 正文里有 "Hello World"，点号按文字处理不会匹配
        assert search_records(store, "Hello.World") == []
        # 括号、加号等元字符按文字匹配
        assert search_records(store, "鸭！Ｇｏ！")  # 全角标点正常匹配

    def test_regex_multiline_and_case(self, tmp_path):
        folder, store, imgs, items = self._setup(tmp_path)
        assert search_records(store, "hello", regex=True) == []
        assert search_records(store, "HELLO", regex=True) == []
        assert [h.path for h in search_records(store, "Hello", regex=True)] == \
            [canonical_path(imgs["b.png"])]
        # . 不跨行
        assert search_records(store, "第二行.猫猫", regex=True) == []
        # 锚点 + 换行 alternation
        assert search_records(store, "^猫猫图$", regex=True) == []
        assert [h.path for h in search_records(store, "(?m)^猫猫图$", regex=True)] == \
            [canonical_path(imgs["c.png"])]

    def test_regex_empty_raises(self, tmp_path):
        folder, store, _, _ = self._setup(tmp_path)
        with pytest.raises(ValueError):
            search_records(store, "", regex=True)

    def test_regex_unsupported_syntax_raises_chinese(self, tmp_path):
        folder, store, _, _ = self._setup(tmp_path)
        for bad in ["(?=x)", "(?!x)", r"(?<=x)y", r"(?<!x)y", r"(a)\1"]:
            with pytest.raises(ValueError) as exc:
                search_records(store, bad, regex=True)
            assert "不支持" in str(exc.value)

    def test_regex_pathological_pattern_is_linear(self, tmp_path):
        folder, store, _, items = self._setup(tmp_path)
        import time
        start = time.monotonic()
        # RE2 线性时间：病态模式不挂起
        assert search_records(store, "(a+)+$", regex=True) == []
        assert time.monotonic() - start < 5.0

    def test_regex_bad_syntax_literal_fine(self, tmp_path):
        folder, store, _, _ = self._setup(tmp_path)
        with pytest.raises(ValueError):
            search_records(store, "a**", regex=True)
        # literal 模式下 a** 就是一段普通文字，不报错
        assert search_records(store, "a**") == []

    def test_deleted_file_excluded(self, tmp_path):
        folder, store, imgs, _ = self._setup(tmp_path)
        os.remove(imgs["a.png"])
        hits = search_records(store, "加油鸭")
        assert hits == []

    def test_version_changed_excluded(self, tmp_path):
        folder, store, imgs, _ = self._setup(tmp_path)
        with open(imgs["a.png"], "wb") as fh:
            fh.write(b"NEW CONTENT")  # size/mtime 变化
        assert search_records(store, "加油鸭") == []

    def test_unregistered_folder_history_excluded(self, tmp_path):
        folder, store, imgs, _ = self._setup(tmp_path)
        # 换一个新的 Store，不登记任何目录 → 历史记录全部排除
        fresh = Store(tmp_path / "other.sqlite")
        for r in store.records():
            fresh.save(r)
        assert search_records(fresh, "加油鸭") == []
        fresh.add_folder("/somewhere/else")
        assert search_records(fresh, "加油鸭") == []

    def test_recursion_narrows_deep_history(self, tmp_path):
        folder, store, imgs, items = self._setup(tmp_path, recurse=False)
        # 先以 recurse=False 登记，但记录的文件在根目录，能搜到
        assert search_records(store, "加油鸭")
        deep = _write_image(folder / "sub" / "deep.png", b"D")
        deep_item = _item_for(deep, folder)
        run_batch([deep_item], store, lambda i: "深层图片加油鸭", threading.Event())
        # recurse=False：深层历史图片被排除
        hits = search_records(store, "深层图片")
        assert hits == []
        # 收窄前若曾以 recurse=True 登记则可以搜到
        store.add_folder(canonical_path(folder), recurse=True)
        hits = search_records(store, "深层图片")
        assert [os.path.basename(h.path) for h in hits] == ["deep.png"]

    def test_non_recursive_deep_record_excluded_after_narrow(self, tmp_path):
        folder, store, imgs, _ = self._setup(tmp_path, recurse=False)
        deep = _write_image(folder / "sub" / "d2.png", b"D")
        deep_item = _item_for(deep, folder)
        run_batch([deep_item], store, lambda i: "deep text hello", threading.Event())
        store.add_folder(canonical_path(folder), recurse=True)
        assert search_records(store, "deep text")
        store.add_folder(canonical_path(folder), recurse=False)  # 收窄
        assert search_records(store, "deep text") == []
        # 根目录直接子文件仍可搜到
        assert search_records(store, "Hello World")

    def test_only_done_with_text_returned(self, tmp_path):
        folder = tmp_path / "pics"
        empty_img = _write_image(folder / "e.png", b"E")
        store = Store(tmp_path / "db.sqlite")
        store.add_folder(canonical_path(folder))
        run_batch([_item_for(empty_img, folder)], store,
                  lambda i: "  \u3000", threading.Event())
        assert search_records(store, "e") == []
        assert search_records(store, ".", regex=True) == []

    def test_results_sorted_by_path(self, tmp_path):
        folder, store, imgs, _ = self._setup(tmp_path)
        store2_records = search_records(store, "o")  # Hello World / 加油鸭！Ｇｏ！
        paths = [h.path for h in store2_records]
        assert paths == sorted(paths)
        assert len(paths) == 2
