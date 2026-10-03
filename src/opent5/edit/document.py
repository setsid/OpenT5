"""``Document``: one zone open for reading and editing (docs/edit-api.md).

The document holds the zone's decompressed content as it was opened and one parse of it
(``opent5.xfile.remap.Rewrite``), whose nodes are the live state: every edit is an
operation that assigns new values into those nodes (bytes, strings, element lists), and
undo assigns the old ones back. Nothing touches a file until ``save``, which writes the
nodes with the type handlers, re-lays the zone and remaps every offset pointer (the
rewrite in remap.py), packs the container, and verifies the result (``opent5.edit.verify``).

Assets are addressed by their index in the zone's asset list, or, for an asset loaded
inside another one (an image inside a material, the entity string inside the clipMap), by
the key ("inline", type, name) that ``inline_assets`` lists.

Strings the zone shares (a later struct pointing at a string loaded earlier, which the
original linker does for identical text) stay correct: before a string is changed, every
other string field pointing at it is given its own inline copy of the old text, so the
edit changes this asset only. The change's ``detail`` says when that happened.
"""

from __future__ import annotations

import hashlib
import itertools
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from opent5 import env
from opent5.container.fastfile import FastFileError, carries_console_signature
from opent5.container.zone import Zone
from opent5.edit import content as ct
from opent5.edit import geometry as geo
from opent5.edit import images as im
from opent5.edit.types import (
    SIGNATURE_INTACT,
    SIGNATURE_NOTE,
    AssetKey,
    AssetRef,
    Change,
    EditError,
    ImageData,
    SaveReport,
    SearchHit,
)
from opent5.export.nodes import walk
from opent5.xfile import REGISTRY, XFileError
from opent5.xfile.constants import PTR_INLINE, type_name
from opent5.xfile.constants import AssetType as T
from opent5.xfile.events import NONE
from opent5.xfile.model import write_asset
from opent5.xfile.remap import RemapError, Rewrite
from opent5.xfile.schema import FieldError, StructView, view
from opent5.xfile.stream import AssetLink

INLINE4 = struct.pack(">I", PTR_INLINE)

#: Node kind -> asset type, for finding assets loaded inside other assets.
KIND_TYPES: dict[str, int] = {}
for _t, _h in sorted(REGISTRY.items()):
    if _h.kind and _h.kind not in KIND_TYPES:
        KIND_TYPES[_h.kind] = _t


@dataclass
class _Op:
    index: AssetKey
    kind: str
    before: Any
    after: Any
    detail: str
    #: (container, key, old value, new value), applied in order; undone in reverse.
    sets: list[tuple[Any, Any, Any, Any]]
    #: Top-level assets whose bytes change (the edited one, and any whose shared string
    #: was given an inline copy).
    touched: set[int] = field(default_factory=set)
    side_effects: set[int] = field(default_factory=set)
    token: int = 0

    def apply(self) -> None:
        for obj, key, _old, new in self.sets:
            obj[key] = new

    def revert(self) -> None:
        for obj, key, old, _new in reversed(self.sets):
            obj[key] = old


_tokens = itertools.count(1)


class _Sets(list):
    """Assignments for one operation, reading values through the ones already queued."""

    def __init__(self):
        super().__init__()
        self._now: dict[tuple[int, Any], Any] = {}

    def get(self, obj: Any, key: Any) -> Any:
        return self._now.get((id(obj), key), obj[key] if _has(obj, key) else None)

    def put(self, obj: Any, key: Any, value: Any) -> None:
        self.append((obj, key, self.get(obj, key), value))
        self._now[(id(obj), key)] = value


def _has(obj: Any, key: Any) -> bool:
    try:
        obj[key]
    except (KeyError, IndexError, TypeError):
        return False
    return True


class Document:
    """A zone open for editing. ``Document.open(path)``."""

    def __init__(self, data: bytes, path: Path | None = None):
        self._source = bytes(data)
        self.path = Path(path) if path is not None else None
        try:
            self._zone = Zone.open(self._source)
        except FastFileError as exc:
            raise EditError(f"{path or 'zone'}: not a fastfile this tool reads: {exc}") from None
        self.content = bytes(self._zone.content)
        self.zone_name = self._zone.name
        self.signed = carries_console_signature(bytes(self._zone.header))
        try:
            self._rw = Rewrite(self.content)
        except XFileError as exc:
            raise EditError(f"{self.zone_name}: the zone does not parse: {exc}") from None
        self.xfile = self._rw.xfile
        self.parse_problems = self.xfile.problems()
        self._undo: list[_Op] = []
        self._redo: list[_Op] = []
        self._saved_token = 0
        self._string_field_of = {
            (id(node), key): at for at, (node, key) in self._rw.trace.string_fields.items()
        }
        self._collect()

    @classmethod
    def open(cls, source: str | Path | bytes, name: str | None = None) -> Document:
        """Open a .ff by path, or from its bytes (``name`` is then only a label)."""
        if isinstance(source, bytes | bytearray | memoryview):
            doc = cls(bytes(source), Path(name) if name else None)
        else:
            path = Path(source)
            if not path.is_file():
                raise EditError(f"{path}: expected a fastfile, found no such file")
            doc = cls(path.read_bytes(), path)
        return doc

    # -- the asset list ----------------------------------------------------------------------

    def _collect(self) -> None:
        spans = self._rw.trace.spans
        self.assets: list[AssetRef] = []
        self.inline_assets: list[AssetRef] = []
        self._inline: dict[tuple, tuple[dict, int, int]] = {}
        for a in self.xfile.assets:
            self.assets.append(
                AssetRef(
                    a.index,
                    a.type,
                    a.type_name,
                    a.name,
                    a.size,
                    self._editable(a.type, a.data, top=True),
                    a.file_start,
                )
            )
            if not isinstance(a.data, dict):
                continue
            for node in walk(a.data):
                if node is a.data or not isinstance(node, dict) or "header" not in node:
                    continue
                t = KIND_TYPES.get(node.get("_t"))
                if t is None:
                    continue
                name = node.get("name")
                if name is None:
                    continue
                key = ("inline", t, name)
                if key in self._inline:
                    continue
                self._inline[key] = (node, t, a.index)
                start, end = spans.get(id(node), (None, None))
                self.inline_assets.append(
                    AssetRef(
                        key,
                        t,
                        type_name(t),
                        name,
                        (end - start) if start is not None else 0,
                        self._editable(t, node, top=False),
                        start,
                        a.index,
                    )
                )

    @staticmethod
    def _editable(t: int, node: Any, top: bool) -> tuple[str, ...]:
        out: list[str] = []
        if not isinstance(node, dict):
            return ("hex",) if top else ()
        if t == T.RAWFILE:
            out.append("text")
        elif t == T.STRINGTABLE:
            out.append("table")
        elif t == T.LOCALIZE:
            out.append("localize")
        elif t == T.IMAGE:
            out.append("image")
        elif t == T.MAP_ENTS:
            out += ["text", "entities"]
        elif t in (T.COL_MAP_MP, T.COL_MAP_SP):
            if isinstance(node.get("map_ents"), dict):
                out += ["text", "entities"]
            out.append("geometry")
        elif t in (T.GFX_MAP, T.XMODEL):
            out.append("geometry")
        if node.get("_t"):
            out.append("fields")
        out.append("hex")
        return tuple(out)

    def type_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.assets:
            out[a.type_name] = out.get(a.type_name, 0) + 1
        return out

    def asset(self, index: AssetKey) -> AssetRef:
        if isinstance(index, tuple):
            for r in self.inline_assets:
                if r.index == index:
                    return r
            raise EditError(f"inline asset {index!r}: expected one of inline_assets, found none")
        if not 0 <= index < len(self.assets):
            raise EditError(f"asset {index}: expected 0..{len(self.assets) - 1}")
        return self.assets[index]

    def find(self, name: str, type: int | str | None = None) -> AssetRef | None:  # noqa: A002
        want = _type_of(type)
        for r in itertools.chain(self.assets, self.inline_assets):
            if r.name == name and (want is None or r.type == want):
                return r
        return None

    def search(self, text: str, names: bool = True, contents: bool = True) -> list[SearchHit]:
        if not text:
            return []
        needle = text.lower()
        hits: list[SearchHit] = []
        refs = list(itertools.chain(self.assets, self.inline_assets))
        if names:
            hits += [
                SearchHit(r, "name", r.name) for r in refs if r.name and needle in r.name.lower()
            ]
        if not contents:
            return hits
        for r in refs:
            try:
                if r.type in (T.RAWFILE, T.MAP_ENTS):
                    body = self.text(r.index)
                    if needle not in body.lower():
                        continue
                    for n, line in enumerate(body.splitlines(), 1):
                        col = line.lower().find(needle)
                        if col >= 0:
                            hits.append(
                                SearchHit(r, "text", line.strip()[:200], line=n, column=col)
                            )
                elif r.type == T.STRINGTABLE:
                    for ri, row in enumerate(self.table(r.index)):
                        for ci, cell in enumerate(row):
                            if needle in cell.lower():
                                hits.append(SearchHit(r, "cell", cell[:200], row=ri, column=ci))
                elif r.type == T.LOCALIZE:
                    value = self.localize(r.index)[1]
                    if needle in value.lower():
                        hits.append(SearchHit(r, "value", value[:200]))
            except EditError:
                continue
        return hits

    # -- nodes -------------------------------------------------------------------------------

    def _resolve(self, index: AssetKey) -> tuple[Any, int, int]:
        """(node, asset type, top-level asset index)."""
        if isinstance(index, tuple):
            found = self._inline.get(index)
            if found is None:
                raise EditError(f"inline asset {index!r}: expected a key from inline_assets")
            return found
        if not isinstance(index, int | np.integer) or not 0 <= index < len(self.xfile.assets):
            raise EditError(f"asset {index!r}: expected an index 0..{len(self.xfile.assets) - 1}")
        a = self.xfile.assets[int(index)]
        return a.data, a.type, a.index

    def _node(self, index: AssetKey, *types: int) -> tuple[dict, int, int]:
        node, t, top = self._resolve(index)
        if types and t not in types:
            names = " or ".join(type_name(x) for x in types)
            raise EditError(f"asset {index!r}: expected {names}, found {type_name(t)}")
        if not isinstance(node, dict):
            raise EditError(
                f"asset {index!r} ({type_name(t)}): the zone holds a reference to an asset "
                "defined elsewhere, not the asset"
            )
        return node, t, top

    def _text_node(self, index: AssetKey) -> tuple[dict, int, int]:
        node, t, top = self._node(index)
        if t in (T.COL_MAP_MP, T.COL_MAP_SP):
            ents = node.get("map_ents")
            if not isinstance(ents, dict):
                raise EditError(f"asset {index!r}: the clipMap holds no entity string here")
            return ents, T.MAP_ENTS, top
        if t not in (T.RAWFILE, T.MAP_ENTS):
            raise EditError(
                f"asset {index!r}: expected rawfile or map_ents (text), found {type_name(t)}"
                + ("; menus carry no source text in a zone" if t in (T.MENU, T.MENUFILE) else "")
            )
        return node, t, top

    # -- reading -----------------------------------------------------------------------------

    def text(self, index: AssetKey) -> str:
        node, t, _ = self._text_node(index)
        return ct.rawfile_text(node) if t == T.RAWFILE else ct.mapents_text(node)

    def original_text(self, index: AssetKey) -> str:
        """The text as opened (for diff views), whatever edits are applied now."""
        for op in self._undo:
            if op.kind == "text" and op.index == index:
                return op.before
        return self.text(index)

    def table(self, index: AssetKey) -> list[list[str]]:
        node, _, _ = self._node(index, T.STRINGTABLE)
        return ct.table_rows(node)

    def localize(self, index: AssetKey) -> tuple[str, str]:
        node, _, _ = self._node(index, T.LOCALIZE)
        return ct.localize_state(node)

    def image(self, index: AssetKey) -> ImageData:
        node, _, _ = self._node(index, T.IMAGE)
        return im.read(node, self.zone_name, self.pak_dirs())

    def pak_dirs(self) -> list[Path]:
        dirs = [self.path.parent] if self.path is not None else []
        return dirs + [d for d in env.zone_dirs() if d not in dirs]

    def mesh(self, kind: str, index: AssetKey | None = None) -> geo.Mesh:
        if kind == "world":
            node = self._node(index, T.GFX_MAP)[0] if index is not None else None
            if node is None:
                a = geo.first_of(self.xfile, geo.WORLD)
                if a is None:
                    raise EditError(f"{self.zone_name}: expected a gfx_map, found none")
                node = a.data
            return geo.world(self.xfile, node)
        if kind == "collision":
            node = self._node(index, *geo.COLLISION)[0] if index is not None else None
            if node is None:
                a = geo.first_of(self.xfile, geo.COLLISION)
                if a is None:
                    raise EditError(f"{self.zone_name}: expected a col_map, found none")
                node = a.data
            return geo.collision(self.xfile, node, self.zone_name)
        if kind == "model":
            if index is None:
                raise EditError("a model mesh needs the model's asset index or inline key")
            return geo.model(self.xfile, self._node(index, T.XMODEL)[0], self.zone_name)
        raise EditError(f"mesh kind {kind!r}: expected world, collision or model")

    def raw(self, index: AssetKey) -> bytes:
        """The asset's file span: as opened, or written from its node once edited."""
        node, t, top = self._resolve(index)
        edited = top in self._touched()
        if isinstance(index, tuple):
            start, end = self._rw.trace.spans.get(id(node), (None, None))
            if start is None:
                raise EditError(f"inline asset {index!r}: its span is not known")
            if edited:
                raise EditError(
                    f"inline asset {index!r} is edited; its new bytes exist once the "
                    f"asset that loads it (asset {top}) is written"
                )
            return self.content[start:end]
        a = self.xfile.assets[top]
        if edited:
            return write_asset(a)
        return self.content[a.file_start : a.file_end]

    def fields(self, index: AssetKey) -> dict:
        node, _, _ = self._resolve(index)
        if isinstance(node, AssetLink):
            return {"alias": node.raw, "type": node.asset_type, "name": node.name}
        return view(node).to_dict()

    # -- editing -----------------------------------------------------------------------------

    def _push(self, op: _Op) -> None:
        op.token = next(_tokens)
        op.apply()
        self._undo.append(op)
        self._redo.clear()

    def _field_bytes(self, at: int) -> tuple[Any, Any, int]:
        try:
            return self._rw.field_holder(at)
        except RemapError as exc:
            raise EditError(str(exc)) from None

    def _string_text_at(self, event: int) -> str:
        """The original text a pointer event's target names (a string, or a suffix of one)."""
        rows = self.xfile.log.table()
        block, offset = int(rows[event, 4]), int(rows[event, 5])
        a = self._rw.layout().find(block, offset, NONE)
        fp = int(rows[a.event, 1]) + offset - a.old_start
        end = self.content.find(b"\0", fp)
        return self.content[fp:end].decode("latin-1")

    def _own_string(
        self, sets: _Sets, node: dict, key: Any, top: int, exclude: frozenset = frozenset()
    ) -> tuple[set[int], int]:
        """Queue assignments that make node[key] a string only this field uses: if the field
        points at a string loaded earlier, it becomes inline; if other string fields point
        at this one, each gets an inline copy of the old text. Returns (other top-level
        assets touched, number of copies)."""
        others: set[int] = set()
        at = self._string_field_of.get((id(node), key))
        if at is not None:
            self._make_inline(sets, at)
            return others, 0
        copies = 0
        rows = self.xfile.log.table()
        for event in self._rw.sharers(node, key, string=True):
            at = int(rows[event, 1])
            sf = self._rw.trace.string_fields.get(at) if at != NONE else None
            owner = self._rw.owner(event)
            if sf is None:
                raise EditError(
                    f"asset {top}: the string {node.get(key)!r} is also the target of a "
                    f"non-string pointer (field at {at:#x}, asset {owner}); changing it "
                    "would change that asset too"
                )
            holder, _, _ = self._field_bytes(at)
            if id(holder) in exclude:
                continue
            if self._make_inline(sets, at):
                snode, skey = sf
                sets.put(snode, skey, self._string_text_at(event))
                copies += 1
                if owner != top:
                    others.add(owner)
        return others, copies

    def _make_inline(self, sets: _Sets, at: int) -> bool:
        """Queue setting the pointer field at original file offset ``at`` to -1; False when
        it already is."""
        holder, hkey, off = self._field_bytes(at)
        current = bytes(sets.get(holder, hkey))
        if current[off : off + 4] == INLINE4:
            return False
        sets.put(holder, hkey, current[:off] + INLINE4 + current[off + 4 :])
        return True

    def _no_sharers(self, node: dict, key: str, what: str, index: AssetKey) -> None:
        events = self._rw.sharers(node, key, string=False)
        if events:
            at = int(self.xfile.log.table()[events[0], 1])
            raise EditError(
                f"asset {index!r}: the {what} is also the target of {len(events)} pointer(s) "
                f"(first: field at {at:#x}); expected none, so it cannot change on its own"
            )

    def set_text(self, index: AssetKey, text: str) -> None:
        node, t, top = self._text_node(index)
        before = self.text(index)
        if text == before:
            return
        if t == T.RAWFILE:
            self._no_sharers(node, "buffer", "buffer", index)
            header, buffer = ct.rawfile_bytes(node, text)
            sets = [
                (node, "header", node["header"], header),
                (node, "buffer", node["buffer"], buffer),
            ]
        else:
            self._no_sharers(node, "entity_string", "entity string", index)
            header, data = ct.mapents_bytes(node, text)
            sets = [
                (node, "header", node["header"], header),
                (node, "entity_string", node["entity_string"], data),
            ]
        self._push(_Op(index, "text", before, text, "", sets, {top}))

    def _cell_index_after(self, node: dict, cells: list, keep_order: bool) -> bytes:
        shadow = {"cells": cells, "cell_index": node.get("cell_index")}
        return ct.resort_index(shadow, keep_order)

    def set_cell(self, index: AssetKey, row: int, col: int, text: str) -> None:
        node, _, top = self._node(index, T.STRINGTABLE)
        columns, rows = ct.table_shape(node)
        if not (0 <= row < rows and 0 <= col < columns):
            raise EditError(
                f"stringtable {node.get('name')}: expected row 0..{rows - 1} and column "
                f"0..{columns - 1}, found ({row}, {col})"
            )
        element = node["cells"][row * columns + col]
        before = element.get("string") or ""
        if text == before:
            return
        ct.encode_text(text, f"stringtable {node.get('name')} cell ({row}, {col})")
        sets = _Sets()
        others, copies = self._own_string(sets, element, "string", top)
        new_raw = ct.cell_raw(text)
        sets.put(element, "raw", new_raw)
        sets.put(element, "string", text)
        cells = list(node["cells"])
        cells[row * columns + col] = dict(element, raw=new_raw)
        sets.put(node, "cell_index", self._cell_index_after(node, cells, keep_order=True))
        detail = f"row {row}, column {col}" + _copies_note(copies, others)
        self._push(_Op(index, "cell", before, text, detail, sets, {top} | others, others))

    def add_row(
        self, index: AssetKey, values: list[str] | None = None, at: int | None = None
    ) -> int:
        """Addition to the contract: insert a row (default: empty cells, at the end).
        Returns the new row's number."""
        node, _, top = self._node(index, T.STRINGTABLE)
        columns, rows = ct.table_shape(node)
        if columns <= 0:
            raise EditError(f"stringtable {node.get('name')}: expected columns, found {columns}")
        values = [""] * columns if values is None else [str(v) for v in values]
        if len(values) != columns:
            raise EditError(
                f"stringtable {node.get('name')}: expected {columns} values, found {len(values)}"
            )
        at = rows if at is None else at
        if not 0 <= at <= rows:
            raise EditError(f"stringtable {node.get('name')}: expected a row 0..{rows}, found {at}")
        for v in values:
            ct.encode_text(v, f"stringtable {node.get('name')} new row")
        new = [{"_t": "StringTableCell", "raw": ct.cell_raw(v), "string": v} for v in values]
        old_cells = node.get("cells") or []
        cells = old_cells[: at * columns] + new + old_cells[at * columns :]
        sets = [
            (node, "header", node["header"], ct.table_header(node, rows + 1)),
            (node, "cells", node.get("cells"), cells),
            (
                node,
                "cell_index",
                node.get("cell_index"),
                self._cell_index_after(node, cells, False),
            ),
        ]
        self._push(_Op(index, "row_added", None, values, f"row {at}", sets, {top}))
        return at

    def remove_row(self, index: AssetKey, row: int) -> list[str]:
        """Addition to the contract: remove a row. Returns the removed cells."""
        node, _, top = self._node(index, T.STRINGTABLE)
        columns, rows = ct.table_shape(node)
        if not 0 <= row < rows:
            raise EditError(
                f"stringtable {node.get('name')}: expected a row 0..{rows - 1}, found {row}"
            )
        old_cells = node["cells"]
        gone = old_cells[row * columns : (row + 1) * columns]
        values = [e.get("string") or "" for e in gone]
        exclude = frozenset(id(e) for e in gone)
        sets = _Sets()
        others: set[int] = set()
        copies = 0
        for e in gone:
            if (id(e), "string") in self._string_field_of:
                continue  # it points at a string elsewhere; nothing points into it
            o, n = self._own_string(sets, e, "string", top, exclude)
            others |= o
            copies += n
        cells = old_cells[: row * columns] + old_cells[(row + 1) * columns :]
        sets.put(node, "header", ct.table_header(node, rows - 1))
        sets.put(node, "cells", cells)
        sets.put(node, "cell_index", self._cell_index_after(node, cells, False))
        detail = f"row {row}" + _copies_note(copies, others)
        self._push(_Op(index, "row_removed", values, None, detail, sets, {top} | others, others))
        return values

    def set_localize(self, index: AssetKey, value: str) -> None:
        node, _, top = self._node(index, T.LOCALIZE)
        before = node.get("value") or ""
        if value == before:
            return
        ct.encode_text(value, f"localize {node.get('name')}")
        sets = _Sets()
        others, copies = self._own_string(sets, node, "value", top)
        sets.put(node, "value", value)
        self._push(
            _Op(
                index,
                "localize",
                before,
                value,
                _copies_note(copies, others).lstrip("; "),
                sets,
                {top} | others,
                others,
            )  # fmt: skip
        )

    def replace_image(self, index: AssetKey, data) -> None:
        """New pixels (RGBA array, six for a cube map, or DDS bytes) for an image whose
        pixels are in the zone; same width, height, format and mip count."""
        node, _, top = self._node(index, T.IMAGE)
        reason = im.why_not_replaceable(node, self.zone_name)
        if reason:
            raise EditError(f"image {node.get('name')}: cannot replace: {reason}")
        self._no_sharers(node, "pixels", "pixel data", index)
        stored = im.encode(node, data, f"image {node.get('name')}")
        old = im.stored_pixels(node) or b""
        sets = [(node, "pixels", node.get("pixels"), im.stored_value(node, stored))]
        before = {"bytes": len(old), "sha1": hashlib.sha1(old).hexdigest()}
        after = {"bytes": len(stored), "sha1": hashlib.sha1(stored).hexdigest()}
        self._push(_Op(index, "image", before, after, im.where(node), sets, {top}))

    def _struct_view(self, node: dict, path: str) -> tuple[StructView, str, Any, dict]:
        """(view over a scratch copy, field name, node key, scratch) for a field path:
        "name" on the node's own struct, or "key:name" / "key[i]:name" on a sub-struct."""
        v = view(node)
        if ":" in path:
            where, name = path.split(":", 1)
            key, element = where, None
            if where.endswith("]") and "[" in where:
                key, _, num = where[:-1].partition("[")
                element = int(num)
            try:
                sv = v.struct(key, element)
            except FieldError as exc:
                raise EditError(str(exc)) from None
        else:
            sv, name = v.fields, path
            if sv is None:
                raise EditError(f"{node.get('_t')}: has no struct schema")
        scratch = {sv._key: bytes(node[sv._key])}
        return StructView(sv.struct, scratch, sv._key, sv._base), name, sv._key, scratch

    def set_field(self, index: AssetKey, path: str, value: Any) -> None:
        """A schema field by name (``"lodInfo[0].dist"``) on the asset's own struct, or on
        a sub-struct as ``"key:field"`` / ``"key[i]:field"``. Pointer and count fields are
        refused (they change layout); the asset is test-written before the edit is kept."""
        node, _, top = self._node(index)
        sv, name, key, scratch = self._struct_view(node, path)
        try:
            before = sv[name]
            sv[name] = value
            after = sv[name]
        except (FieldError, struct.error, ValueError, TypeError) as exc:
            raise EditError(f"asset {index!r} field {path}: {exc}") from None
        if before == after:
            return
        op = _Op(index, "field", before, after, path, [(node, key, node[key], scratch[key])], {top})
        self._push(op)
        try:
            write_asset(self.xfile.assets[top])
        except (XFileError, struct.error, ValueError) as exc:
            self._undo.pop()
            op.revert()
            raise EditError(
                f"asset {index!r} field {path}: the asset no longer writes: {exc}"
            ) from None

    # -- history -----------------------------------------------------------------------------

    @property
    def dirty(self) -> bool:
        return (self._undo[-1].token if self._undo else 0) != self._saved_token

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> Change | None:
        if not self._undo:
            return None
        op = self._undo.pop()
        op.revert()
        self._redo.append(op)
        return _change(op)

    def redo(self) -> Change | None:
        if not self._redo:
            return None
        op = self._redo.pop()
        op.apply()
        self._undo.append(op)
        return _change(op)

    def changes(self) -> list[Change]:
        return [_change(op) for op in self._undo]

    def _touched(self) -> set[int]:
        out: set[int] = set()
        for op in self._undo:
            out |= op.touched
        return out

    # -- saving ------------------------------------------------------------------------------

    def build(self) -> bytes:
        """The edited content (the decompressed zone), re-laid out; the source content when
        nothing is edited."""
        if self.parse_problems:
            raise EditError(
                f"{self.zone_name}: the zone does not parse exactly, so it cannot be "
                f"rewritten: {self.parse_problems[0]}"
            )
        if not self._undo:
            return self.content
        try:
            return self._rw.build().content
        except (RemapError, XFileError) as exc:
            raise EditError(f"{self.zone_name}: {exc}") from None

    def save(self, new_path: str | Path, verify: bool = True) -> SaveReport:
        from opent5.edit.verify import verify_saved

        target = check_target(new_path, self.path)
        content = self.build()
        self._zone.content[:] = content
        built = self._zone.build()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(built.data)
        report = SaveReport(
            path=target,
            bytes=len(built.data),
            assets_checked=0,
            assets_changed=len(self._touched()),
            verified=False,
            problems=[],
            signature_note=None,
            sha1=hashlib.sha1(built.data).hexdigest(),
            identical=built.identical,
        )
        if self.signed:
            report.signature_note = SIGNATURE_INTACT if built.identical else SIGNATURE_NOTE
        if verify:
            problems, details = verify_saved(self, target, content)
            report.problems = problems
            report.details = details
            report.assets_checked = details.get("assets_checked", 0)
            report.verified = not problems
        self._saved_token = self._undo[-1].token if self._undo else 0
        return report

    # -- for verification --------------------------------------------------------------------

    def expectations(self) -> list[tuple[AssetKey, str, Any]]:
        """What each edited asset must read back as: (key, kind, value)."""
        out = []
        seen = set()
        for op in self._undo:
            kind = op.kind
            if kind in ("row_added", "row_removed", "cell"):
                kind = "table"
            if (op.index, kind, op.detail if kind == "field" else "") in seen:
                continue
            seen.add((op.index, kind, op.detail if kind == "field" else ""))
            if kind == "text":
                out.append((op.index, kind, self.text(op.index)))
            elif kind == "table":
                out.append((op.index, kind, self.table(op.index)))
            elif kind == "localize":
                out.append((op.index, kind, self.localize(op.index)))
            elif kind == "image":
                node, _, _ = self._node(op.index, T.IMAGE)
                out.append((op.index, kind, im.stored_pixels(node)))
            elif kind == "field":
                node, _, _ = self._resolve(op.index)
                sv, name, _, _ = self._struct_view(node, op.detail)
                out.append((op.index, "field:" + op.detail, sv[name]))
        return out

    def edited_assets(self) -> set[int]:
        """Top-level assets edited directly (not counting shared-string copies)."""
        return {self._resolve(op.index)[2] for op in self._undo}

    def side_effect_assets(self) -> set[int]:
        out: set[int] = set()
        for op in self._undo:
            out |= op.side_effects
        return out


def _copies_note(copies: int, others: set[int]) -> str:
    if not copies:
        return ""
    where = f" (also in asset(s) {', '.join(str(o) for o in sorted(others))})" if others else ""
    return f"; {copies} other field(s) shared the old string and now hold their own copy{where}"


def _change(op: _Op) -> Change:
    return Change(op.index, op.kind, op.before, op.after, op.detail)


def _type_of(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    from opent5.xfile.constants import ASSET_TYPE_NAMES

    if value in ASSET_TYPE_NAMES:
        return ASSET_TYPE_NAMES.index(value)
    raise EditError(f"asset type {value!r}: expected a type name such as rawfile or image")


def game_folders() -> list[Path]:
    """Every configured game folder (zones, patch, DLC) and the ELF's folder."""
    out = list(env.zone_dirs())
    elf = env.path_of("OPENT5_ELF")
    if elf is not None:
        out.append(elf.parent)
    return out


def check_target(new_path: str | Path, source: Path | None) -> Path:
    """Refuse to write over the source or into any configured game folder."""
    target = Path(new_path).expanduser()
    resolved = target.resolve()
    if source is not None and resolved == Path(source).resolve():
        raise EditError(f"{target}: expected a new path, found the source zone itself")
    for folder in game_folders():
        try:
            resolved.relative_to(folder.resolve())
        except ValueError:
            continue
        raise EditError(
            f"{target}: expected a path outside the game folders, found one inside {folder}"
        )
    if resolved.is_dir():
        raise EditError(f"{target}: expected a file path, found a directory")
    return target
