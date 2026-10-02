import hashlib

import pytest
from PIL import Image, UnidentifiedImageError

from memeocr import images
from memeocr.ocr import Recognizer


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L", "P"])
def test_supported_pixel_modes_and_read_only(tmp_path, mode):
    path = tmp_path / "图片.png"
    Image.new(mode, (40, 20)).save(path)
    before = digest(path)
    with images.read_image(path, 10) as decoded:
        assert decoded.mode == "RGBA"
        assert decoded.size == (10, 5)
    assert digest(path) == before
    assert list(tmp_path.iterdir()) == [path]


def test_exif_rotation_is_applied_before_bounding(tmp_path):
    path = tmp_path / "rotated.jpg"
    source = Image.new("RGB", (10, 30), "red")
    exif = source.getexif()
    exif[274] = 6
    source.save(path, exif=exif)
    before = digest(path)
    with images.read_image(path, 15) as decoded:
        assert decoded.size == (15, 5)
    assert digest(path) == before


def test_animated_image_uses_first_frame(tmp_path):
    path = tmp_path / "animated.gif"
    first = Image.new("RGB", (20, 20), "red")
    second = Image.new("RGB", (20, 20), "lime")
    first.save(path, save_all=True, append_images=[second], duration=100, loop=0)
    before = digest(path)
    with images.read_image(path) as decoded:
        assert decoded.getpixel((0, 0))[:3] == (255, 0, 0)
    assert digest(path) == before


def test_full_resolution_clipboard_decode_preserves_size_and_alpha(tmp_path):
    path = tmp_path / "alpha.png"
    Image.new("RGBA", (320, 100), (10, 20, 30, 40)).save(path)
    with images.read_image(path, None) as decoded:
        assert decoded.size == (320, 100)
        assert decoded.getpixel((0, 0)) == (10, 20, 30, 40)


def test_pixel_limit_is_checked_before_decode(tmp_path, monkeypatch):
    path = tmp_path / "large.png"
    Image.new("RGB", (20, 20)).save(path)
    monkeypatch.setattr(images, "MAX_PIXELS", 399)
    with pytest.raises(ValueError, match="3200"):
        images.read_image(path)


@pytest.mark.parametrize("size", [0, -1])
def test_invalid_decode_bound(tmp_path, size):
    path = tmp_path / "valid.png"
    Image.new("RGB", (5, 5)).save(path)
    with pytest.raises(ValueError):
        images.read_image(path, size)


def test_corrupt_image_does_not_create_files(tmp_path):
    path = tmp_path / "corrupt.png"
    path.write_bytes(b"not an image")
    with pytest.raises(UnidentifiedImageError):
        images.read_image(path)
    assert path.read_bytes() == b"not an image"
    assert list(tmp_path.iterdir()) == [path]


def test_ocr_receives_bgr_with_alpha_on_white(tmp_path):
    path = tmp_path / "red.png"
    Image.new("RGBA", (20, 20), (255, 0, 0, 255)).save(path)
    recognizer = Recognizer.__new__(Recognizer)
    def fake_engine(array):
        assert tuple(array[0, 0]) == (0, 0, 255)
        return [[[], "猫猫", 1.0], [[], "开心", 1.0]], []
    recognizer.engine = fake_engine
    from types import SimpleNamespace

    assert recognizer(SimpleNamespace(path=str(path))) == "猫猫\n开心"


def test_ocr_empty_response_is_successful_empty_text(tmp_path):
    path = tmp_path / "empty.png"
    Image.new("RGB", (20, 20)).save(path)
    recognizer = Recognizer.__new__(Recognizer)
    recognizer.engine = lambda array: (None, [])
    from types import SimpleNamespace

    assert recognizer(SimpleNamespace(path=str(path))) == ""
