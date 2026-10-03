"""Verify a saved zone against the document it came from.

Checks, in order:

1. Container: the file decrypts and inflates, one terminator per stream, the zone's length
   field matches, and the content is exactly what was built.
2. Parse: the content parses exactly (the walk consumes every byte, every block ends at
   the size the header declares).
3. Every asset not edited: its bytes (and the deferred bytes it queued) are identical to
   the source, or identical apart from offset pointer fields, each of which names the
   same thing as before: the same string (by text), or the same allocation of the same
   asset at the same offset (for a target inside an edited asset: the same kind of
   allocation in the same asset).
4. Every edited asset reads back as edited: text, table, localize entry, pixels, field.
   Assets that only received inline copies of a shared string keep every string value.
"""

from __future__ import annotations

import bisect
from pathlib import Path
from typing import Any

import numpy as np

from opent5.container.fastfile import FastFileError
from opent5.container.zone import verify as verify_container
from opent5.edit import content as ct
from opent5.edit import images as im
from opent5.edit.types import AssetKey
from opent5.export.nodes import walk
from opent5.xfile import XFileError, parse
from opent5.xfile.constants import AssetType as T
from opent5.xfile.events import NONE, EventKind, PtrKind
from opent5.xfile.remap import RemapError, _Layout, _replay

POINTER, STRING, INSERT = EventKind.POINTER, EventKind.STRING, EventKind.INSERT
MOVABLE = (PtrKind.OFFSET, PtrKind.ALIAS_REF)
MAX_PROBLEMS = 50


class _Side:
    """One zone's log, layout and content, for describing pointer targets."""

    def __init__(self, xfile, content: bytes, layout: _Layout | None = None):
        self.xfile = xfile
        self.content = content
        self.rows = xfile.log.table()
        self.layout = layout or _replay(self.rows, None)[0]
        self.starts = [a.event_start for a in xfile.assets]

    def asset_rows(self, i: int) -> np.ndarray:
        end = self.starts[i + 1] if i + 1 < len(self.starts) else len(self.rows)
        return self.rows[self.starts[i] : end]

    def owner(self, event: int) -> int:
        return bisect.bisect_right(self.starts, event) - 1

    def describe(self, block: int, offset: int, changed: set[int]) -> tuple:
        try:
            a = self.layout.find(block, offset, NONE)
        except RemapError:
            return ("nothing", block)
        owner = self.owner(a.event)
        kind = int(self.rows[a.event, 0])
        inner = offset - a.old_start
        if kind == STRING:
            fp = int(self.rows[a.event, 1]) + inner
            end = self.content.find(b"\0", fp)
            return (owner, "string", self.content[fp:end])
        if kind == INSERT:
            return (owner, "alias slot")
        if owner in changed:
            return (owner, kind)
        return (owner, kind, inner, a.old_size)

    def tails(self, i: int) -> list[bytes]:
        return [
            bytes(d.data)
            for d in self.xfile.deferred
            if d.asset == i and d.data is not None and d.size
        ]


class Comparison:
    def __init__(self):
        #: (asset index, "identical" | "remapped" | "differs" | "edited", reason)
        self.rows: list[tuple[int, str, str]] = []
        self.identical = self.remapped = self.pointers = 0

    def of(self, status: str) -> list[int]:
        return [i for i, s, _ in self.rows if s == status]


def compare_assets(src: _Side, dst: _Side, touched: set[int] | None, progress=None) -> Comparison:
    """Classify every asset of ``dst`` against ``src``: identical bytes (and deferred
    bytes); identical apart from offset pointers that name the same things ("remapped");
    or different. Assets in ``touched`` are "edited" and not compared. With touched None,
    the assets that differ are found first, then the rest are compared knowing them."""
    if touched is None:
        first = compare_assets(src, dst, set())
        changed = set(first.of("differs"))
        if not changed:
            return first
        out = compare_assets(src, dst, changed)
        reasons = {i: r for i, st, r in first.rows if st == "differs"}
        out.rows = [
            (i, "differs", reasons[i]) if i in reasons else (i, st, r) for i, st, r in out.rows
        ]
        return out
    out = Comparison()
    old_assets, new_assets = src.xfile.assets, dst.xfile.assets
    for i, (a, b) in enumerate(zip(old_assets, new_assets, strict=True)):
        if progress is not None and i % 32 == 0:
            progress("Verifying assets", i, len(old_assets))
        if (a.type, a.name) != (b.type, b.name):
            out.rows.append(
                (i, "differs", f"expected {a.type_name} {a.name!r}, found {b.type_name} {b.name!r}")
            )
            continue
        if i in touched:
            out.rows.append((i, "edited", ""))
            continue
        status, reason = _compare_one(src, dst, i, a, b, touched, out)
        out.rows.append((i, status, reason))
    return out


def _compare_one(src: _Side, dst: _Side, i: int, a, b, touched: set[int], out) -> tuple[str, str]:
    ob = src.content[a.file_start : a.file_end]
    nb = dst.content[b.file_start : b.file_end]
    if src.tails(i) != dst.tails(i):
        return "differs", "its deferred bytes differ from the source"
    if ob == nb:
        out.identical += 1
        return "identical", ""
    if len(ob) != len(nb):
        return "differs", (
            f"expected {len(ob)} bytes as in the source, found {len(nb)} at {b.file_start:#x}"
        )
    orows, nrows = src.asset_rows(i), dst.asset_rows(i)
    op = orows[orows[:, 0] == POINTER]
    np_ = nrows[nrows[:, 0] == POINTER]
    if len(op) != len(np_) or not np.array_equal(op[:, 3], np_[:, 3]):
        return "differs", "its pointer fields differ in number or kind"
    masked_old, masked_new = bytearray(ob), bytearray(nb)
    checked = 0
    for (_, fo, _, kind, bo, oo), (_, fn, _, _, bn, on) in zip(
        op.tolist(), np_.tolist(), strict=True
    ):
        if kind not in MOVABLE or fo == NONE:
            continue
        ro, rn = fo - a.file_start, fn - b.file_start
        if ro != rn:
            return "differs", f"a pointer field moved inside the asset ({ro:#x} -> {rn:#x})"
        masked_old[ro : ro + 4] = masked_new[rn : rn + 4] = bytes(4)
        checked += 1
        was = src.describe(bo, oo, touched)
        now = dst.describe(bn, on, touched)
        if was != now:
            return "differs", f"the pointer at {fn:#x} named {was}, now names {now}"
    if masked_old != masked_new:
        at = next(k for k in range(len(ob)) if masked_old[k] != masked_new[k])
        return "differs", (
            f"byte {at:#x} of the asset (file {b.file_start + at:#x}): expected "
            f"{ob[at]:#04x} as in the source, found {nb[at]:#04x}"
        )
    out.pointers += checked
    out.remapped += 1
    return "remapped", ""


def _strings(node: Any) -> list[str]:
    out = []
    for n in walk(node):
        if isinstance(n, dict):
            out += [v for k, v in n.items() if isinstance(v, str) and k != "_t"]
        elif isinstance(n, list):
            out += [v for v in n if isinstance(v, str)]
    return out


def _find_inline(xfile, key: tuple, parent: int) -> dict | None:
    from opent5.edit.document import KIND_TYPES

    _, t, name = key
    root = xfile.assets[parent].data
    for n in walk(root):
        if isinstance(n, dict) and KIND_TYPES.get(n.get("_t")) == t and n.get("name") == name:
            return n
    return None


def _read(doc, xfile, key: AssetKey, kind: str) -> Any:
    if isinstance(key, tuple):
        node = _find_inline(xfile, key, doc._resolve(key)[2])
        t = key[1]
    else:
        node, t = xfile.assets[key].data, xfile.assets[key].type
    if node is None:
        return None
    if kind == "text":
        if t in (T.COL_MAP_MP, T.COL_MAP_SP):
            node, t = node.get("map_ents"), T.MAP_ENTS
        return ct.rawfile_text(node) if t == T.RAWFILE else ct.mapents_text(node)
    if kind == "table":
        return ct.table_rows(node)
    if kind == "localize":
        return ct.localize_state(node)
    if kind == "image":
        return im.stored_pixels(node)
    if kind.startswith("field:"):
        sv, name, _, _ = doc._struct_view(node, kind[len("field:") :])
        return sv[name]
    raise ValueError(kind)


def _stage(progress, name: str):
    if progress is None:
        return None
    return lambda _stage, done, total: progress(name, done, total)


def verify_saved(doc, path: Path, content: bytes, progress=None) -> tuple[list[str], dict]:
    """Checks 1 to 4 on a written file; ``content`` is what was built. ``progress(stage,
    done, total)`` is optional."""
    try:
        verify_container(
            Path(path).read_bytes(),
            expected=content,
            progress=_stage(progress, "Verifying: decrypting and inflating"),
        )
    except FastFileError as exc:
        return [f"container: {exc}"], {}
    return verify_content(doc, content, progress)


def verify_content(doc, content: bytes, progress=None) -> tuple[list[str], dict]:
    """Checks 2 to 4 on built content (no file)."""
    problems: list[str] = []
    details: dict[str, Any] = {}

    def problem(text: str) -> bool:
        problems.append(text)
        return len(problems) >= MAX_PROBLEMS

    try:
        new = parse(content, progress=_stage(progress, "Verifying: parsing"))
    except XFileError as exc:
        problem(f"parse: {exc}")
        return problems, details
    for p in new.problems():
        problem(f"parse: {p}")
    old = doc.xfile
    if len(new.assets) != len(old.assets):
        problem(f"asset list: expected {len(old.assets)} assets, found {len(new.assets)}")
        return problems, details

    touched = doc._touched()
    side = doc.side_effect_assets()
    src = _Side(old, doc.content, doc._rw.layout())
    dst = _Side(new, content)
    result = compare_assets(src, dst, touched, progress)
    for i, status, reason in result.rows:
        if status == "differs" and problem(f"asset {i} ({old.assets[i].name}): {reason}"):
            break
    identical, remapped, pointers = result.identical, result.remapped, result.pointers

    edited_ok = 0
    for key, kind, want in doc.expectations():
        try:
            got = _read(doc, new, key, kind)
        except Exception as exc:  # noqa: BLE001 - any failure to read back is a finding
            problem(f"asset {key!r}: could not read back {kind}: {exc}")
            continue
        if got != want:
            problem(f"asset {key!r}: {kind} does not read back as edited")
        else:
            edited_ok += 1
    for i in sorted(side):
        if _strings(old.assets[i].data) != _strings(new.assets[i].data):
            problem(f"asset {i} ({old.assets[i].name}): a string changed while copying shared text")

    details.update(
        assets_checked=len(new.assets),
        identical=identical,
        identical_after_pointer_remap=remapped,
        pointers_checked=pointers,
        edited=len(doc.edited_assets()),
        edited_read_back=edited_ok,
        shared_string_copies_in=sorted(side),
    )
    return problems, details
