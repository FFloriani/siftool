import json, subprocess, sys
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
