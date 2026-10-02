"""storage.Store 的测试：records upsert、并发、Unicode 路径、设置、隔离。"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from memeocr.storage import Item, Record, Store


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "cache.db")


def _record(path: str, size: int = 10, modified_ns: int = 100,
            folder: str = "/pics", status: str = "DONE",
            text: str = "", error: str = "") -> Record:
    return Record(path, size, modified_ns, folder, status, text, error)


def _item(path: str, size: int = 10, modified_ns: int = 100,
          folder: str = "/pics") -> Item:
    return Item(path, size, modified_ns, folder)


# ---------- 建库 / WAL ----------

def test_init_creates_tables_and_wal(tmp_path):
    db = tmp_path / "nested" / "cache.db"
    db.parent.mkdir()
    store = Store(db)
    conn = sqlite3.connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"records", "folders", "settings"} <= tables
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


# ---------- get / save / upsert ----------

def test_get_missing_returns_none(store):
    assert store.get("/pics/a.png") is None


def test_save_and_get_roundtrip(store):
    rec = _record("/pics/a.png", 5, 7, "/pics", "DONE", "文本")
    store.save(rec)
    got = store.get("/pics/a.png")
    assert got == rec


def test_save_upserts_same_path(store):
    store.save(_record("/pics/a.png", 5, 7, status="DONE", text="旧"))
    new = _record("/pics/a.png", 8, 9, status="EMPTY")
    store.save(new)
    assert store.get("/pics/a.png") == new
    assert len(store.records()) == 1  # 同 path 是更新而不是新增


def test_save_rejects_bad_status(store):
    with pytest.raises(ValueError):
        store.save(_record("/pics/a.png", status="PARTIAL"))


def test_record_matches_only_version_fields():
    rec = _record("/pics/a.png", 5, 7, folder="/other")
    assert rec.matches(_item("/pics/a.png", 5, 7, folder="/pics"))
    assert not rec.matches(_item("/pics/a.png", 6, 7))
    assert not rec.matches(_item("/pics/a.png", 5, 8))
    assert not rec.matches(_item("/pics/b.png", 5, 7))


# ---------- records(folder) ----------

def test_records_filter_and_order(store):
    store.save(_record("/pics/b.png", folder="/pics"))
    store.save(_record("/pics/a.png", folder="/pics"))
    store.save(_record("/else/c.png", folder="/else"))
    paths = [r.path for r in store.records("/pics")]
    assert paths == ["/pics/a.png", "/pics/b.png"]
    assert len(store.records()) == 3
    assert [r.path for r in store.records("/else")] == ["/else/c.png"]


# ---------- folders ----------

def test_folders_upsert_and_order(store):
    assert store.folders() == []
    store.add_folder("/b", recurse=True)
    store.add_folder("/a")
    assert store.folders() == [("/a", False), ("/b", True)]
    store.add_folder("/a", recurse=True)  # upsert 覆盖 recurse 标志
    assert store.folders() == [("/a", True), ("/b", True)]


# ---------- settings ----------

def test_settings_default_and_upsert(store):
    assert store.get_setting("batch") == ""
    assert store.get_setting("batch", "1000") == "1000"
    store.set_setting("batch", "500")
    assert store.get_setting("batch") == "500"
    store.set_setting("batch", "200")
    assert store.get_setting("batch") == "200"


# ---------- Unicode 路径 ----------

def test_unicode_paths_roundtrip(store):
    rec = _record("/图片/梗图/猫猫😂.png", 3, 4, "/图片", "DONE", "喵")
    store.save(rec)
    assert store.get("/图片/梗图/猫猫😂.png") == rec
    assert [r.path for r in store.records("/图片")] == ["/图片/梗图/猫猫😂.png"]


# ---------- 并发读写 ----------

def test_concurrent_writers_all_persist(store):
    def writer(base: int) -> None:
        for i in range(20):
            store.save(
                _record(f"/pics/w{base}-{i}.png", i, i, status="EMPTY")
            )

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.records()) == 80


def test_concurrent_readers_and_writer(store):
    for i in range(10):
        store.save(_record(f"/pics/seed{i}.png", status="DONE", text=f"t{i}"))

    stop = threading.Event()
    errors: list[Exception] = []

    def reader() -> None:
        try:
            while not stop.is_set():
                assert store.get("/pics/seed0.png") is not None
                store.records()
                store.folders()
                store.get_setting("k")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def writer() -> None:
        try:
            for i in range(50):
                store.save(_record(f"/pics/new{i}.png", status="EMPTY"))
                store.set_setting("k", str(i))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(3)]
    w = threading.Thread(target=writer)
    for t in readers:
        t.start()
    w.start()
    w.join()
    stop.set()
    for t in readers:
        t.join()
    assert errors == []
    assert len(store.records()) == 60


# ---------- 独立短连接（不长期占用连接） ----------

def test_connections_are_closed_after_operations(store, tmp_path, monkeypatch):
    opened = []
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr("memeocr.storage.sqlite3.connect", counting_connect)
    store2 = Store(tmp_path / "c2.db")
    store2.save(_record("/x.png"))
    store2.get("/x.png")
    store2.records()
    store2.folders()
    store2.add_folder("/f")
    store2.get_setting("k")
    # 所有连接都应已被 close（in_memory 计数为 0）
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            _ = conn.total_changes
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


# ---------- db 不落在图片目录 ----------

def test_db_lives_outside_image_folder(tmp_path):
    pics = tmp_path / "pics"
    pics.mkdir()
    store = Store(tmp_path / "db" / "cache.db")
    store.add_folder(str(pics))
    store.save(_record(str(pics / "a.png")))
    assert list(pics.iterdir()) == []  # 图片目录保持干净
