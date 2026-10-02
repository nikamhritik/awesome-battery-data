"""Application entry point and verification entry point for the frozen executable."""

import argparse
import os
import sys
from pathlib import Path

# Third-party libraries can write diagnostics even in a PyInstaller windowed app.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


def data_directory() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "WhereIsMyMeme"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", type=Path, metavar="REPORT_JSON")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv[:1])
    app.setApplicationName("WhereIsMyMeme")
    app.setOrganizationName("WhereIsMyMeme")
    if args.self_test:
        from memeocr.selftest import self_test

        return self_test(args.self_test, app)
    from memeocr.storage import Store
    from memeocr.ui import MainWindow

    directory = data_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(directory / "application.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            QMessageBox.information(None, "Where's My Meme", "应用已经在运行，请切换到已有窗口。")
            return 0
        store = Store(directory / "cache.sqlite3")
        folders = store.folders()
        selected = store.get_setting("selected")
        try:
            batch_size = int(store.get_setting("batch_size", "1000"))
        except ValueError:
            batch_size = 1000
        window = MainWindow(store, folders, selected, min(10000, max(1, batch_size)))
        window.show()
        if args.screenshot:
            def screenshot():
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.screenshot))
                window.close()
            QTimer.singleShot(1200, screenshot)
        result = app.exec()
        lock.unlock()
        return result
    except Exception as error:
        QMessageBox.critical(None, "无法启动", f"无法读取应用缓存：{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
