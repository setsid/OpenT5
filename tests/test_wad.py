"""The online .wad container: read, list, extract, repack."""

import os
import struct
import zlib

import pytest

from opent5 import env
from opent5.container import wad as wadmod
from opent5.container.wad import (
    ENTRY_SIZE,
    MAGIC,
    MAX_FILE_SIZE,
    MAX_MEMBER_SIZE,
    Member,
    Wad,
    WadError,
    main,
    pack,
    read_entries,
    read_wad,
    unpack,
    write_wad,
)

# Real wads are optional: point OPENT5_WADS at a folder holding them.
WADS = env.path_of("OPENT5_WADS") or env.ROOT / "missing"
RETAIL = WADS / "online_tu13_mp_english.wad"
REBUILDS = [WADS / "online_tu13_mp_english.base.wad", WADS / "online_tu13_mp_english.new.wad"]
need_retail = pytest.mark.skipif(not RETAIL.is_file(), reason="the retail wad is not here")


def synthetic(level: int = 9) -> bytes:
    """A wad laid out by hand, independently of write_wad."""
    members = [
        (b"motd-mp.txt", b"Welcome\r\n"),
        (b"playlists_mp.info", b"playlist\r\n" * 50),
        (b"contracts.info", b"contract 1\r\n" * 20),
    ]
    table_end = 16 + 44 * len(members)
    head = struct.pack(">IIII", 0x543377AB, 0x519AD68B, len(members), 13)
    table, blobs, offset = b"", b"", table_end
    for name, body in members:
        stored = zlib.compress(body, level)
        table += name.ljust(32, b"\0") + struct.pack(">III", len(stored), len(body), offset)
        blobs += stored
        offset += len(stored)
    return head + table + blobs


def test_header_and_table():
    timestamp, version, entries = read_entries(synthetic())
    assert (timestamp, version) == (0x519AD68B, 13)
    assert [e.name for e in entries] == ["motd-mp.txt", "playlists_mp.info", "contracts.info"]
    assert entries[0].offset == 16 + 3 * ENTRY_SIZE
    for a, b in zip(entries, entries[1:], strict=False):
        assert a.offset + a.compressed_size == b.offset


def test_members_inflate():
    wad = read_wad(synthetic())
    assert wad.get("motd-mp.txt").data == b"Welcome\r\n"
    assert wad.get("playlists_mp.info").data == b"playlist\r\n" * 50


@pytest.mark.parametrize("level", [1, 6, 9])
def test_byte_identical_repack_any_level(level):
    data = synthetic(level)
    assert write_wad(read_wad(data)) == data


def test_modified_repack():
    wad = read_wad(synthetic()).with_member("motd-mp.txt", b"Changed\r\n" * 3)
    rebuilt = read_wad(write_wad(wad))
    assert rebuilt.get("motd-mp.txt").data == b"Changed\r\n" * 3
    assert rebuilt.get("contracts.info").data == b"contract 1\r\n" * 20
    assert (rebuilt.timestamp, rebuilt.version) == (0x519AD68B, 13)


def test_add_and_remove_member():
    wad = read_wad(synthetic()).with_member("liveblurb-mp-english.txt", b"hi")
    wad = wad.without_member("contracts.info")
    rebuilt = read_wad(write_wad(wad))
    assert rebuilt.names() == ["motd-mp.txt", "playlists_mp.info", "liveblurb-mp-english.txt"]


def test_bad_magic():
    data = bytearray(synthetic())
    data[0] = 0
    with pytest.raises(WadError, match="expected magic 0x543377ab at 0x0, found 0x003377ab"):
        read_wad(bytes(data))


def test_swapped_magic_is_reported():
    data = struct.pack("<I", MAGIC) + synthetic()[4:]
    with pytest.raises(WadError, match="byte-swapped"):
        read_wad(data)


def test_truncated_table():
    with pytest.raises(WadError, match="entries to end at"):
        read_wad(synthetic()[:40])


def test_stream_past_end():
    with pytest.raises(WadError, match="expected a stream inside"):
        read_wad(synthetic()[:-1])


def test_size_mismatch():
    data = bytearray(synthetic())
    struct.pack_into(">I", data, 16 + 36, 1)
    with pytest.raises(WadError, match="expected 1 bytes inflated, found 9"):
        read_wad(bytes(data))


def test_not_zlib():
    data = bytearray(synthetic())
    first = struct.unpack_from(">I", data, 16 + 40)[0]
    data[first : first + 2] = b"\x00\x00"
    with pytest.raises(WadError, match="expected a zlib stream"):
        read_wad(bytes(data))


def test_name_too_long():
    wad = Wad(0, 13, (Member("x" * 32, b"a"),))
    with pytest.raises(WadError, match="expected a name of 1..31 bytes"):
        write_wad(wad)


def test_client_limits():
    with pytest.raises(WadError, match="inflate buffer"):
        write_wad(Wad(0, 13, (Member("a", b"x" * (MAX_MEMBER_SIZE + 1)),)))
    noise = os.urandom(MAX_FILE_SIZE)
    with pytest.raises(WadError, match="download buffer"):
        write_wad(Wad(0, 13, (Member("a", noise),)))
    assert write_wad(Wad(0, 13, (Member("a", noise),)), enforce_limits=False)


def test_unpack_pack_round_trip(tmp_path):
    source = tmp_path / "in.wad"
    source.write_bytes(synthetic(4))
    unpack(source, tmp_path / "out")
    assert (tmp_path / "out" / "motd-mp.txt").read_bytes() == b"Welcome\r\n"
    assert pack(tmp_path / "out", tmp_path / "again.wad") == source.read_bytes()
    (tmp_path / "out" / "motd-mp.txt").write_bytes(b"edited")
    pack(tmp_path / "out", tmp_path / "edited.wad")
    assert read_wad((tmp_path / "edited.wad").read_bytes()).get("motd-mp.txt").data == b"edited"


def test_unpack_refuses_path_names(tmp_path):
    bad = write_wad(Wad(0, 13, (Member("../evil", b"x"),)))
    (tmp_path / "bad.wad").write_bytes(bad)
    with pytest.raises(WadError, match="refusing"):
        unpack(tmp_path / "bad.wad", tmp_path / "out")


def test_cli(tmp_path, capsys):
    (tmp_path / "in.wad").write_bytes(synthetic())
    assert main(["list", str(tmp_path / "in.wad")]) == 0
    assert "playlists_mp.info" in capsys.readouterr().out
    assert main(["unpack", str(tmp_path / "in.wad"), str(tmp_path / "d")]) == 0
    assert main(["pack", str(tmp_path / "d"), str(tmp_path / "o.wad")]) == 0
    assert (tmp_path / "o.wad").read_bytes() == (tmp_path / "in.wad").read_bytes()
    (tmp_path / "junk.wad").write_bytes(b"\0" * 20)
    assert main(["list", str(tmp_path / "junk.wad")]) == 1


@need_retail
def test_retail_layout():
    data = RETAIL.read_bytes()
    assert data[:16].hex() == "543377ab519ad68b000000030000000d"
    timestamp, version, entries = read_entries(data)
    assert [(e.name, e.compressed_size, e.size, e.offset) for e in entries] == [
        ("motd-mp.txt", 0x235, 0x63A, 0x94),
        ("playlists_mp.info", 0x60A5, 0x1E6DC, 0x2C9),
        ("contracts.info", 0x42CE, 0x27B9C, 0x636E),
    ]
    assert entries[-1].offset + entries[-1].compressed_size == len(data)


@need_retail
def test_retail_byte_identical_and_level_9():
    data = RETAIL.read_bytes()
    wad = read_wad(data)
    assert write_wad(wad) == data
    fresh = Wad(wad.timestamp, wad.version, tuple(Member(m.name, m.data) for m in wad.members))
    assert write_wad(fresh) == data  # recompressing at level 9 reproduces retail too


@need_retail
def test_retail_modified():
    wad = read_wad(RETAIL.read_bytes()).with_member("motd-mp.txt", b"test motd\r\n")
    out = read_wad(write_wad(wad))
    assert out.get("motd-mp.txt").data == b"test motd\r\n"
    original = read_wad(RETAIL.read_bytes())
    assert out.get("contracts.info").data == original.get("contracts.info").data


@pytest.mark.parametrize("path", REBUILDS, ids=lambda p: p.name)
def test_rebuilt_samples(path):
    if not path.is_file():
        pytest.skip(f"{path.name} is not here")
    data = path.read_bytes()
    assert write_wad(read_wad(data)) == data


def test_module_constants():
    assert wadmod.ENTRY_SIZE == 44
    assert wadmod.MAX_FILE_SIZE == 0x10000
