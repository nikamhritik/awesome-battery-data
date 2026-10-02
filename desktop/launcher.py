"""PyInstaller launch script; package imports also work with python -m memeocr."""

from memeocr.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
