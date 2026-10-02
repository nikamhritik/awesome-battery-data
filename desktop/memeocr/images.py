"""Bounded, read-only image decoding. Animated images use their first frame."""

from pathlib import Path

from PIL import Image, ImageOps

MAX_PIXELS = 32_000_000


def read_image(path: str | Path, max_side: int | None = 2048) -> Image.Image:
    with open(path, "rb") as stream, Image.open(stream) as original:
        original.seek(0)
        if original.width * original.height > MAX_PIXELS:
            raise ValueError("图片超过 3200 万像素，请选择较小的图片")
        if max_side is not None:
            if max_side < 1:
                raise ValueError("图片解码尺寸须为正数")
            original.draft("RGB", (max_side, max_side))
        image = ImageOps.exif_transpose(original).convert("RGBA")
        if max_side is not None:
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return image


def load_qimage(path: str, max_side: int | None = 2048):
    from PySide6.QtGui import QImage

    with read_image(path, max_side) as bitmap:
        return QImage(
            bitmap.tobytes(), bitmap.width, bitmap.height,
            bitmap.width * 4, QImage.Format.Format_RGBA8888,
        ).copy()
