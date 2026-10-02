"""Read-only enumeration, resumable recognition and linear-time text search."""

import os
import re
import stat
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import re2

from .storage import Item, Record

SUPPORTED_EXTENSIONS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff'})
_REGEX_OPTIONS = re2.Options()
_REGEX_OPTIONS.log_errors = False


def canonical_path(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def stat_item(path, folder):
    info = os.stat(path)
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f'不是常规文件：{path}')
    with open(path, 'rb') as stream:
        stream.read(0)
    return Item(canonical_path(path), info.st_size, info.st_mtime_ns, canonical_path(folder))


def scan_folder(folder, recurse=False):
    root = canonical_path(folder)
    pending = [root]
    items = []
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if recurse and not Path(entry.path).is_junction():
                            pending.append(entry.path)
                    elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in SUPPORTED_EXTENSIONS:
                        items.append(stat_item(entry.path, root))
                except OSError:
                    continue
    return sorted(items, key=lambda item: item.path)


def normalize(text):
    converted = ''.join(chr(ord(ch) - 0xFEE0) if '\uff01' <= ch <= '\uff5e' else
                        ' ' if ch == '\u3000' else ch for ch in text)
    return re.sub(r'\s+', ' ', converted).strip().lower()


def select_batch(items, records, limit, retry=False):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError('本批数量须为 1–10000')
    saved = {record.path: record for record in records}
    selected, seen = [], set()
    for item in items:
        if item.path in seen:
            continue
        seen.add(item.path)
        record = saved.get(item.path)
        eligible = (record is not None and record.status == 'FAILED') if retry else (
            record is None or not record.matches(item))
        if eligible:
            selected.append(item)
            if len(selected) == limit:
                break
    return selected


@dataclass(frozen=True)
class Progress:
    total: int
    processed: int = 0
    succeeded: int = 0
    empty: int = 0
    failed: int = 0
    cancelled: bool = False
    error: str = ''


def _folder_accessible(folder):
    try:
        with os.scandir(folder):
            return True
    except OSError:
        return False


def run_batch(batch, store, recognize, cancel: threading.Event, publish=None):
    progress = Progress(len(batch))
    if publish:
        publish(progress)
    for candidate in batch:
        if cancel.is_set():
            break
        current = candidate
        stop = False
        try:
            current = stat_item(candidate.path, candidate.folder)
            text = recognize(current)
            if stat_item(current.path, current.folder) != current:
                raise OSError('识别期间图片被修改或删除，请重试')
            record = Record(current.path, current.size, current.modified_ns, current.folder,
                            'DONE' if normalize(text) else 'EMPTY', text if normalize(text) else '')
        except Exception as error:
            stop = isinstance(error, OSError) and not _folder_accessible(current.folder)
            if isinstance(error, FileNotFoundError):
                message = '图片已删除或不可访问，请刷新相册'
            else:
                message = str(error) or type(error).__name__
            if stop:
                message = f'相册不可访问：{message}'
            record = Record(current.path, current.size, current.modified_ns, current.folder,
                            'FAILED', '', message)
        store.save(record)
        progress = replace(progress, processed=progress.processed + 1,
                           succeeded=progress.succeeded + (record.status == 'DONE'),
                           empty=progress.empty + (record.status == 'EMPTY'),
                           failed=progress.failed + (record.status == 'FAILED'),
                           error=record.error or progress.error)
        if publish:
            publish(progress)
        if stop:
            break
    final = replace(progress, cancelled=cancel.is_set())
    if publish and final != progress:
        publish(final)
    return final


def search_records(store, query, regex=False):
    if regex:
        if not query:
            raise ValueError('正则表达式不能为空')
        try:
            compiled = re2.compile(query, options=_REGEX_OPTIONS)
        except re2.error as error:
            raise ValueError('正则表达式无效：RE2 不支持前后查找（lookaround）和反向引用等语法。') from error
        matches = lambda text: bool(text) and compiled.search(text) is not None
    else:
        needle = normalize(query)
        if not needle:
            return []
        matches = lambda text: needle in normalize(text)
    folders = dict(store.folders())
    hits = []
    for record in store.records():
        if record.status != 'DONE' or record.folder not in folders or not matches(record.text):
            continue
        parent = canonical_path(Path(record.path).parent)
        if not folders[record.folder] and parent != record.folder:
            continue
        try:
            if os.path.commonpath([record.path, record.folder]) != record.folder:
                continue
            if record.matches(stat_item(record.path, record.folder)):
                hits.append(record)
        except (OSError, ValueError):
            continue
    return hits
