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
original linker does for identical text) stay correct. ``shared_with`` lists the fields
that share a string. The string editors take ``share``: "split" (the default) gives every
other field pointing at the string its own inline copy of the old text, so the edit changes
this field only; "all" edits the stored string itself, so every field sharing it changes
(the string is resized and every pointer remapped). The change's ``detail`` records which.
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
from opent5.container.pak import Pak
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
    SharedField,
)
from opent5.export.nodes import walk
from opent5.xfile import REGISTRY, XFileError
from opent5.xfile.constants import PTR_INLINE, type_name
from opent5.xfile.constants import AssetType as T
from opent5.xfile.events import NONE, EventKind, PtrKind
from opent5.xfile.model import write_asset
from opent5.xfile.remap import RemapError, Rewrite
from opent5.xfile.schema import FieldError, StructView, view
from opent5.xfile.stream import AssetLink

INLINE4 = struct.pack(">I", PTR_INLINE)

#: Said whenever a save writes .pak files.
PAK_NOTE = (
    "streamed pixels were written to .pak file(s) beside the zone: copy the .pak(s) and the "
    ".ff together into the game folder (the game opens <zone>.pak from the folder of <zone>.ff)"
)

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
    #: Further (asset key, kind) pairs that must read back as edited (share="all").
    extra: list[tuple[AssetKey, str]] = field(default_factory=list)
    #: Other assets whose content changed (Change.also).
    also: tuple = ()

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

    def __init__(self, data: bytes, path: Path | None = None, progress=None):
        self._source = bytes(data)
        self.path = Path(path) if path is not None else None
        try:
            self._zone = Zone.open(self._source, progress=progress)
        except FastFileError as exc:
            raise EditError(f"{path or 'zone'}: not a fastfile this tool reads: {exc}") from None
        self.content = bytes(self._zone.content)
        self.zone_name = self._zone.name
        self.signed = carries_console_signature(bytes(self._zone.header))
        try:
            self._rw = Rewrite(self.content, progress)
        except XFileError as exc:
            raise EditError(f"{self.zone_name}: the zone does not parse: {exc}") from None
        self.xfile = self._rw.xfile
        self.parse_problems = self.xfile.problems()
        self._undo: list[_Op] = []
        self._redo: list[_Op] = []
        self._saved_token = 0
        #: Pending .pak entry bytes for streamed images: (slot, entry) -> bytes, or None
        #: once undone. Written beside the zone by ``save`` (opent5.edit.images).
        self._pak_parts: dict[tuple[int, int], bytes | None] = {}
        self._string_field_of = {
            (id(node), key): at for at, (node, key) in self._rw.trace.string_fields.items()
        }
        self._shares: _ShareIndex | None = None
        self._collect()

    @classmethod
    def open(cls, source: str | Path | bytes, name: str | None = None, progress=None) -> Document:
        """Open a .ff by path, or from its bytes (``name`` is then only a label).
        ``progress(stage, done, total)`` is optional: chunk counts while decrypting and
        inflating, asset counts while parsing."""
        if isinstance(source, bytes | bytearray | memoryview):
            doc = cls(bytes(source), Path(name) if name else None, progress)
        else:
            path = Path(source)
            if not path.is_file():
                raise EditError(f"{path}: expected a fastfile, found no such file")
            doc = cls(path.read_bytes(), path, progress)
        return doc

    def index_shared_strings(self) -> int:
        """Build the shared-string index now (``shared_with`` builds it on first use);
        returns the number of strings that more than one field reads."""
        return len(self._share_index().members)

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
        return im.read(node, self.zone_name, self.pak_dirs(), self._pak_parts)

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

    def set_cell(
        self, index: AssetKey, row: int, col: int, text: str, share: str = "split"
    ) -> None:
        """A stringtable cell. ``share`` says what happens when the cell's string is shared
        with other fields: "split" (only this cell changes) or "all" (every field sharing
        the stored string changes)."""
        _check_share(share)
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
        group = self._group(element, "string")
        if share == "all" and group is not None:
            touched, side, note, extra = self._set_all(sets, group, text)
            detail = f"row {row}, column {col}; {note}"
            op = _Op(index, "cell", before, text, detail, sets, {top} | touched, side)
            op.extra = extra
            op.also = _also(index, top, extra, touched - side)
            self._push(op)
            return
        others, copies = self._own_string(sets, element, "string", top)
        new_raw = ct.cell_raw(text)
        sets.put(element, "raw", new_raw)
        sets.put(element, "string", text)
        cells = list(node["cells"])
        cells[row * columns + col] = dict(element, raw=new_raw)
        sets.put(node, "cell_index", self._cell_index_after(node, cells, keep_order=True))
        detail = f"row {row}, column {col}" + _split_note(group, copies, others)
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

    def set_localize(self, index: AssetKey, value: str, share: str = "split") -> None:
        """A localize entry's value. ``share`` as for ``set_cell``: "split" changes this
        key only, "all" every key (and field) that shares the stored string."""
        _check_share(share)
        node, _, top = self._node(index, T.LOCALIZE)
        before = node.get("value") or ""
        if value == before:
            return
        ct.encode_text(value, f"localize {node.get('name')}")
        sets = _Sets()
        group = self._group(node, "value")
        if share == "all" and group is not None:
            touched, side, note, extra = self._set_all(sets, group, value)
            op = _Op(index, "localize", before, value, note, sets, {top} | touched, side)
            op.extra = extra
            op.also = _also(index, top, extra, touched - side)
            self._push(op)
            return
        others, copies = self._own_string(sets, node, "value", top)
        sets.put(node, "value", value)
        detail = _split_note(group, copies, others).lstrip("; ")
        self._push(_Op(index, "localize", before, value, detail, sets, {top} | others, others))

    # -- shared strings ------------------------------------------------------------------------

    def _share_index(self) -> _ShareIndex:
        if self._shares is None:
            self._shares = _ShareIndex(self)
        return self._shares

    def _pointer_inline(self, sets: _Sets | None, at: int) -> bool:
        """True when the string pointer field at original file offset ``at`` is inline now
        (in the document, or in the assignments queued so far)."""
        holder, hkey, off = self._field_bytes(at)
        current = bytes(sets.get(holder, hkey) if sets is not None else holder[hkey])
        return current[off : off + 4] == INLINE4

    def _group(self, node: dict, key: Any) -> _Group | None:
        """The fields sharing node[key]'s stored string now, or None when it is not shared."""
        shares = self._share_index()
        at = self._string_field_of.get((id(node), key))
        if at is not None:
            if at not in shares.target or self._pointer_inline(None, at):
                return None
            event = shares.target[at][0]
        else:
            event = self._rw.event_of(("S", id(node), key))
            if event is None:
                return None
        owner = shares.owner_of(event)
        if owner is None:
            return None
        members = [m for m in shares.members.get(event, []) if not self._pointer_inline(None, m[0])]
        if not members:
            return None
        return _Group(event, owner[0], owner[1], members, (id(node), key))

    def _string_node(self, index: AssetKey, row, col, field_name) -> tuple[dict, Any]:
        node, t, _ = self._node(index)
        if t == T.LOCALIZE and field_name in (None, "value"):
            return node, "value"
        if t == T.STRINGTABLE and field_name is None:
            columns, rows = ct.table_shape(node)
            if row is None or col is None or not (0 <= row < rows and 0 <= col < columns):
                raise EditError(
                    f"stringtable {node.get('name')}: expected a cell row 0..{rows - 1}, "
                    f"column 0..{columns - 1}, found ({row}, {col})"
                )
            return node["cells"][row * columns + col], "string"
        if field_name is None or not isinstance(node.get(field_name), str):
            raise EditError(
                f"asset {index!r} ({type_name(t)}): expected the name of a string field, "
                f"found {field_name!r}"
            )
        return node, field_name

    def shared_with(
        self,
        index: AssetKey,
        row: int | None = None,
        col: int | None = None,
        field: str | None = None,
    ) -> list[SharedField]:
        """The other fields that hold the same stored string as this one: a localize value,
        a stringtable cell (``row``, ``col``), or a string ``field`` of the asset's node.
        Empty when the string is not shared."""
        node, key = self._string_node(index, row, col, field)
        group = self._group(node, key)
        if group is None:
            return []
        shares = self._share_index()
        out: list[SharedField] = []
        if (id(group.owner), group.owner_key) != group.asked:
            out.append(shares.describe(group.owner, group.owner_key, group.event, owner=True))
        for at, inner, event in group.members:
            snode, skey = self._rw.trace.string_fields[at]
            if (id(snode), skey) == group.asked:
                continue
            out.append(shares.describe(snode, skey, event, suffix=inner > 0))
        return out

    def _set_all(
        self, sets: _Sets, group: _Group, text: str
    ) -> tuple[set[int], set[int], str, list[tuple[AssetKey, str]]]:
        """Queue edits that change the stored string itself: the owner and every field
        pointing at its start take ``text``; a field pointing into the middle (a suffix)
        cannot follow, so it gets its own copy of the old text. Returns (top-level assets
        touched, assets that only received copies, detail, read-back expectations)."""
        shares = self._share_index()
        rows = self.xfile.log.table()
        movable = (PtrKind.OFFSET, PtrKind.ALIAS_REF)
        for event in self._rw.sharers(group.owner, group.owner_key, string=True):
            at = int(rows[event, 1])
            if int(rows[event, 3]) in movable and at not in self._rw.trace.string_fields:
                raise EditError(
                    f"the string {group.owner.get(group.owner_key)!r} is also the target of a "
                    f"non-string pointer (field at {at:#x}, asset {self._rw.owner(event)}); "
                    "changing it would change that asset too"
                )
        if _is_name(group.owner, group.owner_key):
            raise EditError(
                f"the string {group.owner.get(group.owner_key)!r} is stored as the name of "
                f"asset {self._rw.owner(group.event)}; share='all' would rename that asset. "
                "Expected share='split' for this string"
            )
        changed: list[tuple[dict, Any, int, int]] = [
            (group.owner, group.owner_key, self._rw.owner(group.event), group.event)
        ]
        split: set[int] = set()
        copies = 0
        for at, inner, event in group.members:
            snode, skey = self._rw.trace.string_fields[at]
            if inner > 0 or _is_name(snode, skey):
                if self._make_inline(sets, at):
                    sets.put(snode, skey, self._string_text_at(event))
                    copies += 1
                    split.add(self._rw.owner(event))
                continue
            changed.append((snode, skey, self._rw.owner(event), event))
        tables: dict[int, tuple[dict, int]] = {}
        extra: list[tuple[AssetKey, str]] = []
        labels: list[str] = []
        tops: set[int] = set()
        for n, k, top, event in changed:
            sets.put(n, k, text)
            tops.add(top)
            where = shares.locate(n, top)
            if n.get("_t") == "StringTableCell":
                raw = bytes(sets.get(n, "raw"))
                sets.put(n, "raw", raw[:4] + struct.pack(">I", ct.string_hash(text)))
                cell = shares.cell_of(n)
                if cell is not None:
                    tables[id(cell[0])] = (cell[0], cell[1])
                    extra.append((cell[1], "table"))
            elif where[1] == "localize":
                extra.append((where[0], "localize"))
            if (id(n), k) != group.asked:
                labels.append(shares.describe(n, k, event).label)
        for table, _ in tables.values():
            cells = [dict(e, raw=sets.get(e, "raw")) for e in table["cells"]]
            sets.put(table, "cell_index", self._cell_index_after(table, cells, keep_order=True))
        note = f"share=all: the stored string changed for {len(changed)} field(s)"
        if labels:
            shown = ", ".join(labels[:6]) + (
                f" and {len(labels) - 6} more" if len(labels) > 6 else ""
            )
            note += f" (also {shown})"
        if copies:
            note += (
                f"; {copies} field(s) that read the end of it or name an asset kept the old "
                "text as their own copy"
            )
        return tops | split, split - tops, note, extra

    def replace_image(
        self, index: AssetKey, data, allow_shared: bool = False, resize: bool = False
    ) -> None:
        """New pixels (RGBA array, six for a cube map, or DDS bytes); same width, height,
        format and mip count. A streamed image's parts go to its .pak files (written by
        ``save`` beside the zone); parts in a shared pak (images_low, common, ui_mp, ...)
        are left as they are unless ``allow_shared``. ``resize`` lets a streamed image
        take another power-of-two size: its records and GfxImage header change and its
        larger levels are laid out again in the level pak (``images.resize_streamed``)."""
        node, _, top = self._node(index, T.IMAGE)
        reason = im.why_not_replaceable(node, self.zone_name)
        if reason:
            raise EditError(f"image {node.get('name')}: cannot replace: {reason}")
        if im.where(node) == "pak":
            f = view(node).fields
            size = im.input_size(data)
            if size is not None and size != (f["texture.width"], f["texture.height"]):
                if resize:
                    self._resize_streamed(index, node, top, data, allow_shared)
                    return
                hint = (
                    "; pass resize=True (command line: --resize) to change its size"
                    if im.why_not_resizable(node) is None
                    else ""
                )
                raise EditError(
                    f"image {node.get('name')}: expected {f['texture.width']}x"
                    f"{f['texture.height']} pixels, found {size[0]}x{size[1]}{hint}"
                )
            self._replace_streamed(index, node, data, allow_shared)
            return
        if resize:
            raise EditError(
                f"image {node.get('name')}: only streamed (.pak) images change size; this "
                f"one's pixels are {im.where(node)} in the zone"
            )
        self._no_sharers(node, "pixels", "pixel data", index)
        stored = im.encode(node, data, f"image {node.get('name')}")
        old = im.stored_pixels(node) or b""
        sets = [(node, "pixels", node.get("pixels"), im.stored_value(node, stored))]
        before = {"bytes": len(old), "sha1": hashlib.sha1(old).hexdigest()}
        after = {"bytes": len(stored), "sha1": hashlib.sha1(stored).hexdigest()}
        self._push(_Op(index, "image", before, after, im.where(node), sets, {top}))

    def _replace_streamed(self, index: AssetKey, node: dict, data, allow_shared: bool) -> None:
        what = f"image {node.get('name')}"
        parts = im.encode_parts(node, data, what)
        self._push_streamed(index, node, parts, allow_shared, [], set(), "")

    def _resize_streamed(self, index: AssetKey, node: dict, top: int, data, allow_shared) -> None:
        what = f"image {node.get('name')}"
        f = view(node).fields
        plan = im.resize_streamed(node, data, what, self._next_level_entry())
        sets = [(node, "header", node["header"], plan.header)]
        note = (
            f"size {f['texture.width']}x{f['texture.height']} ({f['texture.mipmap']} mips) -> "
            f"{plan.width}x{plan.height} ({plan.levels} mips), {len(plan.parts)} parts"
        )
        if plan.appended:
            note += f"; level pak entries added: {', '.join(map(str, plan.appended))}"
        if plan.unused:
            note += (
                f"; level pak entries no longer read (left as they were): "
                f"{', '.join(map(str, plan.unused))}"
            )
        if not plan.linker_layout:
            note += "; the parts are not split the way the linker splits an image of this size"
        self._push_streamed(index, node, plan.parts, allow_shared, sets, {top}, note)

    def _next_level_entry(self) -> int:
        """The index the next appended level-pak entry takes: after the pak's last entry
        and after any appended by edits still applied."""
        source = im.find_pak(self.zone_name, self.pak_dirs(), im.LEVEL_SLOT)
        if source is None:
            raise EditError(
                f"{self.zone_name}: expected {self.zone_name}.pak in "
                f"{', '.join(str(d) for d in self.pak_dirs()) or 'no folder'}, found none"
            )
        with Pak.open(source) as pak:
            count = pak.count
        used = [e for (slot, e), v in self._pak_parts.items() if slot == im.LEVEL_SLOT and v]
        return max([count - 1, *used]) + 1

    def _push_streamed(
        self, index, node, parts, allow_shared: bool, sets: list, touched: set, note: str
    ) -> None:
        what = f"image {node.get('name')}"
        level = [(p, b) for p, b in parts if p.slot == im.LEVEL_SLOT]
        shared = [(p, b) for p, b in parts if p.slot != im.LEVEL_SLOT]
        if not level and not allow_shared:
            names = sorted({im.pak_file_name(self.zone_name, p.slot) for p, _ in shared})
            raise EditError(
                f"{what}: every part streams from a shared pak ({', '.join(names)}); "
                "changing it changes every zone that uses this image: pass allow_shared=True "
                "(command line: --allow-shared) to write it"
            )
        chosen = parts if allow_shared else level
        sets = list(sets)
        for p, b in chosen:
            key = (p.slot, p.entry)
            sets.append((self._pak_parts, key, self._pak_parts.get(key), b))

        def label(p) -> str:
            return (
                f"{im.pak_file_name(self.zone_name, p.slot)} entry {p.entry} ({p.width}x{p.height})"
            )

        detail = (note + "; " if note else "") + "pak: " + ", ".join(label(p) for p, _ in chosen)
        kept = [p for p, _ in shared if not allow_shared]
        if kept:
            detail += (
                "; kept as they were (shared pak, pass allow_shared=True, command line "
                "--allow-shared, to write): "
                + ", ".join(label(p) for p in kept)
                + ". Those smaller mips still show the old picture at a distance"
            )
        elif shared:
            detail += "; " + "; ".join(
                sorted(
                    {
                        im.SHARED_NOTE.format(pak=im.pak_file_name(self.zone_name, p.slot))
                        for p, _ in shared
                    }
                )
            )
        old = b"".join(
            self._pak_parts.get((p.slot, p.entry)) or b"" for p, _ in chosen
        )  # fmt: skip
        new = b"".join(b for _, b in chosen)
        before = {"pak_parts": len(chosen), "sha1": hashlib.sha1(old).hexdigest() if old else None}
        after = {"pak_parts": len(chosen), "bytes": len(new), "sha1": hashlib.sha1(new).hexdigest()}
        # Same size: the zone does not change (no top-level asset touched), the paks do.
        # Another size: the GfxImage header changes too, in the asset that loads it.
        self._push(_Op(index, "image", before, after, detail, sets, set(touched)))

    def pak_edits(self) -> dict[tuple[int, int], bytes]:
        """Pending .pak entry bytes, (slot, entry) -> bytes (undone edits left out)."""
        return {k: v for k, v in self._pak_parts.items() if v is not None}

    def _struct_view(self, node: dict, path: str) -> tuple[StructView, str, Any, dict]:
        """(view over a scratch copy, field name, node key, scratch) for a field path:
        "name" on the node's own struct, or "key:name" / "key[i]:name" on a sub-struct;
        any number of "child/" or "child[i]/" steps first reach a nested node."""
        node, path = _nested_node(node, path)
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

    def field_info(self, index: AssetKey, path: str) -> dict[str, Any]:
        """What ``set_field`` would edit at ``path``: the field's ``type`` ("f32", "vec3",
        "u16[4]"), ``role`` (None, "ptr", "count", "scrstr", "enum", "flags"), ``names`` (for
        an enum), ``value`` now, and ``editable`` with the ``reason`` when it is not.
        Raises EditError when the path names no field."""
        node, _, _ = self._node(index)
        sv, name, _, _ = self._struct_view(node, path)
        try:
            f = sv.struct.field(name)
            value = sv[name]
        except FieldError as exc:
            raise EditError(f"asset {index!r} field {path}: {exc}") from None
        reason = None
        if f.role == "ptr":
            reason = "a pointer: it names data elsewhere in the zone, so it changes with layout"
        elif f.role == "count":
            what = f" of {f.counts!r}" if f.counts else ""
            reason = (
                f"a count: it sizes the array{what}; changing it means adding or removing "
                "elements, not setting the number"
            )
        elif f.type.startswith(("char[", "bytes[")):
            reason = "fixed-size text or bytes: not edited here"
        return {
            "name": name,
            "type": f.type,
            "role": f.role,
            "names": dict(f.names) if f.names else None,
            "value": value,
            "editable": reason is None,
            "reason": reason,
        }

    def set_field(self, index: AssetKey, path: str, value: Any) -> None:
        """A schema field by name (``"lodInfo[0].dist"``) on the asset's own struct, or on
        a sub-struct as ``"key:field"`` / ``"key[i]:field"``; ``"child/"`` and
        ``"child[i]/"`` steps first reach a node nested in the asset (``"materials[0]/
        material/flags"``). Pointer and count fields are refused (they change layout); the
        asset is test-written before the edit is kept."""
        node, _, top = self._node(index)
        sv, name, key, scratch = self._struct_view(node, path)
        node = _nested_node(node, path)[0]
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

    def touch_asset(self, index: AssetKey) -> None:
        """Record that an asset's node was edited in place (outside the field/text/image
        editors), so ``build`` re-lays it out and ``save`` verifies it. The caller owns the
        node mutation and its reversal; this only marks the asset and keeps one undo/redo
        step in step with that mutation. Used by the map editor's clip edits, which change
        the clipMap node directly through ``opent5.convert.propclip`` and the Rewrite."""
        _, _, top = self._resolve(index)
        self._push(_Op(index, "clip", None, None, "", [], {top}))

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

    def build(self, progress=None) -> bytes:
        """The edited content (the decompressed zone), re-laid out; the source content when
        nothing is edited."""
        if self.parse_problems:
            raise EditError(
                f"{self.zone_name}: the zone does not parse exactly, so it cannot be "
                f"rewritten: {self.parse_problems[0]}"
            )
        if not self._undo or not self._touched():
            # Nothing in the zone changed (no edits, or only streamed pixels in .pak files).
            return self.content
        try:
            return self._rw.build(progress=progress).content
        except (RemapError, XFileError) as exc:
            raise EditError(f"{self.zone_name}: {exc}") from None

    def save(self, new_path: str | Path, verify: bool = True, progress=None) -> SaveReport:
        """Write a new file. ``progress(stage, done, total)`` is optional: assets written,
        pointers remapped, chunks compressed and encrypted, then the verification's chunks
        and assets."""
        from opent5.edit.verify import verify_saved

        target = check_target(new_path, self.path)
        paks = self.pak_edits()
        pak_targets = []
        if paks:
            pak_targets = im.pak_targets(
                self.zone_name, self.pak_dirs(), paks, target.parent, target.stem
            )
            for _, source, out, _ in pak_targets:
                if out.resolve() == source.resolve():
                    raise EditError(
                        f"{out}: expected a new path, found the source pak itself (save the "
                        "zone to another folder)"
                    )
                check_target(out, None)
        content = self.build(progress)
        self._zone.content[:] = content
        built = self._zone.build(progress=progress)
        target.parent.mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress("Writing the file", 0, 1)
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
            problems, details = verify_saved(self, target, content, progress)
            report.problems = problems
            report.details = details
            report.assets_checked = details.get("assets_checked", 0)
            report.verified = not problems
        if pak_targets:
            self._save_paks(report, pak_targets, paks, target, verify, progress)
        self._saved_token = self._undo[-1].token if self._undo else 0
        return report

    def _save_paks(self, report, pak_targets, paks, target: Path, verify: bool, progress) -> None:
        """Write the edited .pak files beside the zone; with verify, re-read them and decode
        every streamed image edited (docs/research/pak.md 6)."""
        if progress is not None:
            progress("Writing .pak files", 0, len(pak_targets))
        infos, problems = im.save_paks(pak_targets, verify)
        decoded = 0
        if verify and not problems:
            for op in self._undo:
                if op.kind != "image":
                    continue
                node, _, _ = self._node(op.index, T.IMAGE)
                if im.where(node) != "pak":
                    continue
                found = im.check_streamed(
                    node, self.zone_name, self.pak_dirs(), paks, target.parent, target.stem
                )
                if found:
                    problems.append(found)
                else:
                    decoded += 1
        report.details = dict(report.details or {})
        report.details["paks"] = infos
        report.details["streamed_images_decoded"] = decoded
        if infos:
            report.details["pak_note"] = PAK_NOTE
        report.problems = list(report.problems) + problems
        if verify:
            report.verified = not report.problems

    # -- for verification --------------------------------------------------------------------

    def expectations(self) -> list[tuple[AssetKey, str, Any]]:
        """What each edited asset must read back as: (key, kind, value)."""
        out = []
        seen = set()
        for op in self._undo:
            kind = op.kind
            if kind in ("row_added", "row_removed", "cell"):
                kind = "table"
            for key, extra_kind in op.extra:
                if (key, extra_kind, "") not in seen and key != op.index:
                    seen.add((key, extra_kind, ""))
                    value = self.table(key) if extra_kind == "table" else self.localize(key)
                    out.append((key, extra_kind, value))
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
                if im.where(node) == "pak":
                    # the pixels are in the paks (checked by decoding); the zone holds
                    # the header, which a resize changes
                    out.append((op.index, "image_header", bytes(node["header"])))
                else:
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


@dataclass
class _Group:
    """Fields sharing one stored string: the STRING event, the field that stores it, and
    the pointer fields (file offset, offset into the string, pointer event) that still
    point at it."""

    event: int
    owner: dict
    owner_key: Any
    members: list[tuple[int, int, int]]
    #: (id(node), key) of the field the question was about.
    asked: tuple[int, Any]


class _ShareIndex:
    """Every string pointer field of the original zone, grouped by the string it names.
    Built once per document on first use (about 0.1 s on a large zone)."""

    def __init__(self, doc: Document):
        self.doc = doc
        rw = doc._rw
        rows = doc.xfile.log.table()
        layout = rw.layout()
        fields_at = rw.trace.string_fields
        #: pointer field offset -> (STRING event, offset into it, pointer event)
        self.target: dict[int, tuple[int, int, int]] = {}
        #: STRING event -> [(field offset, offset into it, pointer event)]
        self.members: dict[int, list[tuple[int, int, int]]] = {}
        mask = (rows[:, 0] == EventKind.POINTER) & (rows[:, 3] == PtrKind.OFFSET)
        for event in np.nonzero(mask)[0].tolist():
            at = int(rows[event, 1])
            if at not in fields_at:
                continue
            block, offset = int(rows[event, 4]), int(rows[event, 5])
            try:
                a = layout.find(block, offset, event)
            except RemapError:
                continue
            if int(rows[a.event, 0]) != EventKind.STRING:
                continue
            entry = (at, offset - a.old_start, event)
            self.target[at] = (a.event, entry[1], event)
            self.members.setdefault(a.event, []).append(entry)
        self._nodes: dict[int, Any] | None = None
        self._cells: dict[int, tuple[dict, int, int, int]] | None = None
        self._inline_ids = {id(node): key for key, (node, _t, _top) in doc._inline.items()}

    def owner_of(self, event: int) -> tuple[dict, Any] | None:
        ident = self.doc._rw.trace.ident.get(event)
        if not ident or ident[0] != "S":
            return None
        if self._nodes is None:
            self._nodes = {id(n): n for n in self.doc._rw.trace.keep}
        node = self._nodes.get(ident[1])
        return None if node is None else (node, ident[2])

    def cell_of(self, element: dict) -> tuple[dict, int, int, int] | None:
        """(table node, table asset index, row, column) of a stringtable cell element."""
        if self._cells is None:
            self._cells = {}
            for a in self.doc.xfile.assets:
                if a.type != T.STRINGTABLE or not isinstance(a.data, dict):
                    continue
                columns, _ = ct.table_shape(a.data)
                for n, e in enumerate(a.data.get("cells") or []):
                    self._cells[id(e)] = (a.data, a.index, *divmod(n, max(columns, 1)))
        return self._cells.get(id(element))

    def locate(self, node: dict, top: int) -> tuple[AssetKey, str]:
        """(asset key, type name) of the asset whose node this is (or which loads it)."""
        if id(node) in self._inline_ids:
            key = self._inline_ids[id(node)]
            return key, type_name(key[1])
        a = self.doc.xfile.assets[top] if 0 <= top < len(self.doc.xfile.assets) else None
        return top, a.type_name if a is not None else "?"

    def describe(
        self, node: dict, key: Any, event: int, owner: bool = False, suffix: bool = False
    ) -> SharedField:
        cell = self.cell_of(node) if node.get("_t") == "StringTableCell" else None
        if cell is not None:
            _, index, row, col = cell
            ref = self.doc.assets[index]
            return SharedField(
                index, ref.type_name, ref.name, f"row {row}, column {col}", owner, suffix
            )
        # ``event`` is the STRING event for the owner, else the pointer's own event: either
        # way the asset that logged it holds the field
        top = self.doc._rw.owner(event)
        index, tname = self.locate(node, top)
        is_root = id(node) in self._inline_ids or (
            isinstance(index, int) and self.doc.xfile.assets[index].data is node
        )
        if is_root and tname == "localize" and key == "value":
            label = "value"
        else:
            label = f"{node.get('_t') or tname}.{key}"
        name = index[2] if isinstance(index, tuple) else self.doc.assets[index].name
        return SharedField(index, tname, name, label, owner, suffix)


def _nested_node(node: dict, path: str) -> tuple[dict, str]:
    """(node, rest of the path) after the "child/" and "child[i]/" steps at the start of a
    field path; a step may name an integer key ("0/" for GfxLightmapArray)."""
    steps = path.split("/")
    for step in steps[:-1]:
        key, element = step, None
        if step.endswith("]") and "[" in step:
            key, _, num = step[:-1].partition("[")
            if not num.isdigit():
                raise EditError(f"field path {path!r}: expected an element number in {step!r}")
            element = int(num)
        if key not in node and key.isdigit() and int(key) in node:
            key = int(key)
        child = node.get(key) if isinstance(node, dict) else None
        if element is not None:
            child = child[element] if isinstance(child, list) and element < len(child) else None
        if not isinstance(child, dict) or "_t" not in child:
            raise EditError(
                f"field path {path!r}: expected a nested node at {step!r} of "
                f"{node.get('_t') if isinstance(node, dict) else node!r}, found none"
            )
        node = child
    return node, steps[-1]


def _is_name(node: Any, key: Any) -> bool:
    """True for the name field of an asset node (renaming an asset is not a text edit)."""
    return key == "name" and isinstance(node, dict) and "header" in node


def _check_share(share: str) -> None:
    if share not in ("split", "all"):
        raise EditError(f"share: expected 'split' or 'all', found {share!r}")


def _split_note(group: _Group | None, copies: int, others: set[int]) -> str:
    note = _copies_note(copies, others)
    if group is None:
        return note
    if not copies:
        return "; share=split: this field now holds its own copy of the shared string"
    return "; share=split" + note.replace(";", ":", 1)


def _copies_note(copies: int, others: set[int]) -> str:
    if not copies:
        return ""
    where = f" (also in asset(s) {', '.join(str(o) for o in sorted(others))})" if others else ""
    return f"; {copies} other field(s) shared the old string and now hold their own copy{where}"


def _change(op: _Op) -> Change:
    return Change(op.index, op.kind, op.before, op.after, op.detail, op.also)


def _also(index: AssetKey, top: int, extra: list, tops: set[int]) -> tuple:
    """The other assets a share="all" edit changed: the keys read back, then any other
    top-level asset holding a changed field."""
    keys = [k for k, _ in extra if k != index]
    keys += sorted(t for t in tops if t != top and t != index)
    return tuple(dict.fromkeys(keys))


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
