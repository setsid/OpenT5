"""Tier-1 content as text and tables, and the node bytes that store it.

Each ``*_state`` function returns what the editor shows; each ``*_bytes`` function returns
the node values that store a new state, computed the way the game's own files are built
(docs/research/structs-content.md sections 3, 4 and 16):

- rawfile, plain (cfg, menus, txt, csv, vision, and binary files such as .png): ``len`` =
  byte length, buffer = the bytes + NUL (the length is explicit, so NULs inside are kept).
- rawfile, .gsc / .csc: data = text + NUL; buffer = u32 BE len(data), u32 BE
  len(compressed), zlib level 6, then one byte, which is data[len] when len < len(data) and 0
  otherwise; ``len`` = 8 + len(compressed). This reproduces every retail script, so an
  unchanged script recompresses to the same bytes.
- stringtable: cell hash = djb2 over the lower-cased string; cellIndex lists every cell
  sorted by hash as signed 32-bit.
- map_ents: the entity string is stored with its NUL; numEntityChars (+8) counts it.
- localize: two plain strings, nothing else to recompute.

Text is Latin-1 throughout, so every byte value round-trips.
"""

from __future__ import annotations

import struct
import zlib

from opent5.edit.types import EditError
from opent5.xfile.constants import PTR_INLINE
from opent5.xfile.handlers.stringtable import string_hash

ZLIB_LEVEL = 6


def encode_text(text: str, what: str) -> bytes:
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise EditError(
            f"{what}: expected text the game can store (Latin-1), found "
            f"{text[exc.start : exc.end]!r} at character {exc.start}"
        ) from None


# -- rawfile -------------------------------------------------------------------------------


def rawfile_compressed(node: dict) -> bool:
    buf = node.get("buffer")
    return buf is not None and len(buf) >= 10 and bytes(buf[8:10]) == b"\x78\x9c"


def rawfile_text(node: dict) -> str:
    """The file as the game reads it; for a script, inflated, without its final NUL."""
    buf = node.get("buffer")
    if buf is None:
        return ""
    length = struct.unpack_from(">i", node["header"], 4)[0]
    if rawfile_compressed(node):
        size, packed = struct.unpack_from(">II", buf, 0)
        data = zlib.decompress(bytes(buf[8 : 8 + packed]))
        if len(data) != size:
            raise EditError(
                f"rawfile {node.get('name')}: expected {size} inflated bytes, found {len(data)}"
            )
        if data.endswith(b"\0"):
            data = data[:-1]
        return data.decode("latin-1")
    return bytes(buf[:length]).decode("latin-1")


def script_buffer(data: bytes) -> tuple[bytes, int]:
    """A script's stored buffer and ``len`` (data already ends with its NUL)."""
    packed = zlib.compress(data, ZLIB_LEVEL)
    length = 8 + len(packed)
    extra = data[length] if length < len(data) else 0
    return struct.pack(">II", len(data), len(packed)) + packed + bytes([extra]), length


def rawfile_bytes(node: dict, text: str) -> tuple[bytes, bytes]:
    """(header, buffer) storing ``text``; scripts stay compressed, plain files stay plain."""
    data = encode_text(text, f"rawfile {node.get('name')}")
    header = bytes(node["header"])
    if node.get("buffer") is None:
        raise EditError(
            f"rawfile {node.get('name')}: expected a buffer (pointer +8 non-zero), found none"
        )
    if rawfile_compressed(node):
        buffer, length = script_buffer(data + b"\0")
    else:
        buffer, length = data + b"\0", len(data)
    return header[:4] + struct.pack(">i", length) + header[8:], buffer


# -- map_ents ------------------------------------------------------------------------------


def mapents_text(node: dict) -> str:
    raw = node.get("entity_string")
    if raw is None:
        return ""
    raw = bytes(raw)
    if raw.endswith(b"\0"):
        raw = raw[:-1]
    return raw.decode("latin-1")


def mapents_bytes(node: dict, text: str) -> tuple[bytes, bytes]:
    """(header, entity_string) storing ``text`` (numEntityChars at +8 includes the NUL)."""
    data = encode_text(text, f"map_ents {node.get('name')}") + b"\0"
    if b"\0" in data[:-1]:
        raise EditError(f"map_ents {node.get('name')}: the entity string cannot hold a NUL")
    header = bytes(node["header"])
    if struct.unpack_from(">I", header, 4)[0] == 0:
        raise EditError(
            f"map_ents {node.get('name')}: expected an entity string (pointer +4 non-zero), "
            "found none"
        )
    return header[:8] + struct.pack(">I", len(data)) + header[12:], data


# -- stringtable ---------------------------------------------------------------------------


def table_shape(node: dict) -> tuple[int, int]:
    """(columns, rows)."""
    columns, rows = struct.unpack_from(">ii", node["header"], 4)
    return columns, rows


def table_rows(node: dict) -> list[list[str]]:
    columns, rows = table_shape(node)
    cells = node.get("cells") or []
    out = []
    for r in range(rows):
        out.append([cells[r * columns + c].get("string") or "" for c in range(columns)])
    return out


def signed(h: int) -> int:
    return h - (1 << 32) if h & 0x80000000 else h


def cell_raw(text: str) -> bytes:
    """A cell whose string follows inline, with its hash."""
    return struct.pack(">II", PTR_INLINE, string_hash(text))


def cell_hash(element: dict) -> int:
    return struct.unpack_from(">I", element["raw"], 4)[0]


def resort_index(node: dict, keep_order: bool) -> bytes:
    """cellIndex for the cells as they are now: every cell number sorted by signed hash.
    ``keep_order`` starts from the stored index (stable sort), so cells whose hashes did
    not move keep their relative order and an unchanged table keeps its exact index."""
    cells = node.get("cells") or []
    hashes = [signed(cell_hash(e)) for e in cells]
    if keep_order and node.get("cell_index") is not None:
        current = [v for (v,) in struct.iter_unpack(">h", node["cell_index"])]
        if sorted(current) == list(range(len(cells))):
            order = sorted(current, key=lambda i: hashes[i])
            return b"".join(struct.pack(">h", i) for i in order)
    order = sorted(range(len(cells)), key=lambda i: hashes[i])
    return b"".join(struct.pack(">h", i) for i in order)


MAX_CELLS = 32767


def table_header(node: dict, rows: int) -> bytes:
    """The StringTable header with a new rowCount; the cells and cellIndex pointers are
    set inline (they must be non-zero once there are cells)."""
    header = bytearray(node["header"])
    columns = struct.unpack_from(">i", header, 4)[0]
    if columns * rows > MAX_CELLS:
        raise EditError(
            f"stringtable {node.get('name')}: expected at most {MAX_CELLS} cells (the index "
            f"is 16-bit), found {columns} x {rows} = {columns * rows}"
        )
    struct.pack_into(">i", header, 8, rows)
    for off in (12, 16):
        if struct.unpack_from(">I", header, off)[0] == 0:
            struct.pack_into(">I", header, off, PTR_INLINE)
    return bytes(header)


# -- localize ------------------------------------------------------------------------------


def localize_state(node: dict) -> tuple[str, str]:
    return node.get("name") or "", node.get("value") or ""
