import threading
from pathlib import Path

from memeocr import core
from memeocr.storage import Item, Record, Store


def test_retry_excludes_fresh_and_changed_success():
    items = [Item("/new", 1, 1, "/"), Item("/done", 2, 2, "/"), Item("/failed", 2, 2, "/")]
    records = [Record("/done", 1, 1, "/", "DONE", "old"),
               Record("/failed", 1, 1, "/", "FAILED", error="bad")]
    assert core.select_batch(items, records, 1000, True) == [items[2]]
    assert core.select_batch(items, records, 1000) == items


def test_modification_before_recognition_saves_actual_version(tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"first")
    candidate = core.stat_item(source, tmp_path)
    source.write_bytes(b"changed before processing")
    current = core.stat_item(source, tmp_path)
    store = Store(tmp_path / "cache.db")
    core.run_batch([candidate], store, lambda item: "new text", threading.Event())
    assert store.get(current.path).matches(current)
    assert core.select_batch([current], store.records(), 1) == []


def test_cancel_on_last_image_is_reported_and_saved(tmp_path):
    source = tmp_path / "photo.png"
    source.write_bytes(b"first")
    item = core.stat_item(source, tmp_path)
    store = Store(tmp_path / "cache.db")
    cancel = threading.Event()
    def recognize(current):
        cancel.set()
        return "finished"
    result = core.run_batch([item], store, recognize, cancel)
    assert result.cancelled and result.succeeded == 1
    assert store.get(item.path).text == "finished"


def test_root_access_denial_stops_after_saving_current_failure(tmp_path, monkeypatch):
    sources = [tmp_path / name for name in ("a.png", "b.png")]
    for path in sources:
        path.write_bytes(b"image")
    items = [core.stat_item(path, tmp_path) for path in sources]
    store = Store(tmp_path / "cache.db")
    real_stat = core.stat_item
    denied = threading.Event()
    def stat(path, folder):
        if denied.is_set():
            raise PermissionError("simulated directory ACL denial")
        return real_stat(path, folder)
    def recognize(item):
        denied.set()
        return "untrusted"
    monkeypatch.setattr(core, "stat_item", stat)
    monkeypatch.setattr(core, "_folder_accessible", lambda folder: not denied.is_set())
    result = core.run_batch(items, store, recognize, threading.Event())
    assert result.processed == 1 and result.failed == 1
    assert "相册不可访问" in result.error
    assert store.get(items[0].path).status == "FAILED"
    assert store.get(items[1].path) is None


def test_search_rejects_record_outside_registered_recursive_folder(tmp_path):
    folder = tmp_path / "inside"
    folder.mkdir()
    source = tmp_path / "outside.png"
    source.write_bytes(b"image")
    item = core.stat_item(source, folder)
    store = Store(tmp_path / "cache.db")
    store.add_folder(core.canonical_path(folder), True)
    store.save(Record(item.path, item.size, item.modified_ns, item.folder, "DONE", "猫猫"))
    assert core.search_records(store, "猫猫") == []
