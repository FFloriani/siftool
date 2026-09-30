import pytest
from siftool import detect
from siftool.errors import UnsupportedFormatError

class Fake:
    name, extensions = "fake", (".fk",)
    def sniff(self, head): return head.startswith(b"FAKE")

@pytest.mark.parametrize("head,name", [
    (b"GIF89a....", "GIF"), (b"II*\x00....", "TIFF"), (b"BM......", "BMP"),
    (b"\x00\x00\x00\x18ftypheic", "HEIC"), (b"\x00\x00\x00\x18ftypavif", "AVIF"),
    (b"\x00\x00\x00\x18ftypisom", "MP4/MOV"), (b"\x00\x00\x00\x08wide", "MP4/MOV"),
    (b"\x1aE\xdf\xa3....", "MKV/WebM"), (b"RIFF\x00\x00\x00\x00AVI ", "AVI"), (b"%PDF-1.7", "PDF")])
def test_known_unsupported_formats_are_named(head, name):
    with pytest.raises(UnsupportedFormatError) as e: detect.detect(head)
    assert e.value.format_name == name and str(e.value) == f"{name} is not supported yet"

def test_unknown_bytes():
    with pytest.raises(UnsupportedFormatError) as e: detect.detect(b"hello world")
    assert e.value.format_name is None

def test_registered_handler_wins_and_extensions_are_listed():
    h = detect.register(Fake())
    try:
        assert detect.detect(b"FAKE...") is h and ".fk" in detect.supported_extensions()
    finally:
        detect.unregister(h)
