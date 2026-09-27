import io, struct, zlib, pytest
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
    assert all(f.where for b in c.blocks for f in b.findings)

@pytest.mark.parametrize("kw", sorted(AI_TEXT_KEYWORDS))
def test_ai_keywords(kw):
    c = H.parse(make_png(ai_text=False, extra_chunks=[png_chunk(b"tEXt", kw.encode() + b"\x00x")]))
    assert by_tag(c, "tEXt")[-1].findings[0].category is C.AI_PROVENANCE

def test_comment_json_prompt_is_ai_and_other_keywords():
    c = H.parse(make_png())
    by_label = {f.label: f.category for b in c.blocks for f in b.findings}
    assert by_label["Comment"] is C.AI_PROVENANCE and by_label["Author"] is C.IDENTITY
    assert by_label["Software"] is C.SOFTWARE and by_label["Title"] is C.TEXT

def test_comment_with_bare_prompt_word_is_not_ai_provenance():
    c = H.parse(make_png(ai_text=False, extra_chunks=[png_chunk(b"tEXt", b'Comment\x00{"note": "impromptu"}')]))
    assert by_tag(c, "tEXt")[-1].findings[0].category is C.TEXT

def test_ztxt_decompression_is_bounded():
    z = zlib.compressobj(); bomb = b"".join(z.compress(b"\x00" * (1 << 20)) for _ in range(50)) + z.flush()
    c = H.parse(make_png(extra_chunks=[png_chunk(b"zTXt", b"Title\x00\x00" + bomb)]))
    finding = by_tag(c, "zTXt")[-1].findings[0]
    assert finding.size <= 1 << 20
    assert len(finding.detail or "") <= 80

def test_unknown_critical_chunk_is_unsafe():
    with pytest.raises(UnsafeFileError): H.parse(make_png(extra_chunks=[png_chunk(b"ABCD", b"x")]))

def test_bad_crc_rules():
    with pytest.raises(CorruptFileError): H.parse(make_png(bad_crc="IDAT"))
    clean_bytes(make_png(bad_crc="tEXt"))
    with pytest.raises(CorruptFileError): clean_bytes(make_png(bad_crc="iCCP"))

def test_bad_crc_on_rewrite_raises_except_in_maximum():
    with pytest.raises(CorruptFileError): clean_bytes(make_png(bad_crc="eXIf"), Mode.PRIVACY)
    with pytest.raises(CorruptFileError): clean_bytes(make_png(bad_crc="eXIf"), Mode.PHOTOGRAPHER)
    clean_bytes(make_png(bad_crc="eXIf"), Mode.MAXIMUM)

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

# ---------------------------------------------------------------------------
# Task 9b: essential chunks whose payload size is fixed by the format must
# actually be that size -- write() copies an ESSENTIAL chunk byte-for-byte
# and verify() never signature-scans it, so an oversize IHDR/IEND/acTL/fcTL
# could otherwise smuggle a secret past both.
# ---------------------------------------------------------------------------

def _rebuild_with_chunk(data: bytes, ctype: bytes, payload: bytes) -> bytes:
    """Rebuild a PNG, replacing the first chunk of type `ctype` with one
    carrying `payload` and a valid CRC; every other byte (signature, every
    other chunk) is copied unchanged. Assumes no data after IEND."""
    out = bytearray(data[:8])
    p = 8
    replaced = False
    while p < len(data):
        length = int.from_bytes(data[p : p + 4], "big")
        this_type = data[p + 4 : p + 8]
        end = p + 8 + length + 4
        if this_type == ctype and not replaced:
            out += png_chunk(ctype, payload)
            replaced = True
        else:
            out += data[p:end]
        p = end
    assert replaced
    return bytes(out)

def test_oversize_ihdr_is_corrupt():
    bad = _rebuild_with_chunk(make_png(), b"IHDR", b"\x00" * 14)
    with pytest.raises(CorruptFileError, match="invalid IHDR"): H.parse(bad)

def test_iend_with_payload_is_corrupt():
    secret = b" GPS +23.5505-046.6333 owner=Felipe"
    bad = _rebuild_with_chunk(make_png(), b"IEND", secret)
    with pytest.raises(CorruptFileError, match="invalid IEND"): H.parse(bad)

def test_apng_control_chunks_must_have_fixed_size():
    with pytest.raises(CorruptFileError, match="invalid acTL"):
        H.parse(_rebuild_with_chunk(make_png(apng=True), b"acTL", b"\x00" * 9))
    with pytest.raises(CorruptFileError, match="invalid fcTL"):
        H.parse(_rebuild_with_chunk(make_png(apng=True), b"fcTL", b"\x00" * 25))

# ---------------------------------------------------------------------------
# Controller ruling R15 (Task 9b fix round 1): the size rules alone are not
# enough -- a REPEATED or misplaced structure at its own legal size (a
# second IHDR, a second/late acTL, an fcTL with no acTL at all) is just as
# unscanned and byte-for-byte-trusted as an oversize one. Also: tRNS/sBIT
# have a payload size fixed by IHDR's own colour type, not a single
# constant, and were not size-checked at all before this round.
# ---------------------------------------------------------------------------

def test_second_ihdr_is_corrupt():
    bad = make_png(extra_chunks=[png_chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))])
    with pytest.raises(CorruptFileError, match="invalid IHDR"): H.parse(bad)

def test_repeated_actl_is_corrupt():
    bad = make_png(apng=True, extra_chunks=[png_chunk(b"acTL", b"\x00" * 8)])
    with pytest.raises(CorruptFileError, match="invalid acTL"): H.parse(bad)

def test_fctl_without_actl_is_corrupt():
    bad = make_png(apng=False, extra_chunks=[png_chunk(b"fcTL", b"\x00" * 26)])
    with pytest.raises(CorruptFileError, match="invalid fcTL"): H.parse(bad)

def test_oversize_trns_is_corrupt():
    # default make_png() is colour type 2 (RGB): tRNS must be exactly 6 bytes.
    bad = make_png(extra_chunks=[png_chunk(b"tRNS", b"\x00" * 35)])
    with pytest.raises(CorruptFileError, match="invalid tRNS"): H.parse(bad)

def test_oversize_sbit_is_corrupt():
    # default make_png() is colour type 2 (RGB): sBIT must be exactly 3 bytes.
    bad = make_png(extra_chunks=[png_chunk(b"sBIT", b"\x00" * 35)])
    with pytest.raises(CorruptFileError, match="invalid sBIT"): H.parse(bad)
