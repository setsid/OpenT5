"""An inventory of every zone on this machine: does each open, what is in
it, and does it rebuild byte for byte.

Used by tools/inventory.py to regenerate the table in docs/research/zones.md.
Reads only; nothing is written next to the zones.

The asset and script string counts come from the XAssetList, which the
loader reads raw straight after the 36-byte prefix (docs/research/xfile.md,
section 2):

    content 0x24  u32  scriptStringCount
    content 0x28  u32  scriptStrings pointer (0xffffffff inline, 0 none)
    content 0x2c  u32  assetCount
    content 0x30  u32  assets pointer (0xffffffff inline)
"""

from __future__ import annotations

import os
import struct
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from opent5.container.fastfile import (
    CHUNKS_OFFSET,
    OFFSET_VERSION,
    STREAM_COUNT,
    XCHUNK_WRITE_SIZE,
    ZONE_SIZE_PREFIX,
    FastFileError,
    carries_console_signature,
    declared_size,
    padded_length,
    read_fastfile,
)
from opent5.container.zone import Zone

ASSET_LIST_AT = ZONE_SIZE_PREFIX
#: A file whose size moved while it was read, or modified this recently, may
#: still be arriving.
SETTLE_SECONDS = 60


@dataclass
class ZoneRow:
    name: str
    folder: str
    size: int
    version: int = 0
    signed: bool = False
    opens: bool = False
    chunks: int = 0
    content_bytes: int = 0
    script_strings: int = 0
    assets: int = 0
    #: Unmodified open -> save, untouched chunks carried verbatim.
    verbatim_identical: bool = False
    #: Every chunk deflated afresh at level 9, memLevel 9.
    recompressed_identical: bool | None = None
    declared_size_ok: bool = False
    padding_rule_ok: bool = False
    terminators: int = 0
    #: Chunk 0 inflates to the 36-byte prefix and nothing else.
    prefix_alone: bool = False
    ring_gaps: int = 0
    max_stored: int = 0
    #: Inflated sizes of the chunks between the prefix and the last chunk
    #: that are not XCHUNK_WRITE_SIZE.
    odd_bodies: dict[int, int] = field(default_factory=dict)
    error: str = ""
    seconds: float = 0.0


def folder_label(path: Path, roots: dict[str, Path]) -> str:
    for label, root in roots.items():
        try:
            rel = path.parent.relative_to(root)
        except ValueError:
            continue
        return label if str(rel) == "." else f"{label}/{rel}"
    return str(path.parent)


def _might_be_arriving(path: Path, before: os.stat_result) -> bool:
    after = path.stat()
    return after.st_size != before.st_size or time.time() - after.st_mtime < SETTLE_SECONDS


def inspect(path: Path, folder: str = "", recompress: bool = True) -> ZoneRow:
    """Everything the table reports for one zone."""
    started = time.perf_counter()
    before = path.stat()
    row = ZoneRow(name=path.name, folder=folder, size=before.st_size)
    data = path.read_bytes()
    try:
        if len(data) >= OFFSET_VERSION + 4:
            (row.version,) = struct.unpack_from(">I", data, OFFSET_VERSION)
        row.signed = carries_console_signature(data[:CHUNKS_OFFSET])
        fastfile = read_fastfile(data)
        row.opens = True
        row.chunks = len(fastfile.chunks)
        content = fastfile.content
        row.content_bytes = len(content)
        if len(content) >= ASSET_LIST_AT + 16:
            row.script_strings, _, row.assets, _ = struct.unpack_from(">4I", content, ASSET_LIST_AT)
        row.declared_size_ok = declared_size(content) == len(content) - ZONE_SIZE_PREFIX
        row.terminators = fastfile.terminators
        row.prefix_alone = len(fastfile.chunks[0].body) == ZONE_SIZE_PREFIX
        row.padding_rule_ok = len(data) == padded_length(fastfile.end) and not any(
            data[fastfile.end :]
        )
        row.ring_gaps = sum(1 for c in fastfile.chunks if c.skipped) + len(fastfile.trailing_skips)
        row.max_stored = max(c.compressed for c in fastfile.chunks)
        inner = [len(c.body) for c in fastfile.chunks[1:-1]]
        row.odd_bodies = dict(Counter(n for n in inner if n != XCHUNK_WRITE_SIZE))
        del fastfile, content
        zone = Zone.open(data)
        row.verbatim_identical = zone.build().identical
        if recompress:
            row.recompressed_identical = zone.build(recompress=True).identical
    except FastFileError as exc:
        row.error = str(exc)
        if "is short" in row.error or _might_be_arriving(path, before):
            row.error = f"incomplete transfer? {row.error}"
    except Exception as exc:  # noqa: BLE001 - the table records every failure
        row.error = f"{type(exc).__name__}: {exc}"
    row.seconds = time.perf_counter() - started
    return row


def _inspect(args: tuple[str, str, bool]) -> dict:
    path, folder, recompress = args
    return asdict(inspect(Path(path), folder, recompress))


def run(
    paths: list[Path],
    roots: dict[str, Path] | None = None,
    recompress: bool = True,
    jobs: int = 4,
) -> list[ZoneRow]:
    roots = roots or {}
    work = [(str(p), folder_label(p, roots), recompress) for p in paths]
    if jobs <= 1:
        rows = [_inspect(item) for item in work]
    else:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            rows = list(pool.map(_inspect, work))
    return [ZoneRow(**row) for row in rows]


def _yes(value: bool | None) -> str:
    return "n/a" if value is None else ("yes" if value else "**no**")


def render(rows: list[ZoneRow]) -> str:
    """The zones table as Markdown."""
    head = (
        "| Zone | Folder | Size | Ver | Signed | Opens | Chunks | Inflated | Assets "
        "| Script strings | Round-trip (verbatim) | Round-trip (recompressed) |\n"
        "|---|---|--:|--:|---|---|--:|--:|--:|--:|---|---|\n"
    )
    lines = []
    for r in rows:
        lines.append(
            f"| {r.name} | {r.folder} | {r.size:,} | {r.version} | {_yes(r.signed)} "
            f"| {_yes(r.opens)} | {r.chunks:,} | {r.content_bytes:,} | {r.assets:,} "
            f"| {r.script_strings:,} | {_yes(r.verbatim_identical)} "
            f"| {_yes(r.recompressed_identical)} |"
        )
    return head + "\n".join(lines) + "\n"


def summary(rows: list[ZoneRow]) -> dict[str, int]:
    return {
        "zones": len(rows),
        "signed": sum(r.signed for r in rows),
        "open": sum(r.opens for r in rows),
        "verbatim_identical": sum(r.verbatim_identical for r in rows),
        "recompressed_identical": sum(bool(r.recompressed_identical) for r in rows),
        "declared_size_ok": sum(r.declared_size_ok for r in rows),
        "padding_rule_ok": sum(r.padding_rule_ok for r in rows),
        "four_terminators": sum(r.terminators == STREAM_COUNT for r in rows),
        "prefix_alone": sum(r.prefix_alone for r in rows),
        "ring_gaps": sum(r.ring_gaps for r in rows),
        "zones_with_odd_chunk_bodies": sum(bool(r.odd_bodies) for r in rows),
        "max_stored": max((r.max_stored for r in rows), default=0),
        "errors": sum(bool(r.error) for r in rows),
    }
