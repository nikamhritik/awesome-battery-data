"""SQLite 存储层：登记目录、按版本记录 OCR 结果、轻量设置项。

约定：
- 数据库表在首次构造 Store 时创建，启用 WAL 以便线程并发读写。
- 每个操作使用独立的短连接，操作结束立即 close（sqlite3.connect 的
  上下文管理器只管理事务，不负责关闭连接，因此不能只依赖 with）。
- Store 自身绝不 stat 原图；path/folder 的规范化由 core.canonical_path
  在调用方完成，这里按收到的字符串原样存取。
- 异常向上冒泡，交由 UI 展示，不在本层吞掉。
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    path        TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    folder      TEXT NOT NULL,
    status      TEXT NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_records_folder ON records(folder);

CREATE TABLE IF NOT EXISTS folders (
    path     TEXT PRIMARY KEY,
    recurse  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Item:
    """一次扫描得到的待处理图片（版本 = path + size + modified_ns）。"""

    path: str
    size: int
    modified_ns: int
    folder: str


@dataclass(frozen=True)
class Record:
    """一条已落库的 OCR 结果。status 仅允许 DONE / EMPTY / FAILED。"""

    path: str
    size: int
    modified_ns: int
    folder: str
    status: str
    text: str = ""
    error: str = ""

    def matches(self, item: Item) -> bool:
        """版本匹配：只比较 path/size/modified_ns，folder 不构成版本。"""
        return (
            self.path == item.path
            and self.size == item.size
            and self.modified_ns == item.modified_ns
        )


class Store:
    """线程安全的 SQLite 封装：读写走各自独立的短连接。"""

    def __init__(self, db_path: "Path | str") -> None:
        self._db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        conn = self._connect()
        try:
            with conn:
                conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    # ---- records ----

    def get(self, path: str) -> "Record | None":
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT path, size, modified_ns, folder, status, text, error "
                "FROM records WHERE path = ?",
                (path,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return Record(*row)

    def save(self, record: Record) -> None:
        """单条记录立即提交，识别过程中断也不丢已完成结果。"""
        if record.status not in ("DONE", "EMPTY", "FAILED"):
            raise ValueError(f"非法 status：{record.status!r}")
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO records(path, size, modified_ns, folder, status, text, error) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET "
                        "size = excluded.size, modified_ns = excluded.modified_ns, "
                        "folder = excluded.folder, status = excluded.status, "
                        "text = excluded.text, error = excluded.error",
                        (
                            record.path,
                            record.size,
                            record.modified_ns,
                            record.folder,
                            record.status,
                            record.text,
                            record.error,
                        ),
                    )
            finally:
                conn.close()

    def records(self, folder: "str | None" = None) -> "list[Record]":
        conn = self._connect()
        try:
            if folder is None:
                rows = conn.execute(
                    "SELECT path, size, modified_ns, folder, status, text, error "
                    "FROM records ORDER BY path"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT path, size, modified_ns, folder, status, text, error "
                    "FROM records WHERE folder = ? ORDER BY path",
                    (folder,),
                ).fetchall()
        finally:
            conn.close()
        return [Record(*row) for row in rows]

    # ---- folders ----

    def add_folder(self, path: str, recurse: bool = False) -> None:
        """upsert 登记根目录及其递归标志。"""
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO folders(path, recurse) VALUES (?, ?) "
                        "ON CONFLICT(path) DO UPDATE SET recurse = excluded.recurse",
                        (path, 1 if recurse else 0),
                    )
            finally:
                conn.close()

    def folders(self) -> "list[tuple[str, bool]]":
        """返回 (登记根目录, 是否递归) 列表，按 path 排序保证确定顺序。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT path, recurse FROM folders ORDER BY path"
            ).fetchall()
        finally:
            conn.close()
        return [(path, bool(recurse)) for path, recurse in rows]

    # ---- settings ----

    def get_setting(self, key: str, default: str = "") -> str:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        finally:
            conn.close()
        return default if row is None else row[0]

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._connect()
            try:
                with conn:
                    conn.execute(
                        "INSERT INTO settings(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, value),
                    )
            finally:
                conn.close()
