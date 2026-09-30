"""Tests for the checks inside `siftool_gui.self_test` (the hidden `--self-test` the build runs on the frozen program).

The whole run -- window, clean, report -- is tested in `test_window.py`, in a process of its own. Here are the
pieces that must fail when something the program needs is missing from the bundle: the image plugins and the
place a portable copy keeps its settings (release prep).
"""

from __future__ import annotations

import io

import pytest
from PySide6.QtCore import QByteArray
from PySide6.QtGui import QImageReader

from siftool_gui import self_test


def _solid(kind: str, color: tuple[int, int, int], size: int = 8) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, kind)
    return buffer.getvalue()


def test_the_tiny_photos_are_an_8_by_8_jpeg_and_webp_of_the_color_the_check_expects():
    Image = pytest.importorskip("PIL.Image")

    for data, kind in ((self_test.TINY_JPEG, "JPEG"), (self_test.TINY_WEBP, "WEBP")):
        image = Image.open(io.BytesIO(data))
        assert image.format == kind and image.size == (self_test.TINY_SIZE,) * 2
        red, green, blue = image.convert("RGB").getpixel((4, 4))
        assert all(abs(got - want) <= 24 for got, want in zip((red, green, blue), self_test.TINY_COLOR))


def test_the_image_check_decodes_a_jpeg_and_a_webp_through_qt(qapp):
    self_test._check_image_formats()  # the plugins of this environment do it


@pytest.mark.parametrize("name", ["jpeg", "webp"])
def test_a_photo_that_does_not_decode_fails_the_self_test(qapp, monkeypatch, name):
    # A plugin that is listed but cannot decode -- or whose library did not come along -- fails in the build,
    # not in a user's window.
    monkeypatch.setattr(self_test, f"TINY_{name.upper()}", b"not an image")

    with pytest.raises(self_test.SelfTestError, match=name):
        self_test._check_image_formats()


@pytest.mark.parametrize(("name", "kind"), [("jpeg", "JPEG"), ("webp", "WEBP")])
def test_a_decoder_that_gives_another_image_fails_the_self_test(qapp, monkeypatch, name, kind):
    blue = _solid(kind, (0, 0, 255))
    monkeypatch.setattr(self_test, f"TINY_{name.upper()}", blue)
    with pytest.raises(self_test.SelfTestError, match=f"{name}.*color"):
        self_test._check_image_formats()

    bigger = _solid(kind, self_test.TINY_COLOR, size=16)
    monkeypatch.setattr(self_test, f"TINY_{name.upper()}", bigger)
    with pytest.raises(self_test.SelfTestError, match=f"{name}.*16x16"):
        self_test._check_image_formats()


def test_a_format_missing_from_qt_still_fails_the_self_test(qapp, monkeypatch):
    monkeypatch.setattr(QImageReader, "supportedImageFormats", staticmethod(lambda: [QByteArray(b"png")]))

    with pytest.raises(self_test.SelfTestError, match="image formats missing: jpeg, webp, ico"):
        self_test._check_image_formats()


def test_the_portable_settings_check_writes_the_file_where_it_was_told(tmp_path):
    config = tmp_path / "dados" / "config.json"

    self_test._check_portable_settings(config)

    assert config.is_file()


def test_the_portable_settings_check_fails_when_the_folder_cannot_be_written(unwritable_config):
    # `settings.save` no longer raises on a read-only folder, so the check has to ask: a file left by an earlier
    # run would otherwise make a copy on read-only media pass.
    with pytest.raises(self_test.SelfTestError, match="portable settings"):
        self_test._check_portable_settings(unwritable_config)
