"""The command line on a synthetic zone: every command, its --json shape, exit codes, and
the refusal to write over the source or into the game folders."""

from __future__ import annotations

import csv
import hashlib
import io
import json

import numpy as np
import pytest

from opent5 import LICENCE_NOTICE, cli
from opent5.edit import Document
from opent5.formats import texture as tx
from test_edit_document import synthetic_ff


@pytest.fixture
def zone(tmp_path):
    path = tmp_path / "zones" / "mp_testedit.ff"
    path.parent.mkdir()
    path.write_bytes(synthetic_ff())
    return path


def run(capsys, *argv) -> tuple[int, str, str]:
    code = cli.main([str(a) for a in argv])
    out, err = capsys.readouterr()
    return code, out, err


def run_json(capsys, *argv) -> tuple[int, dict]:
    code, out, _ = run(capsys, *argv, "--json")
    return code, json.loads(out)


def test_version_keeps_the_licence_line_breaks(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.splitlines()[1:] == LICENCE_NOTICE.splitlines()


def test_usage_errors_exit_2(capsys, zone):
    assert cli.main([]) == 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["nosuchcommand"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        cli.main(["replace", str(zone), "x", "y"])  # no -o
    assert exc.value.code == 2
    code, _, err = run(capsys, "replace", zone, "only-one", "-o", zone.parent / "o.ff")
    assert code == 2 and "pairs" in err


def test_info(capsys, zone):
    code, d = run_json(capsys, "info", zone)
    assert code == 0 and d["ok"]
    assert d["zone"] == "mp_testedit" and d["assets"] == 10 and d["parse_exact"]
    assert d["type_counts"]["localize"] == 4
    assert d["sha1"] == hashlib.sha1(zone.read_bytes()).hexdigest()
    code, out, _ = run(capsys, "info", zone)
    assert code == 0 and "parse     exact" in out


def test_list_filters(capsys, zone):
    code, d = run_json(capsys, "list", zone, "--type", "localize")
    assert code == 0 and d["count"] == 4
    assert {a["name"] for a in d["assets"]} >= {"GREETING", "CFG_NAME"}
    code, d = run_json(capsys, "list", zone, "--match", "*.gsc")
    assert [a["index"] for a in d["assets"]] == [3]
    assert d["assets"][0]["editable"][0] == "text"


def test_extract_selected(capsys, zone, tmp_path):
    out = tmp_path / "x"
    code, d = run_json(capsys, "extract", zone, out, "--type", "stringtable")
    assert code == 0 and d["mode"] == "selected" and d["failures"] == []
    text = (out / "stringtables/mp/t.csv").read_text()
    rows = list(csv.reader(io.StringIO(text)))
    assert rows == [["alpha", "1"], ["beta", "2"], ["alpha", "3"]]
    code, d = run_json(capsys, "extract", zone, out, "--name", "*")
    assert code == 0
    assert (out / "rawfiles/maps/test.gsc").read_text() == "main()\n{\n\twait 1;\n}\n"
    assert (out / "localize/GREETING.txt").read_text() == "Hello"
    assert (out / "images/inline_img.png").is_file()
    assert (out / "map_ents/test.d3dbsp.ents").read_text().startswith("{")


def test_replace_every_tier1_kind(capsys, zone, tmp_path):
    files = tmp_path / "files"
    files.mkdir()
    (files / "a.gsc").write_text('main()\n{\n\tprintln("OpenT5");\n}\n')
    (files / "t.csv").write_text("alpha,1\nbeta,two hundred\nalpha,3\nadded,row\n")
    (files / "v.txt").write_text("Greetings from the command line\n")
    img = np.zeros((8, 8, 4), np.uint8)
    img[...] = (0, 255, 0, 255)
    (files / "i.png").write_bytes(tx.write_png(img))
    out = tmp_path / "out" / "edited.ff"
    code, d = run_json(
        capsys,
        "replace",
        zone,
        "maps/test.gsc", files / "a.gsc",
        "stringtable:mp/t.csv", files / "t.csv",
        "0", files / "v.txt",
        "image:deferred_img", files / "i.png",
        "-o", out,
    )  # fmt: skip
    assert code == 0, d
    assert d["verified"] and d["problems"] == []
    assert d["sha1"] == hashlib.sha1(out.read_bytes()).hexdigest()
    assert [r["as"] for r in d["replaced"]] == [
        "text",
        "table, 4 rows",
        "localize value",
        "image from PNG",
    ]
    back = Document.open(out)
    assert back.text(3) == 'main()\n{\n\tprintln("OpenT5");\n}\n'
    assert back.table(4)[1] == ["beta", "two hundred"] and back.table(4)[3] == ["added", "row"]
    assert back.localize(0)[1] == "Greetings from the command line"
    assert back.localize(1)[1] == "Hello"
    assert tuple(back.image(6).rgba[0, 0]) == (0, 255, 0, 255)


def test_replace_refuses_the_source_and_the_game_folders(capsys, zone, tmp_path, monkeypatch):
    (tmp_path / "v.txt").write_text("x")
    code, d = run_json(capsys, "replace", zone, "0", tmp_path / "v.txt", "-o", zone)
    assert code == 1 and "source zone itself" in d["error"]
    monkeypatch.setenv("OPENT5_ZONES", str(zone.parent))
    target = zone.parent / "other.ff"
    code, d = run_json(capsys, "replace", zone, "0", tmp_path / "v.txt", "-o", target)
    assert code == 1 and "outside the game folders" in d["error"]
    assert not target.exists()
    code, d = run_json(capsys, "extract", zone, zone.parent / "dump")
    assert code == 1 and not (zone.parent / "dump").exists()


def test_replace_reports_unknown_assets(capsys, zone, tmp_path):
    (tmp_path / "v.txt").write_text("x")
    code, d = run_json(
        capsys, "replace", zone, "nothing", tmp_path / "v.txt", "-o", tmp_path / "o.ff"
    )
    assert code == 1 and "no match" in d["error"]


def test_rebuild_is_byte_identical(capsys, zone, tmp_path):
    out = tmp_path / "rebuilt.ff"
    code, d = run_json(capsys, "rebuild", zone, "-o", out)
    assert code == 0 and d["file_identical"] and d["content_identical"]
    assert d["sha1"] == d["source_sha1"] == hashlib.sha1(out.read_bytes()).hexdigest()
    code, d = run_json(capsys, "rebuild", zone, "-o", tmp_path / "r2.ff", "--recompress")
    assert code == 0 and d["file_identical"] and d["chunks_carried"] == 0


def test_verify_and_against(capsys, zone, tmp_path):
    code, d = run_json(capsys, "verify", zone)
    assert code == 0 and d["ok"] and d["checks"]["parse_exact"]
    assert d["checks"]["rewrites_identically"]
    doc = Document.open(zone)
    doc.set_text(2, "a much longer config file\n" * 3)
    doc.save(tmp_path / "e.ff")
    code, d = run_json(capsys, "verify", tmp_path / "e.ff", "--against", zone)
    assert code == 0
    a = d["against"]
    assert [c["index"] for c in a["changed"]] == [2]
    assert a["identical"] + a["identical_after_pointer_remap"] == 9
    code, out, _ = run(capsys, "verify", tmp_path / "e.ff", "--against", zone)
    assert "1 changed" in out


def test_verify_fails_on_a_damaged_file(capsys, zone, tmp_path):
    bad = tmp_path / "bad.ff"
    data = bytearray(zone.read_bytes())
    data[0x13C + 4 + 20] ^= 0xFF  # inside the first chunk after the prefix chunk
    bad.write_bytes(bytes(data))
    code, d = run_json(capsys, "verify", bad)
    assert code == 1 and not d["ok"]


def test_unpack_and_pack(capsys, zone, tmp_path):
    folder = tmp_path / "unpacked"
    code, d = run_json(capsys, "unpack", zone, folder)
    assert code == 0 and d["zone"] == "mp_testedit"
    out = tmp_path / "packed.ff"
    code, d = run_json(capsys, "pack", folder, "-o", out)
    assert code == 0
    assert out.read_bytes() == zone.read_bytes()


def test_text_output_is_readable(capsys, zone, tmp_path):
    code, out, _ = run(capsys, "list", zone)
    assert code == 0 and "10 asset(s)" in out
    code, out, _ = run(capsys, "rebuild", zone, "-o", tmp_path / "r.ff")
    assert code == 0 and "byte-identical" in out


def test_csv_round_trip_through_extract_and_replace(capsys, zone, tmp_path):
    run(capsys, "extract", zone, tmp_path / "x", "--type", "stringtable")
    path = tmp_path / "x/stringtables/mp/t.csv"
    rows = list(csv.reader(io.StringIO(path.read_text())))
    rows.append(["with, comma", 'and "quotes"'])
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_text(buf.getvalue())
    code, d = run_json(capsys, "replace", zone, "mp/t.csv", path, "-o", tmp_path / "o.ff")
    assert code == 0
    assert Document.open(tmp_path / "o.ff").table(4)[-1] == ["with, comma", 'and "quotes"']


def test_a_path_or_unknown_name_is_left_as_given(tmp_path):
    from opent5.cli import zone_by_name

    existing = tmp_path / "zone.ff"
    existing.write_bytes(b"")
    assert zone_by_name(str(existing)) == str(existing)
    assert zone_by_name("some/dir/zone.ff") == "some/dir/zone.ff"
    assert zone_by_name("no_such_zone_anywhere") == "no_such_zone_anywhere"
