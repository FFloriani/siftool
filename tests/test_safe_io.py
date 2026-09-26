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
