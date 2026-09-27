import json, os, subprocess, sys
from pathlib import Path
import siftool
from siftool.cli import main
from fixtures.make_fixtures import make_jpeg, make_png, write

def run(*args, cwd=None): return subprocess.run([sys.executable, "-m", "siftool", *map(str, args)], capture_output=True, cwd=cwd)

def test_module_entry_help(tmp_path):
    r = run("--help", cwd=tmp_path); assert r.returncode == 0 and b"clean" in r.stdout

def test_clean_human_output_and_exit_code(tmp_path, capsys):
    src = write(tmp_path, "a.png", make_png())
    assert main(["clean", str(src)]) == 0
    out = capsys.readouterr().out
    assert "[verified]" in out and "removed: C2PA manifest (ai_provenance)" in out and (tmp_path / "a_clean.png").exists()

def test_clean_json_schema_2(tmp_path, capsys):
    main(["clean", str(write(tmp_path, "a.jpg", make_jpeg())), "--json", "--mode", "photographer"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == 2 and doc["mode"] == "photographer"
    assert set(doc["results"][0]) == {"file", "output", "status", "removed", "kept", "notes"}
    assert set(doc["summary"]) == {"total", "verified", "warnings", "failed"}
    assert set(doc["results"][0]["removed"][0]) == {"category", "label", "detail", "where"}

def test_exit_codes(tmp_path):
    assert main(["clean", str(write(tmp_path, "w.jpg", make_jpeg(reserved_marker=True)))]) == 1
    assert main(["clean", str(write(tmp_path, "g.gif", b"GIF89a" + b"\x00" * 20))]) == 2

def test_scan_and_verify_commands(tmp_path, capsys):
    src = write(tmp_path, "a.jpg", make_jpeg())
    assert main(["scan", str(src), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["format"] == "jpeg"
    main(["clean", str(src)]); capsys.readouterr()
    assert main(["verify", str(tmp_path / "a_clean.jpg"), "--original", str(src)]) == 0

def test_unsupported_file_message(tmp_path, capsys):
    main(["clean", str(write(tmp_path, "g.gif", b"GIF89a" + b"\x00" * 20))])
    assert "GIF is not supported yet" in capsys.readouterr().out

def test_name_collision_overwrite_and_out_dir(tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg()); (tmp_path / "a_clean.jpg").write_bytes(b"old")
    main(["clean", str(src)])
    assert (tmp_path / "a_clean (2).jpg").exists() and (tmp_path / "a_clean.jpg").read_bytes() == b"old"
    main(["clean", str(src), "--overwrite"]); assert (tmp_path / "a_clean.jpg").read_bytes() != b"old"
    main(["clean", str(src), "--out", str(tmp_path / "new" / "dir")]); assert (tmp_path / "new" / "dir" / "a_clean.jpg").exists()

# Foco de revisão
def test_cli_prints_accented_names_without_error(tmp_path):
    src = write(tmp_path, "fotó 26 de set., ção.png", make_png())
    r = run("clean", src, cwd=tmp_path)
    assert r.returncode == 0 and "fotó 26 de set., ção" in r.stdout.decode("utf-8")

def test_cli_unwritable_out_dir_fails_without_traceback(tmp_path):
    src = write(tmp_path, "a.jpg", make_jpeg()); blocker = write(tmp_path, "file_not_dir", b"x")
    r = run("clean", src, "--out", blocker, cwd=tmp_path)
    assert r.returncode == 2 and b"Traceback" not in r.stderr

def test_cli_continues_after_a_failed_file(tmp_path, capsys):
    good = write(tmp_path, "a.jpg", make_jpeg())
    assert main(["clean", str(tmp_path / "missing.jpg"), str(good)]) == 2
    out = capsys.readouterr().out
    assert "File not found" in out and (tmp_path / "a_clean.jpg").exists()

def test_cli_folder_skips_previous_outputs_and_does_not_overwrite(tmp_path):
    write(tmp_path, "a.jpg", make_jpeg()); prev = write(tmp_path, "a_clean.jpg", b"previous run")
    write(tmp_path, "a_clean (2).jpg", b"older run")
    assert main(["clean", str(tmp_path)]) == 0
    assert prev.read_bytes() == b"previous run" and (tmp_path / "a_clean (3).jpg").exists()
    assert not (tmp_path / "a_clean_clean.jpg").exists()

# ---------------------------------------------------------------------------
# Ruling R22 (review I3): one run never reuses an output path it produced,
# even with --overwrite; folders are walked for spec section 10's extensions
# plus the handlers' own, so HEIC/video files are reported, not skipped.
# Ruling R23 (review I4): a failed record says why.
# ---------------------------------------------------------------------------

def _json(capsys, argv):
    code = main(argv)
    return code, json.loads(capsys.readouterr().out)

def test_same_name_in_two_folders_gets_two_outputs_even_with_overwrite(tmp_path, capsys):
    (tmp_path / "in" / "trip1").mkdir(parents=True); (tmp_path / "in" / "trip2").mkdir()
    write(tmp_path / "in" / "trip1", "IMG_0001.jpg", make_jpeg())
    write(tmp_path / "in" / "trip2", "IMG_0001.jpg", make_png())  # another picture, same name
    (tmp_path / "out").mkdir()
    write(tmp_path / "out", "IMG_0001_clean.jpg", b"from an earlier run")
    code, doc = _json(capsys, ["clean", str(tmp_path / "in"), "--out", str(tmp_path / "out"), "--overwrite", "--json"])
    assert code == 0
    outputs = [Path(r["output"]).name for r in doc["results"]]
    assert outputs == ["IMG_0001_clean.jpg", "IMG_0001_clean (2).jpg"]
    assert siftool.inspect(tmp_path / "out" / outputs[0]).format == "jpeg"  # trip1, replacing the old file
    assert siftool.inspect(tmp_path / "out" / outputs[1]).format == "png"   # trip2, not over trip1's

def test_outputs_differing_only_in_case_never_collide(tmp_path, capsys):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    write(tmp_path / "a", "IMG_0001.jpg", make_jpeg()); write(tmp_path / "b", "img_0001.JPG", make_png())
    code, doc = _json(capsys, ["clean", str(tmp_path / "a"), str(tmp_path / "b"), "--out", str(tmp_path / "out"),
                               "--overwrite", "--json"])
    outputs = [r["output"] for r in doc["results"]]
    assert code == 0 and len({os.path.normcase(o) for o in outputs}) == 2
    assert [siftool.inspect(o).format for o in outputs] == ["jpeg", "png"]

def test_folder_walk_reports_formats_not_supported_yet(tmp_path, capsys):
    write(tmp_path, "a.jpg", make_jpeg())
    write(tmp_path, "IMG_0002.HEIC", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64)
    write(tmp_path, "clip.mp4", b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64)
    write(tmp_path, "notes.txt", b"not an image")
    code, doc = _json(capsys, ["clean", str(tmp_path), "--json"])
    assert code == 2 and doc["summary"] == {"total": 3, "verified": 1, "warnings": 0, "failed": 2}
    errors = {Path(r["file"]).name: r.get("error") for r in doc["results"]}
    assert errors == {"a.jpg": None, "IMG_0002.HEIC": "HEIC is not supported yet", "clip.mp4": "MP4/MOV is not supported yet"}
    code, doc = _json(capsys, ["scan", str(tmp_path), "--json"])
    assert code == 2 and len(doc["results"]) == 3

def test_folder_walk_includes_every_extension_a_handler_declares(tmp_path, capsys):
    for name in ("a.apng", "b.jfif", "c.jpe"):
        write(tmp_path, name, make_png() if name.endswith(".apng") else make_jpeg())
    code, doc = _json(capsys, ["clean", str(tmp_path), "--json"])
    assert code == 0 and sorted(Path(r["file"]).name for r in doc["results"]) == ["a.apng", "b.jfif", "c.jpe"]

FAILED_KEYS = ["file", "output", "status", "removed", "kept", "notes", "error", "violations"]

def test_failed_verification_record_carries_violations_and_notes(tmp_path, capsys):
    bad = write(tmp_path, "b.jpg", make_jpeg(reserved_marker=True, reserved_payload=b"has c2pa inside"))
    code, doc = _json(capsys, ["clean", str(bad), "--json"])
    [record] = doc["results"]
    assert code == 2 and list(record) == FAILED_KEYS
    assert (record["output"], record["status"], record["removed"], record["kept"]) == (None, "failed", [], [])
    assert record["error"] == "Verification failed"
    assert record["violations"] == [{"category": "ai_provenance", "label": "C2PA label", "detail": None, "where": "RESERVED:0xF0"}]
    assert record["notes"] == ["Unrecognized structure kept: RESERVED:0xF0"]
    assert main(["clean", str(bad)]) == 2
    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == [f"{bad}  [failed] Verification failed", "  violation: C2PA label (ai_provenance)",
                         "  note: Unrecognized structure kept: RESERVED:0xF0"]

def test_other_failed_records_have_the_same_keys(tmp_path, capsys):
    code, doc = _json(capsys, ["clean", str(tmp_path / "missing.jpg"), "--json"])
    [record] = doc["results"]
    assert code == 2 and list(record) == FAILED_KEYS
    assert (record["error"], record["notes"], record["violations"]) == ("File not found", [], [])
