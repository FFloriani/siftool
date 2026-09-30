import pytest
from siftool.model import Category as C, Kind, Span, Finding, Block, Container, Action, Mode
from siftool.modes import ALLOWED
from siftool.planner import plan_container, MAXIMUM_APPEARANCE_NOTE

def blk(kind, *cats, removable=True, rewritable=False, tag="X"):
    return Block(kind=kind, span=Span(0, 1), label=tag, tag=tag,
                 findings=[Finding(c, c.value) for c in cats],
                 removable=removable, rewritable=rewritable, raw=b"r" if rewritable else b"")

def run(blocks, mode=Mode.PRIVACY, rewrite=lambda b, a: None, positional=False):
    return plan_container(Container("t", blocks, b"", positional), mode, rewrite)

def test_allowed_sets_per_mode():
    assert ALLOWED[Mode.PRIVACY] == {C.APPEARANCE}
    assert ALLOWED[Mode.PHOTOGRAPHER] == {C.APPEARANCE, C.DEVICE, C.CAMERA_SETTINGS}
    assert ALLOWED[Mode.MAXIMUM] == frozenset()

def test_finding_detail_is_truncated_to_80_chars():
    assert len(Finding(C.TEXT, "c", "x" * 200).detail) == 80

def test_essential_is_kept():
    assert run([blk(Kind.ESSENTIAL)]).actions == [Action.KEEP]

def test_metadata_fully_allowed_is_kept():
    assert run([blk(Kind.METADATA, C.APPEARANCE)]).actions == [Action.KEEP]

def test_metadata_not_allowed_is_dropped():
    assert run([blk(Kind.METADATA, C.LOCATION)]).actions == [Action.DROP]

def test_positional_container_zeroes_instead_of_dropping():
    assert run([blk(Kind.METADATA, C.LOCATION)], positional=True).actions == [Action.ZERO]

def test_mixed_rewritable_block_is_rewritten():
    plan = run([blk(Kind.METADATA, C.APPEARANCE, C.LOCATION, rewritable=True)], rewrite=lambda b, a: b"NEW")
    assert plan.actions == [Action.REWRITE] and plan.replacements == {0: b"NEW"}

def test_mixed_block_whose_rewrite_returns_none_is_dropped():
    assert run([blk(Kind.METADATA, C.APPEARANCE, C.LOCATION, rewritable=True)]).actions == [Action.DROP]

def test_mixed_block_not_rewritable_is_dropped_with_note():
    plan = run([blk(Kind.METADATA, C.APPEARANCE, C.LOCATION, tag="XMP")])
    assert plan.actions == [Action.DROP]
    assert any("could not keep" in n and "XMP" in n for n in plan.notes)

def test_unknown_removable_is_dropped():
    assert run([blk(Kind.UNKNOWN, C.UNKNOWN)]).actions == [Action.DROP]

def test_unknown_not_removable_is_kept_with_note():
    plan = run([blk(Kind.UNKNOWN, C.UNKNOWN, removable=False, tag="RESERVED:0xF0")])
    assert plan.actions == [Action.KEEP]
    assert "Unrecognized structure kept: RESERVED:0xF0" in plan.notes

def test_metadata_block_without_findings_is_dropped():
    assert run([blk(Kind.METADATA)]).actions == [Action.DROP]

def test_maximum_note_when_appearance_is_removed():
    plan = run([blk(Kind.METADATA, C.APPEARANCE)], mode=Mode.MAXIMUM)
    assert plan.actions == [Action.DROP] and MAXIMUM_APPEARANCE_NOTE in plan.notes

# Ruling R18 (review C1): a rewritable block always goes through rewrite(),
# whatever its categories -- a fully allowed one is re-serialized, never
# KEPT verbatim (EXIF slack, unreported sub-IFDs and odd-typed allowed tags
# would otherwise survive byte for byte).

def test_fully_allowed_rewritable_block_is_rewritten_not_kept():
    calls = []
    def rewrite(block, allowed):
        calls.append(allowed)
        return b"NEW"
    plan = run([blk(Kind.METADATA, C.APPEARANCE, rewritable=True)], rewrite=rewrite)
    assert plan.actions == [Action.REWRITE] and plan.replacements == {0: b"NEW"}
    assert calls == [ALLOWED[Mode.PRIVACY]] and plan.notes == []

def test_fully_allowed_rewritable_block_whose_rewrite_returns_none_is_dropped():
    assert run([blk(Kind.METADATA, C.APPEARANCE, rewritable=True)]).actions == [Action.DROP]

def test_rewritable_block_with_nothing_allowed_still_goes_through_rewrite():
    calls = []
    plan = run([blk(Kind.METADATA, C.LOCATION, rewritable=True)], rewrite=lambda b, a: calls.append(a))
    assert plan.actions == [Action.DROP] and calls == [ALLOWED[Mode.PRIVACY]] and plan.notes == []
