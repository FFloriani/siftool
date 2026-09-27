import os, pytest
from pathlib import Path
from siftool.safe_io import load, check_destination, AtomicOutput, TEMP_MARKER
from siftool.errors import CorruptFileError, UnsafeFileError, SameFileError, DestinationExistsError

def test_load_empty_file_is_corrupt(tmp_path):
    p = tmp_path / "e.jpg"; p.write_bytes(b"")
    with pytest.raises(CorruptFileError): load(p)

def test_load_over_limit_is_unsafe(tmp_path):
    p = tmp_path / "big.png"; p.write_bytes(b"x" * 11)
    with pytest.raises(UnsafeFileError): load(p, max_bytes=10)

def test_load_over_limit_does_not_read_the_file(tmp_path, monkeypatch):
    p = tmp_path / "big.png"; p.write_bytes(b"x" * 11)
    def _boom(self):
        raise AssertionError("must not read")
    monkeypatch.setattr(Path, "read_bytes", _boom)
    with pytest.raises(UnsafeFileError): load(p, max_bytes=10)

def test_same_file_is_refused_even_with_different_spelling(tmp_path):
    p = tmp_path / "a.png"; p.write_bytes(b"1")
    other = Path(str(p).upper()) if os.name == "nt" else p
    with pytest.raises(SameFileError): check_destination(p, other, overwrite=True)

def test_existing_destination_needs_overwrite(tmp_path):
    s = tmp_path / "a.png"; d = tmp_path / "b.png"; s.write_bytes(b"1"); d.write_bytes(b"2")
    with pytest.raises(DestinationExistsError): check_destination(s, d, overwrite=False)
    check_destination(s, d, overwrite=True)

def test_atomic_output_commit_and_discard(tmp_path):
    d = tmp_path / "out.png"
    a = AtomicOutput(d)
    with a.open() as f: f.write(b"data")
    assert TEMP_MARKER in a.path.name and a.path.parent == tmp_path and not d.exists()
    a.commit(); a.discard()
    assert d.read_bytes() == b"data" and not list(tmp_path.glob(f"*{TEMP_MARKER}*"))
    b = AtomicOutput(tmp_path / "gone.png")
    with b.open() as f: f.write(b"x")
    b.discard()
    assert not list(tmp_path.glob(f"*{TEMP_MARKER}*")) and not (tmp_path / "gone.png").exists()

# Review M9: commit(overwrite=False) never replaces a destination that
# appeared after check_destination -- os.rename refuses it on Windows; a hard
# link refuses it everywhere (both strategies run here: NTFS has hard links).

from siftool import safe_io

@pytest.mark.parametrize("rename_refuses", [True, False], ids=["rename", "link"])
def test_commit_without_overwrite_never_replaces_a_late_destination(tmp_path, monkeypatch, rename_refuses):
    if rename_refuses and os.name != "nt":
        pytest.skip("only Windows' rename refuses an existing target")
    monkeypatch.setattr(safe_io, "_RENAME_REFUSES_EXISTING", rename_refuses)
    late = tmp_path / "late.png"
    a = AtomicOutput(late)
    with a.open() as f: f.write(b"new")
    late.write_bytes(b"late")  # created after the existence check
    with pytest.raises(DestinationExistsError): a.commit(overwrite=False)
    assert late.read_bytes() == b"late" and a.path.read_bytes() == b"new"
    a.discard()
    assert not list(tmp_path.glob(f"*{TEMP_MARKER}*"))
    free = tmp_path / "free.png"
    b = AtomicOutput(free)
    with b.open() as f: f.write(b"new")
    b.commit(overwrite=False); b.discard()
    assert free.read_bytes() == b"new" and not list(tmp_path.glob(f"*{TEMP_MARKER}*"))

def test_commit_without_overwrite_on_a_filesystem_without_hard_links(tmp_path, monkeypatch):
    def no_links(src, dst):
        raise PermissionError("hard links not supported")
    monkeypatch.setattr(safe_io, "_RENAME_REFUSES_EXISTING", False)
    monkeypatch.setattr(safe_io.os, "link", no_links)
    free = tmp_path / "free.png"
    a = AtomicOutput(free)
    with a.open() as f: f.write(b"new")
    a.commit(overwrite=False); a.discard()
    assert free.read_bytes() == b"new" and not list(tmp_path.glob(f"*{TEMP_MARKER}*"))
    b = AtomicOutput(free)
    with b.open() as f: f.write(b"newer")
    with pytest.raises(DestinationExistsError): b.commit(overwrite=False)
    b.discard()
    assert free.read_bytes() == b"new" and not list(tmp_path.glob(f"*{TEMP_MARKER}*"))
