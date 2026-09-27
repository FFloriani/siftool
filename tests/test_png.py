import io, zlib, pytest
from siftool.model import Category as C, Kind, Mode
from siftool.planner import plan_container
from siftool.formats.png import PngHandler, AI_TEXT_KEYWORDS
from siftool.errors import CorruptFileError, UnsafeFileError
from fixtures.make_fixtures import make_png, png_chunk, decode_pixels

H = PngHandler()
def by_tag(c, t): return [b for b in c.blocks if b.tag == t]
def clean_bytes(data, mode=Mode.PRIVACY):
    c = H.parse(data); buf = io.BytesIO(); H.write(c, plan_container(c, mode, H.rewrite), buf); return buf.getvalue()

def test_parse_classifies_chunks():
    c = H.parse(make_png(trailing=b"junk"))
    assert all(b.kind is Kind.ESSENTIAL for t in ("IHDR", "IDAT", "IEND") for b in by_tag(c, t))
    assert by_tag(c, "iCCP")[0].findings[0].category is C.APPEARANCE
    assert by_tag(c, "caBX")[0].findings[0].category is C.AI_PROVENANCE
    assert by_tag(c, "eXIf")[0].rewritable and by_tag(c, "tIME")[0].findings[0].category is C.TIME
    assert by_tag(c, "vpAg")[0].kind is Kind.UNKNOWN and c.blocks[-1].tag == "TRAILING"

@pytest.mark.parametrize("kw", sorted(AI_TEXT_KEYWORDS))
def test_ai_keywords(kw):
    c = H.parse(make_png(ai_text=False, extra_chunks=[png_chunk(b"tEXt", kw.encode() + b"\x00x")]))
    assert by_tag(c, "tEXt")[-1].findings[0].category is C.AI_PROVENANCE

def test_comment_json_prompt_is_ai_and_other_keywords():
    c = H.parse(make_png())
    by_label = {f.label: f.category for b in c.blocks for f in b.findings}
    assert by_label["Comment"] is C.AI_PROVENANCE and by_label["Author"] is C.IDENTITY
    assert by_label["Software"] is C.SOFTWARE and by_label["Title"] is C.TEXT

def test_ztxt_decompression_is_bounded():
    z = zlib.compressobj(); bomb = b"".join(z.compress(b"\x00" * (1 << 20)) for _ in range(50)) + z.flush()
    c = H.parse(make_png(extra_chunks=[png_chunk(b"zTXt", b"Title\x00\x00" + bomb)]))
    assert len(by_tag(c, "zTXt")[-1].findings[0].detail or "") <= 80

def test_unknown_critical_chunk_is_unsafe():
    with pytest.raises(UnsafeFileError): H.parse(make_png(extra_chunks=[png_chunk(b"ABCD", b"x")]))

def test_bad_crc_rules():
    with pytest.raises(CorruptFileError): H.parse(make_png(bad_crc="IDAT"))
    clean_bytes(make_png(bad_crc="tEXt"))
    with pytest.raises(CorruptFileError): clean_bytes(make_png(bad_crc="iCCP"))

def test_apng_chunks_are_essential_and_frames_survive():
    src = make_png(apng=True); c = H.parse(src)
    assert all(b.kind is Kind.ESSENTIAL for t in ("acTL", "fcTL", "fdAT") for b in by_tag(c, t))
    assert decode_pixels(clean_bytes(src)) == decode_pixels(src)

def test_privacy_output_keeps_appearance_and_digest():
    src = make_png(trailing=b"junk"); out = H.parse(clean_bytes(src))
    assert {b.tag for b in out.blocks if b.kind is not Kind.ESSENTIAL} <= {"iCCP", "sRGB", "gAMA", "cHRM", "pHYs", "eXIf"}
    assert {f.label for b in by_tag(out, "eXIf") for f in b.findings} == {"Orientation", "ColorSpace"}
    assert H.essential_digest(out) == H.essential_digest(H.parse(src))

def test_photographer_rewrites_exif():
    out = clean_bytes(make_png(), Mode.PHOTOGRAPHER)
    labels = {f.label for b in H.parse(out).blocks if b.tag == "eXIf" for f in b.findings}
    assert {"Make", "Model"} <= labels and not any(l.startswith("GPS") for l in labels)
