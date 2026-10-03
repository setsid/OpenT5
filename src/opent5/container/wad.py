"""Read, list, extract and repack the online .wad container (online_tu13_mp_<lang>.wad).

The wad is the bundle of online text the MP client downloads from storage at
sign-in: the message of the day, the playlists and the contracts. It is not a
zone. Everything is big-endian, there is no signature and no encryption, and
each member is an ordinary zlib stream (78 da header, Adler-32 trailer).

    0x00  u32  magic            0x543377AB
    0x04  u32  timestamp        Unix seconds; not read by the parser
    0x08  u32  entry count
    0x0c  u32  version          13 in the TU13 wad; the client stores it
    0x10  entry[count], 44 bytes each:
          char name[32]         NUL-padded
          u32  compressed size  bytes of the zlib stream
          u32  size             bytes once inflated; not read by the parser
          u32  offset           from the start of the file
    then the zlib streams, back to back in table order, no padding

Evidence for every field, from the samples and from the parser in t5mp.elf
at 0x6bccf0, is in docs/research/wad.md.

Limits imposed by the client, not by the format: the whole file is
downloaded into a 0x10000-byte buffer, and each member is inflated into a
zeroed 0x40000-byte buffer that its consumer reads as a NUL-terminated
string. `write_wad` refuses to produce something larger than either unless
told otherwise.

    python -m opent5.container.wad list online_tu13_mp_english.wad
    python -m opent5.container.wad unpack online_tu13_mp_english.wad out/
    python -m opent5.container.wad pack out/ rebuilt.wad
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path

MAGIC = 0x543377AB
#: The magic read as little-endian. The client accepts it and byte-swaps the
#: three header words after it, but not the entry fields (t5mp.elf
#: 0x6bce18-0x6bcf78), so such a file cannot work; it is reported, not read.
MAGIC_SWAPPED = 0xAB773354

HEADER = struct.Struct(">IIII")
ENTRY_FIELDS = struct.Struct(">III")
NAME_SIZE = 32
ENTRY_SIZE = NAME_SIZE + ENTRY_FIELDS.size  # 44, the loop stride at 0x6bcff4

OFFSET_TIMESTAMP = 0x04
OFFSET_COUNT = 0x08
OFFSET_VERSION = 0x0C
OFFSET_ENTRIES = HEADER.size

#: The download buffer the client fetches the wad into (t5mp.elf 0x6bc8e8:
#: lis r5,1, stored as the buffer length).
MAX_FILE_SIZE = 0x10000
#: The inflate buffer for one member (0x6bcfd0: lis r20,4, passed as
#: avail_out after a memset of the same length). The consumer reads it as a
#: string, so a member of exactly this length would have no terminator.
MAX_MEMBER_SIZE = 0x40000 - 1

#: The names the parser acts on (strings at 0x940258..0x940290). Anything else
#: is inflated and ignored. liveblurb takes the language name.
KNOWN_MEMBERS = ("contracts.info", "playlists_mp.info", "motd-mp.txt", "liveblurb-mp-%s.txt")

#: zlib level 9 reproduces every stream in the retail wad byte for byte.
DEFAULT_LEVEL = 9

MANIFEST_NAME = "wad.json"


class WadError(ValueError):
    pass


@dataclass(frozen=True)
class Member:
    name: str
    data: bytes
    #: The stream as it was in the file, kept so an untouched member repacks
    #: byte for byte whatever compressor made it. None for a new member.
    stored: bytes | None = field(default=None, repr=False)

    @property
    def unchanged(self) -> bool:
        return self.stored is not None and _inflate_or_none(self.stored) == self.data


@dataclass(frozen=True)
class Wad:
    timestamp: int
    version: int
    members: tuple[Member, ...]

    def names(self) -> list[str]:
        return [m.name for m in self.members]

    def get(self, name: str) -> Member:
        for member in self.members:
            if member.name == name:
                return member
        raise WadError(f"no member named {name!r}; the wad holds {self.names()}")

    def with_member(self, name: str, data: bytes) -> Wad:
        """Replace a member's content, or append a new member at the end."""
        members = list(self.members)
        for i, member in enumerate(members):
            if member.name == name:
                members[i] = Member(name=name, data=data)
                return replace(self, members=tuple(members))
        return replace(self, members=(*self.members, Member(name=name, data=data)))

    def without_member(self, name: str) -> Wad:
        self.get(name)
        return replace(self, members=tuple(m for m in self.members if m.name != name))


@dataclass(frozen=True)
class Entry:
    """One table row exactly as stored, for listing."""

    index: int
    name: str
    compressed_size: int
    size: int
    offset: int


def _inflate_or_none(stored: bytes) -> bytes | None:
    try:
        return zlib.decompress(stored)
    except zlib.error:
        return None


def read_entries(data: bytes) -> tuple[int, int, list[Entry]]:
    """Parse the header and table without inflating anything."""
    if len(data) < HEADER.size:
        raise WadError(f"expected at least {HEADER.size} bytes of header, found {len(data)}")
    magic, timestamp, count, version = HEADER.unpack_from(data, 0)
    if magic == MAGIC_SWAPPED:
        raise WadError(
            "magic at 0x0 is 0x543377ab byte-swapped; the client swaps only the header "
            "of such a file, not its entries, so it is not a usable wad"
        )
    if magic != MAGIC:
        raise WadError(f"expected magic 0x{MAGIC:08x} at 0x0, found 0x{magic:08x}")
    table_end = OFFSET_ENTRIES + count * ENTRY_SIZE
    if table_end > len(data):
        raise WadError(
            f"expected {count} entries to end at 0x{table_end:x}, the file ends at 0x{len(data):x}"
        )
    entries = []
    for i in range(count):
        at = OFFSET_ENTRIES + i * ENTRY_SIZE
        raw_name = data[at : at + NAME_SIZE]
        if b"\x00" not in raw_name:
            raise WadError(f"expected a NUL in the name at 0x{at:x}, found none in 32 bytes")
        name = raw_name.split(b"\x00", 1)[0].decode("latin-1")
        compressed, size, offset = ENTRY_FIELDS.unpack_from(data, at + NAME_SIZE)
        if offset < table_end or offset + compressed > len(data):
            raise WadError(
                f"entry {i} {name!r} at 0x{at:x}: expected a stream inside "
                f"0x{table_end:x}..0x{len(data):x}, found 0x{offset:x}+0x{compressed:x}"
            )
        entries.append(Entry(i, name, compressed, size, offset))
    return timestamp, version, entries


def read_wad(data: bytes) -> Wad:
    timestamp, version, entries = read_entries(data)
    members = []
    for entry in entries:
        stored = data[entry.offset : entry.offset + entry.compressed_size]
        inflater = zlib.decompressobj()
        try:
            body = inflater.decompress(stored)
        except zlib.error as exc:
            raise WadError(
                f"entry {entry.index} {entry.name!r} at 0x{entry.offset:x}: "
                f"expected a zlib stream, found {stored[:2].hex()}...: {exc}"
            ) from exc
        if not inflater.eof:
            raise WadError(
                f"entry {entry.index} {entry.name!r} at 0x{entry.offset:x}: "
                f"expected a complete zlib stream in 0x{entry.compressed_size:x} bytes, "
                "it is cut short"
            )
        if inflater.unused_data:
            raise WadError(
                f"entry {entry.index} {entry.name!r} at 0x{entry.offset:x}: expected the stream "
                f"to fill its size, found {len(inflater.unused_data)} bytes after its end"
            )
        if len(body) != entry.size:
            raise WadError(
                f"entry {entry.index} {entry.name!r} at "
                f"0x{OFFSET_ENTRIES + entry.index * ENTRY_SIZE:x}: "
                f"expected {entry.size} bytes inflated, found {len(body)}"
            )
        members.append(Member(name=entry.name, data=body, stored=stored))
    return Wad(timestamp=timestamp, version=version, members=tuple(members))


def write_wad(wad: Wad, level: int = DEFAULT_LEVEL, enforce_limits: bool = True) -> bytes:
    """Build the file, recomputing every size and offset.

    An untouched member keeps its original stream; anything else is
    compressed at `level`. Streams follow the table back to back, as in
    every sample, so a wad read from a retail file comes back byte for byte.
    """
    table_end = OFFSET_ENTRIES + len(wad.members) * ENTRY_SIZE
    table = bytearray()
    blobs = []
    offset = table_end
    seen = set()
    for member in wad.members:
        try:
            raw_name = member.name.encode("ascii")
        except UnicodeEncodeError as exc:
            raise WadError(f"expected an ASCII member name, found {member.name!r}") from exc
        if len(raw_name) >= NAME_SIZE or not raw_name or b"\x00" in raw_name:
            raise WadError(
                f"expected a name of 1..{NAME_SIZE - 1} bytes, found {member.name!r} "
                f"({len(raw_name)} bytes)"
            )
        if member.name in seen:
            raise WadError(f"expected unique names, found {member.name!r} twice")
        seen.add(member.name)
        if enforce_limits and len(member.data) > MAX_MEMBER_SIZE:
            raise WadError(
                f"{member.name!r}: expected at most {MAX_MEMBER_SIZE} bytes (the client's inflate "
                f"buffer less a terminator), found {len(member.data)}"
            )
        stored = member.stored if member.unchanged else zlib.compress(member.data, level)
        table += raw_name.ljust(NAME_SIZE, b"\x00")
        table += ENTRY_FIELDS.pack(len(stored), len(member.data), offset)
        blobs.append(stored)
        offset += len(stored)
    out = HEADER.pack(MAGIC, wad.timestamp, len(wad.members), wad.version) + bytes(table)
    out += b"".join(blobs)
    if enforce_limits and len(out) > MAX_FILE_SIZE:
        raise WadError(
            f"expected at most {MAX_FILE_SIZE} bytes (the client's download buffer), "
            f"the wad comes to {len(out)}"
        )
    return out


def _safe_member_path(directory: Path, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in (".", "..") or name == MANIFEST_NAME:
        raise WadError(f"refusing to write member {name!r} as a file")
    return directory / name


def unpack(path: Path, directory: Path) -> Wad:
    """Extract every member, with a manifest of order, timestamp and version."""
    wad = read_wad(Path(path).read_bytes())
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for member in wad.members:
        _safe_member_path(directory, member.name).write_bytes(member.data)
    manifest = {
        "timestamp": wad.timestamp,
        "version": wad.version,
        "members": [
            {"name": m.name, "stored_zlib_hex": m.stored.hex() if m.stored else None}
            for m in wad.members
        ],
    }
    (directory / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1) + "\n")
    return wad


def pack(directory: Path, path: Path, level: int = DEFAULT_LEVEL) -> bytes:
    """Rebuild from an unpacked directory. Members whose files are unchanged
    reuse their recorded streams, so an untouched directory repacks exactly."""
    directory = Path(directory)
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise WadError(f"expected {manifest_path}, written by unpack; it is missing")
    manifest = json.loads(manifest_path.read_text())
    members = []
    for row in manifest["members"]:
        name = row["name"]
        data = _safe_member_path(directory, name).read_bytes()
        stored = bytes.fromhex(row["stored_zlib_hex"]) if row.get("stored_zlib_hex") else None
        members.append(Member(name=name, data=data, stored=stored))
    listed = {row["name"] for row in manifest["members"]}
    for extra in sorted(p.name for p in directory.iterdir() if p.is_file()):
        if extra != MANIFEST_NAME and extra not in listed:
            members.append(Member(name=extra, data=(directory / extra).read_bytes()))
    wad = Wad(manifest["timestamp"], manifest["version"], tuple(members))
    out = write_wad(wad, level)
    Path(path).write_bytes(out)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    show = sub.add_parser("list", help="print the header and table")
    show.add_argument("wad", type=Path)
    out = sub.add_parser("unpack", help="extract members and a manifest")
    out.add_argument("wad", type=Path)
    out.add_argument("directory", type=Path)
    into = sub.add_parser("pack", help="rebuild a wad from an unpacked directory")
    into.add_argument("directory", type=Path)
    into.add_argument("wad", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "list":
            timestamp, version, entries = read_entries(args.wad.read_bytes())
            print(f"timestamp {timestamp}  version {version}  entries {len(entries)}")
            for e in entries:
                print(
                    f"  {e.name:<32} offset 0x{e.offset:05x}  "
                    f"stored {e.compressed_size:>6}  size {e.size:>7}"
                )
        elif args.action == "unpack":
            unpack(args.wad, args.directory)
        else:
            pack(args.directory, args.wad)
    except WadError as exc:
        print(f"wad: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
