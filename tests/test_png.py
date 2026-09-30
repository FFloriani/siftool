import io, struct, zlib, pytest
import siftool
from siftool import Finding, Status
from siftool.model import Category as C, Kind, Mode
from siftool.planner import plan_container
from siftool.formats.png import PngHandler, AI_TEXT_KEYWORDS
from siftool.errors import CorruptFileError, UnsafeFileError
from fixtures.make_fixtures import make_png, png_chunk, decode_pixels, make_icc, tiff_header, tiff_ifd, write

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
    # colour type 2 (RGB): tRNS must be exactly 6 bytes where a decoder reads it, before IDAT.
    # After IDAT it is misplaced -- dropped whatever its size (ruling R28) -- and make_png's
    # extra_chunks land after IDAT.
    with pytest.raises(CorruptFileError, match="invalid tRNS"): H.parse(_png(2, 8, png_chunk(b"tRNS", b"\x00" * 35)))
    [late] = [b for b in H.parse(make_png(extra_chunks=[png_chunk(b"tRNS", b"\x00" * 35)])).blocks if b.tag == "tRNS"]
    assert (late.kind, late.removable, late.label) == (Kind.UNKNOWN, True, "Misplaced or repeated tRNS")

def test_oversize_sbit_is_corrupt():
    # colour type 2 (RGB): sBIT must be exactly 3 bytes where a decoder reads it, before IDAT
    # (and PLTE); after IDAT it is misplaced and dropped, as for tRNS (ruling R28).
    with pytest.raises(CorruptFileError, match="invalid sBIT"): H.parse(_png(2, 8, png_chunk(b"sBIT", b"\x00" * 35)))
    [late] = [b for b in H.parse(make_png(extra_chunks=[png_chunk(b"sBIT", b"\x00" * 35)])).blocks if b.tag == "sBIT"]
    assert (late.kind, late.removable, late.label) == (Kind.UNKNOWN, True, "Misplaced or repeated sBIT")

# ---------------------------------------------------------------------------
# Final-review fix wave. Ruling R19 (C3, I2): a palette image needs exactly
# one valid PLTE before its first IDAT; a PLTE in any other colour type is
# dropped; a PNG without IDAT is corrupt. Ruling R20 (I1, triage T7): iCCP
# must inflate to a valid ICC profile (exposed as Block.logical) and its
# keyword is rewritten to "ICC profile"; the other appearance chunks must
# have their exact sizes.
# ---------------------------------------------------------------------------

IDAT_16 = png_chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + bytes(range(16)) for _ in range(16))))

def _png(colour_type, bit_depth, *chunks, idat=IDAT_16):
    ihdr = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, bit_depth, colour_type, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"".join(chunks) + idat + png_chunk(b"IEND", b"")

def _palette(entries):
    return png_chunk(b"PLTE", bytes(v for i in range(entries) for v in (i, 255 - i, i // 2)))

def layout(data):
    return [(b.tag, b.kind, b.removable) for b in H.parse(data).blocks]

def test_palette_png_is_essential_and_verified(tmp_path):
    data = _png(3, 8, _palette(16), png_chunk(b"tRNS", b"\x00\x80"))
    assert [t for t, k, _ in layout(data)] == ["IHDR", "PLTE", "tRNS", "IDAT", "IEND"]
    assert all(k is Kind.ESSENTIAL for _, k, _ in layout(data))
    for mode in Mode:
        res = siftool.clean(write(tmp_path, "p.png", data), tmp_path / f"o_{mode.value}.png", mode)
        assert res.verdict.status is Status.VERIFIED and (tmp_path / f"o_{mode.value}.png").read_bytes() == data

INVALID_PALETTES = {
    "missing": _png(3, 8),
    "two": _png(3, 8, _palette(16), _palette(16)),
    "after-idat": _png(3, 8, idat=IDAT_16 + _palette(16)),
    "not-multiple-of-3": _png(3, 8, png_chunk(b"PLTE", b"\x00" * 7)),
    "empty": _png(3, 8, png_chunk(b"PLTE", b"")),
    "over-bit-depth": _png(3, 1, _palette(3)),
    "over-256": _png(3, 8, png_chunk(b"PLTE", b"\x00" * 3 * 257)),
}

@pytest.mark.parametrize("case", sorted(INVALID_PALETTES))
def test_palette_png_needs_exactly_one_valid_plte_before_idat(case):
    with pytest.raises(CorruptFileError, match="^invalid PLTE$"): H.parse(INVALID_PALETTES[case])

@pytest.mark.parametrize("colour_type,bit_depth", [(0, 8), (2, 8), (4, 8), (6, 8)], ids=["gray", "rgb", "gray-alpha", "rgba"])
def test_plte_outside_palette_images_is_unused(colour_type, bit_depth):
    data = _png(colour_type, bit_depth, _palette(16))
    assert layout(data) == [("IHDR", Kind.ESSENTIAL, True), ("PLTE", Kind.UNKNOWN, True),
                            ("IDAT", Kind.ESSENTIAL, True), ("IEND", Kind.ESSENTIAL, True)]
    [plte] = [b for b in H.parse(data).blocks if b.tag == "PLTE"]
    assert (plte.label, plte.findings) == ("Unused PLTE chunk", [])
    assert b"PLTE" not in clean_bytes(data, Mode.PRIVACY)

@pytest.mark.parametrize("chunks", [[], [_palette(4)]], ids=["ihdr-iend", "palette-only"])
def test_png_without_idat_is_corrupt(chunks):
    with pytest.raises(CorruptFileError, match="^missing image data$"): H.parse(_png(3 if chunks else 2, 8, *chunks, idat=b""))

APPEARANCE_SIZES = {b"gAMA": 4, b"sRGB": 1, b"cHRM": 32, b"pHYs": 9, b"cICP": 4, b"mDCV": 24, b"cLLI": 8}

@pytest.mark.parametrize("ctype", sorted(APPEARANCE_SIZES), ids=lambda t: t.decode())
def test_appearance_chunks_must_have_their_exact_size(ctype):
    size = APPEARANCE_SIZES[ctype]
    tag = ctype.decode()
    for payload, expected in ((b"\x01" * size, (Kind.METADATA, C.APPEARANCE)),
                              (b"\x01" * (size + 46), (Kind.UNKNOWN, None)),
                              (b"\x01" * (size - 1), (Kind.UNKNOWN, None))):
        [block] = [b for b in H.parse(_png(2, 8, png_chunk(ctype, payload))).blocks if b.tag == tag]
        assert (block.kind, block.findings[0].category if block.findings else None) == expected
        if block.kind is Kind.UNKNOWN:
            assert block.removable and block.label == f"Invalid {tag} chunk"

PROFILE = make_icc("Display P3", 4)

def _iccp(keyword=b"icc", method=b"\x00", compressed=None):
    return png_chunk(b"iCCP", keyword + b"\x00" + method + (zlib.compress(PROFILE) if compressed is None else compressed))

def test_valid_iccp_is_rewritable_and_exposes_the_profile():
    [iccp] = [b for b in H.parse(_png(2, 8, _iccp())).blocks if b.tag == "iCCP"]
    assert (iccp.kind, iccp.rewritable, iccp.logical) == (Kind.METADATA, True, PROFILE)
    assert iccp.findings == [Finding(C.APPEARANCE, "ICC profile", "Display P3", "iCCP", len(PROFILE))]

def test_iccp_keyword_is_normalized_and_the_profile_copied_unchanged():
    compressed = zlib.compress(PROFILE, 9)
    data = _png(2, 8, _iccp(keyword=b"Felipe Floriani calibrated", compressed=compressed))
    out = clean_bytes(data)
    expected = png_chunk(b"iCCP", b"ICC profile\x00\x00" + compressed)
    assert expected in out and b"Felipe" not in out
    assert clean_bytes(out) == out  # idempotent: a second clean is byte-identical
    assert H.essential_digest(H.parse(out)) == H.essential_digest(H.parse(data))
    assert b"iCCP" not in clean_bytes(data, Mode.MAXIMUM)

INVALID_ICCP = {
    "no-keyword-terminator": png_chunk(b"iCCP", b"icc"),
    "bad-method": _iccp(method=b"\x01"),
    "not-zlib": _iccp(compressed=b"definitely not zlib"),
    "truncated-stream": _iccp(compressed=zlib.compress(PROFILE)[:-6]),
    "data-after-stream": _iccp(compressed=zlib.compress(PROFILE) + b" owner=Felipe"),
    "not-a-profile": _iccp(compressed=zlib.compress(b"desc GPS +23.5505-046.6333 owner=Felipe")),
    "over-4-mib": _iccp(compressed=zlib.compress(struct.pack(">I", (4 << 20) + 1) + PROFILE[4:] + bytes((4 << 20) + 1 - len(PROFILE)))),
}

@pytest.mark.parametrize("case", sorted(INVALID_ICCP))
def test_invalid_iccp_is_dropped(case):
    data = _png(2, 8, INVALID_ICCP[case])
    [iccp] = [b for b in H.parse(data).blocks if b.tag == "iCCP"]
    assert (iccp.kind, iccp.removable, iccp.label, iccp.findings) == (Kind.UNKNOWN, True, "Invalid ICC profile", [])
    assert clean_bytes(data) == _png(2, 8)

# ---------------------------------------------------------------------------
# Residual round. Rulings R25/R28 (I1 variant): the chunks the PNG spec (3rd
# edition) allows once -- gAMA/cHRM/sRGB/iCCP/cICP/mDCV/cLLI/sBIT before PLTE
# and IDAT, tRNS after PLTE and before IDAT, pHYs before IDAT -- keep only
# their first instance in a legal position; every repeat and every misplaced
# one is a removable UNKNOWN block. Ruling R24: one eXIf per file.
# ---------------------------------------------------------------------------

E, U = Kind.ESSENTIAL, Kind.UNKNOWN
_TEXT = b"owner=Felipe Floriani; phone=+55 11 91234-5678 " * 2

ONCE_ONLY = {  # type -> a payload valid in the 16x16 RGB8 image `_png(2, 8, ...)` builds
    b"gAMA": struct.pack(">I", 45455),
    b"cHRM": struct.pack(">8I", 31270, 32900, 64000, 33000, 30000, 60000, 15000, 6000),
    b"sRGB": b"\x00",
    b"iCCP": b"icc\x00\x00" + zlib.compress(PROFILE),
    b"cICP": bytes([1, 13, 0, 1]),
    b"mDCV": bytes(24),
    b"cLLI": bytes(8),
    b"sBIT": b"\x08\x08\x08",
    b"pHYs": struct.pack(">IIB", 2835, 2835, 1),
    b"tRNS": bytes(6),
}
ESSENTIAL_ONCE_ONLY = (b"sBIT", b"tRNS")
BEFORE_PLTE = sorted(set(ONCE_ONLY) - {b"pHYs", b"tRNS"})

def _another(ctype):
    """A second instance of `ctype` as valid as the first: text of the same size, or another valid profile."""
    if ctype == b"iCCP":
        return b"icc\x00\x00" + zlib.compress(make_icc("Felipe Floriani", 4))
    return _TEXT[: len(ONCE_ONLY[ctype])]

def _dropped(ctype, payload):
    """What a repeated or misplaced chunk parses to: a removable UNKNOWN block naming it."""
    label = f"Misplaced or repeated {ctype.decode()}"
    return (Kind.UNKNOWN, True, label, [Finding(C.UNKNOWN, label, None, ctype.decode(), len(payload))])

def _as_block(block):
    return (block.kind, block.removable, block.label, block.findings)

@pytest.mark.parametrize("ctype", sorted(ONCE_ONLY), ids=lambda t: t.decode())
def test_repeated_once_only_chunk_keeps_its_first_instance(ctype):
    first, repeat = png_chunk(ctype, ONCE_ONLY[ctype]), png_chunk(ctype, _another(ctype))
    data = _png(2, 8, first, repeat)
    kept, extra = [b for b in H.parse(data).blocks if b.tag == ctype.decode()]
    assert kept.kind is (E if ctype in ESSENTIAL_ONCE_ONLY else Kind.METADATA)
    assert _as_block(extra) == _dropped(ctype, _another(ctype))
    rewritten = png_chunk(b"iCCP", b"ICC profile" + ONCE_ONLY[b"iCCP"][3:]) if ctype == b"iCCP" else first
    assert clean_bytes(data, Mode.PRIVACY) == _png(2, 8, rewritten)
    assert clean_bytes(data, Mode.MAXIMUM) == (_png(2, 8, first) if ctype in ESSENTIAL_ONCE_ONLY else _png(2, 8))

@pytest.mark.parametrize("ctype", sorted(ONCE_ONLY), ids=lambda t: t.decode())
def test_once_only_chunk_after_idat_is_dropped(ctype):
    data = _png(2, 8, idat=IDAT_16 + png_chunk(ctype, ONCE_ONLY[ctype]))
    [block] = [b for b in H.parse(data).blocks if b.tag == ctype.decode()]
    assert _as_block(block) == _dropped(ctype, ONCE_ONLY[ctype])
    assert clean_bytes(data, Mode.PRIVACY) == _png(2, 8)

@pytest.mark.parametrize("ctype", BEFORE_PLTE, ids=lambda t: t.decode())
def test_once_only_chunk_after_plte_is_dropped(ctype):
    data = _png(3, 8, _palette(16), png_chunk(ctype, ONCE_ONLY[ctype]))  # sBIT is 3 bytes in a palette image too
    [block] = [b for b in H.parse(data).blocks if b.tag == ctype.decode()]
    assert _as_block(block) == _dropped(ctype, ONCE_ONLY[ctype])
    assert clean_bytes(data, Mode.PRIVACY) == _png(3, 8, _palette(16))

def test_phys_after_plte_is_in_place():
    phys = png_chunk(b"pHYs", ONCE_ONLY[b"pHYs"])
    data = _png(3, 8, _palette(16), phys)
    assert layout(data) == [("IHDR", E, True), ("PLTE", E, True), ("pHYs", Kind.METADATA, True),
                            ("IDAT", E, True), ("IEND", E, True)]
    assert clean_bytes(data, Mode.PRIVACY) == data

def test_trns_before_the_palette_is_dropped_and_the_next_one_kept():
    early, late = png_chunk(b"tRNS", b"\x00"), png_chunk(b"tRNS", b"\x00\x80")
    data = _png(3, 8, early, _palette(16), late)
    assert layout(data) == [("IHDR", E, True), ("tRNS", U, True), ("PLTE", E, True), ("tRNS", E, True),
                            ("IDAT", E, True), ("IEND", E, True)]
    assert clean_bytes(data, Mode.MAXIMUM) == _png(3, 8, _palette(16), late)

def test_a_suggested_palette_still_sets_the_chunk_order():
    trns, gama = png_chunk(b"tRNS", ONCE_ONLY[b"tRNS"]), png_chunk(b"gAMA", ONCE_ONLY[b"gAMA"])
    before = _png(2, 8, trns, _palette(16), gama)  # tRNS before PLTE, gAMA after it: both out of place
    assert layout(before) == [("IHDR", E, True), ("tRNS", U, True), ("PLTE", U, True), ("gAMA", U, True),
                              ("IDAT", E, True), ("IEND", E, True)]
    assert clean_bytes(before, Mode.PRIVACY) == _png(2, 8)
    after = _png(2, 8, gama, _palette(16), trns)  # both in place; the suggested palette itself is dropped
    assert clean_bytes(after, Mode.PRIVACY) == _png(2, 8, gama, trns)

def test_second_exif_chunk_is_dropped():  # ruling R24
    tiffs = [tiff_header() + tiff_ifd([(0x0112, 3, 1, struct.pack(">HH", v, 0))]) for v in (6, 3)]
    data = _png(2, 8, idat=IDAT_16 + png_chunk(b"eXIf", tiffs[0]) + png_chunk(b"eXIf", tiffs[1]))
    first, extra = [b for b in H.parse(data).blocks if b.tag == "eXIf"]
    assert (first.kind, first.rewritable, [f.detail for f in first.findings]) == (Kind.METADATA, True, ["6"])
    label = "Extra EXIF block"
    assert _as_block(extra) == (Kind.UNKNOWN, True, label, [Finding(C.UNKNOWN, label, None, "eXIf", len(tiffs[1]))])
    assert clean_bytes(data, Mode.PRIVACY) == _png(2, 8, idat=IDAT_16 + png_chunk(b"eXIf", tiffs[0]))
