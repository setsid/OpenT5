"""The GUI's one door to a zone: ``ZoneDoc``.

The editing contract is ``docs/edit-api.md`` (``opent5.edit.Document``). When
that package is importable, ``ZoneDoc`` delegates every read and every edit to
it. Until then, ``_Adapter`` implements the read side of the same contract
directly from ``opent5.xfile`` and ``opent5.export``, and keeps edits as an
in-memory overlay (with undo and redo) so the editors, markers and the Changes
panel work; saving needs ``opent5.edit``.

On top of the contract, ``ZoneDoc`` adds what only the GUI needs: inline
assets (images, materials, models and entity strings that a zone loads inside
another asset, so they are not in its asset list) as read-only refs, and a
search that reports line numbers and cells.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from opent5 import env
from opent5.xfile.constants import AssetType as T
from opent5.xfile.constants import type_name

Progress = Callable[[str, float], None]

#: Kinds of view an asset offers, in the order the view bar shows them.
VIEW_KINDS = ("text", "table", "localize", "image", "entities", "geometry", "fields", "hex")

TEXT_TYPES = {T.RAWFILE, T.MAP_ENTS}
GEOMETRY_TYPES = {T.GFX_MAP, T.COL_MAP_MP, T.COL_MAP_SP, T.XMODEL}


try:  # one exception type for both backends
    from opent5.edit import EditError
except ImportError:  # pragma: no cover - before opent5.edit existed

    class EditError(Exception):
        """Mirrors opent5.edit.EditError."""


@dataclass(frozen=True)
class Ref:
    """An asset as the GUI addresses it. ``key`` is the asset-list index for a
    top-level asset, or ("inline", type, name) for one loaded inside another."""

    key: Any
    type: int
    type_name: str
    name: str
    size: int
    editable: tuple[str, ...]
    offset: int | None = None

    @property
    def inline(self) -> bool:
        return isinstance(self.key, tuple)

    @property
    def index(self) -> int | None:
        return None if self.inline else self.key

    @property
    def label(self) -> str:
        return self.name or f"<{self.type_name} {self.key}>"


@dataclass
class ImageData:
    info: dict[str, Any]
    rgba: np.ndarray | None
    reason: str | None = None


@dataclass
class MeshData:
    positions: np.ndarray  # (n, 3) float32
    triangles: np.ndarray  # (m, 3) int
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    #: Optional (m,) int group id per triangle (surface, brush), for tinting.
    groups: np.ndarray | None = None
    label: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class Change:
    key: Any
    kind: str  # "text", "cell", "localize", "image", "field"
    before: Any
    after: Any
    name: str = ""
    type_name: str = ""
    detail: str = ""


@dataclass
class SearchHit:
    ref: Ref
    where: str  # "name", "text", "cell", "value"
    line: int | None = None
    column: int | None = None
    row: int | None = None
    snippet: str = ""


@dataclass
class SaveReport:
    path: Path
    bytes: int
    assets_checked: int
    assets_changed: int
    verified: bool
    problems: list[str]
    signature_note: str | None
    sha1: str = ""
    identical: bool = False


SIGNATURE_NOTE = (
    "The console signature no longer matches this file. It will load only on a client "
    "with the signature check patched out."
)


def edit_available() -> bool:
    try:
        import opent5.edit  # noqa: F401
    except ImportError:
        return False
    return hasattr(opent5.edit, "Document")


def pak_dirs(path: Path) -> list[Path]:
    dirs = [path.parent]
    for folder in env.zone_dirs():
        if folder not in dirs:
            dirs.append(folder)
    return dirs


def editable_for(asset_type: int, has_text: bool = False) -> tuple[str, ...]:
    kinds: list[str] = []
    if asset_type in TEXT_TYPES or has_text:
        kinds.append("text")
    if asset_type == T.STRINGTABLE:
        kinds.append("table")
    if asset_type == T.LOCALIZE:
        kinds.append("localize")
    if asset_type == T.IMAGE:
        kinds.append("image")
    if asset_type in GEOMETRY_TYPES:
        kinds.append("geometry")
    kinds += ["fields", "hex"]
    return tuple(kinds)


# -- the adapter: the read side of the contract, from xfile/export --------------------------


class _Adapter:
    """Implements docs/edit-api.md reads over a parsed zone; edits are an overlay."""

    name = "adapter"

    def __init__(self, path: Path, progress: Progress | None = None):
        from opent5.container.fastfile import carries_console_signature, zone_name_of
        from opent5.container.zone import Zone
        from opent5.xfile import parse

        report = progress or (lambda _m, _f: None)
        self.path = Path(path)
        report("Reading and inflating", 0.05)
        self.zone = Zone.open(self.path)
        self.content = bytes(self.zone.content)
        report("Parsing assets", 0.55)
        self.xfile = parse(self.content, log=False)
        header = bytes(self.zone.header)
        self.zone_name = zone_name_of(header).decode("ascii", "replace") or self.path.stem
        report("Checking the signature", 0.85)
        self.signed = carries_console_signature(header)
        self.problems = self.xfile.problems()
        self._undo: list[tuple] = []
        self._redo: list[tuple] = []
        self._text: dict[int, str] = {}
        self._cells: dict[tuple[int, int, int], str] = {}
        self._localize: dict[int, str] = {}
        self._images: dict[int, np.ndarray] = {}
        self._original_text: dict[int, str] = {}
        self._paks = None

    # contract: listing
    @property
    def assets(self):
        return self.xfile.assets

    def type_counts(self) -> dict[str, int]:
        return self.xfile.counts()

    def data(self, index: int):
        return self.xfile.assets[index].data

    # contract: reading
    def text(self, index: int) -> str:
        if index in self._text:
            return self._text[index]
        return self.original_text(index)

    def original_text(self, index: int) -> str:
        if index not in self._original_text:
            self._original_text[index] = self._read_text(index)
        return self._original_text[index]

    def _read_text(self, index: int) -> str:
        from opent5.export.entities import entity_text

        a = self.xfile.assets[index]
        d = a.data
        if a.type == T.RAWFILE:
            body = d.contents()
            return "" if body is None else body.decode("latin-1")
        if a.type == T.MAP_ENTS:
            return entity_text(d.get("entity_string"))
        if a.type in (T.COL_MAP_MP, T.COL_MAP_SP) and isinstance(d.get("map_ents"), dict):
            return entity_text(d["map_ents"].get("entity_string"))
        raise EditError(f"asset {index} ({a.type_name}): no text view")

    def table(self, index: int) -> list[list[str]]:
        d = self.xfile.assets[index].data
        rows = [[d.cell(r, c) or "" for c in range(d.column_count)] for r in range(d.row_count)]
        for (i, r, c), value in self._cells.items():
            if i == index:
                rows[r][c] = value
        return rows

    def localize(self, index: int) -> tuple[str, str]:
        d = self.xfile.assets[index].data
        return d.name or "", self._localize.get(index, d.value or "")

    def image(self, index: int) -> ImageData:
        data = self.image_node(self.xfile.assets[index].data)
        if index in self._images:
            data.rgba = self._images[index]
            data.info["source"] = f"{data.info.get('source', '')}, replaced (unsaved)"
        return data

    def image_node(self, node: dict) -> ImageData:
        from opent5.export.images import ImageError, PakSet, decode_image, stream_parts
        from opent5.export.zone import SEMANTICS
        from opent5.formats import texture as tx
        from opent5.xfile.schema import view

        f = view(node).fields
        parts = stream_parts(f)
        info = {
            "format": tx.format_name(f["texture.format"]),
            "width": f["texture.width"],
            "height": f["texture.height"],
            "depth": f["texture.depth"],
            "mips": f["texture.mipmap"],
            "cube": bool(f["texture.cubemap"]),
            "semantic": SEMANTICS.get(f["semantic"], str(f["semantic"])),
            "streamed": bool(parts),
        }
        if self._paks is None:
            self._paks = PakSet(self.zone_name, pak_dirs(self.path))
        try:
            decoded = decode_image(node, self._paks)
        except ImageError as exc:
            info["source"] = "streamed" if parts else "none"
            return ImageData(info, None, str(exc))
        info["source"] = decoded.source
        info["decoded"] = f"{decoded.width} x {decoded.height}"
        if decoded.notes:
            info["notes"] = "; ".join(decoded.notes)
        rgba = decoded.layers[0][1] if decoded.layers else None
        if len(decoded.layers) > 1:
            info["layers"] = ", ".join(s.strip("_") for s, _ in decoded.layers)
        return ImageData(info, rgba, None)

    def raw(self, index: int) -> bytes:
        a = self.xfile.assets[index]
        return self.content[a.file_start : a.file_end]

    def fields(self, index: int) -> dict:
        return fields_of(self.xfile.assets[index].data)

    def mesh(self, kind: str, index: int | None = None) -> MeshData:
        from opent5.gui import geometry

        return geometry.mesh(self, kind, index)

    # contract: editing (overlay)
    def _apply(self, op: tuple, value: Any) -> None:
        kind, key = op[0], op[1]
        if kind == "text":
            if value is None:
                self._text.pop(key, None)
            else:
                self._text[key] = value
        elif kind == "cell":
            if value is None:
                self._cells.pop(key, None)
            else:
                self._cells[key] = value
        elif kind == "localize":
            if value is None:
                self._localize.pop(key, None)
            else:
                self._localize[key] = value
        elif kind == "image":
            if value is None:
                self._images.pop(key, None)
            else:
                self._images[key] = value

    def _store(self, kind: str):
        return {
            "text": self._text,
            "cell": self._cells,
            "localize": self._localize,
            "image": self._images,
        }[kind]

    def _edit(self, kind: str, key: Any, value: Any) -> None:
        store = self._store(kind)
        before = store.get(key)
        self._apply((kind, key), value)
        self._undo.append((kind, key, before, value))
        self._redo.clear()

    def set_text(self, index: int, text: str) -> None:
        self.original_text(index)  # raises when there is no text
        if text == self.text(index):
            return
        self._edit("text", index, None if text == self.original_text(index) else text)

    def set_cell(self, index: int, row: int, col: int, text: str) -> None:
        d = self.xfile.assets[index].data
        if not (0 <= row < d.row_count and 0 <= col < d.column_count):
            raise EditError(
                f"stringtable {d.name}: cell ({row}, {col}) is outside "
                f"{d.row_count} x {d.column_count}"
            )
        original = d.cell(row, col) or ""
        self._edit("cell", (index, row, col), None if text == original else text)

    def set_localize(self, index: int, value: str) -> None:
        original = self.xfile.assets[index].data.value or ""
        self._edit("localize", index, None if value == original else value)

    def replace_image(self, index: int, rgba) -> None:
        current = self.image_node(self.xfile.assets[index].data)
        if current.rgba is None:
            raise EditError(f"asset {index}: the pixels are not in this zone ({current.reason})")
        if not isinstance(rgba, np.ndarray) or rgba.shape != current.rgba.shape:
            found = getattr(rgba, "shape", type(rgba).__name__)
            raise EditError(
                f"asset {index}: expected an image of {current.rgba.shape}, found {found}"
            )
        self._edit("image", index, np.array(rgba, np.uint8))

    def set_field(self, index: int, path: str, value) -> None:
        raise EditError("field editing needs opent5.edit")

    @property
    def dirty(self) -> bool:
        return bool(self._text or self._cells or self._localize or self._images)

    def changes(self) -> list[Change]:
        out = []
        for i, text in sorted(self._text.items()):
            out.append(Change(i, "text", self.original_text(i), text))
        for (i, r, c), value in sorted(self._cells.items()):
            d = self.xfile.assets[i].data
            out.append(Change(i, "cell", d.cell(r, c) or "", value, detail=f"row {r}, col {c}"))
        for i, value in sorted(self._localize.items()):
            out.append(Change(i, "localize", self.xfile.assets[i].data.value or "", value))
        for i in sorted(self._images):
            out.append(Change(i, "image", None, None, detail="pixels replaced"))
        return out

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return None
        kind, key, before, after = self._undo.pop()
        self._apply((kind, key), before)
        self._redo.append((kind, key, before, after))
        return key

    def redo(self):
        if not self._redo:
            return None
        kind, key, before, after = self._redo.pop()
        self._apply((kind, key), after)
        self._undo.append((kind, key, before, after))
        return key

    def save(self, new_path, verify: bool = True) -> SaveReport:
        raise EditError(
            "Saving needs the opent5.edit package, which is not installed in this build; "
            "the edits are kept in this window until it is."
        )

    def close(self) -> None:
        if self._paks is not None:
            self._paks.close()


def fields_of(node: Any, max_items: int = 256) -> Any:
    from opent5.xfile.schema import to_dict

    if node is None:
        return {}
    return to_dict(node, max_items=max_items)


# -- the GUI document ------------------------------------------------------------------------

#: Semantic codes of a GfxImage (opent5.export.zone.SEMANTICS) for the info panel.
_SEMANTICS = {0: "2d", 1: "function", 2: "colour", 5: "normal", 8: "specular", 11: "water"}


class ZoneDoc:
    """One open zone. Wraps opent5.edit.Document when available, else the adapter.

    Every read and edit is addressed by a ``Ref``; its ``key`` is what both
    backends take (an asset-list index, or ("inline", type, name))."""

    def __init__(self, impl, backend: str):
        self.impl = impl
        self.backend = backend
        self.path = Path(impl.path)
        self.zone_name = impl.zone_name
        self.signed = bool(impl.signed)
        self.xfile = getattr(impl, "xfile", None)
        self.problems = list(
            getattr(impl, "parse_problems", None) or getattr(impl, "problems", None) or []
        )
        self.refs: list[Ref] = [self._ref(a, i) for i, a in enumerate(impl.assets)]
        self.inline_refs: list[Ref] = []
        self._inline_nodes: dict[tuple, Any] = {}
        self._by_key: dict[Any, Ref] = {}
        self.saved_path: Path | None = None
        self.load_seconds = 0.0

    # opening
    @classmethod
    def open(cls, path: str | Path, progress: Progress | None = None, prefer_edit=True) -> ZoneDoc:
        started = time.perf_counter()
        path = Path(path)
        report = progress or (lambda _m, _f: None)
        if prefer_edit and edit_available():
            from opent5.edit import Document

            report("Reading, inflating and parsing", 0.1)
            doc = cls(Document.open(path), "edit")
        else:
            doc = cls(_Adapter(path, report), "adapter")
        report("Indexing inline assets", 0.92)
        doc._collect_inline()
        doc.load_seconds = time.perf_counter() - started
        report("Ready", 1.0)
        return doc

    def _ref(self, a, i) -> Ref:
        t = int(a.type)
        key = getattr(a, "index", i)
        if not isinstance(key, int | tuple):
            key = i
        editable = getattr(a, "editable", None)
        if editable:
            kinds = [k for k in VIEW_KINDS if k in editable]
            if "entities" in kinds and "text" not in kinds:
                kinds.insert(0, "text")
            kinds = [k for k in kinds if k != "entities"]
            for k in ("fields", "hex"):
                if k not in kinds and (k == "fields" or not isinstance(key, tuple)):
                    kinds.append(k)
            editable = tuple(kinds)
        else:
            has_text = False
            if self.xfile is not None and t in (T.COL_MAP_MP, T.COL_MAP_SP):
                data = self.xfile.assets[i].data
                has_text = isinstance(data, dict) and isinstance(data.get("map_ents"), dict)
            editable = editable_for(t, has_text)
        start = getattr(a, "file_start", None)
        size = int(getattr(a, "size", 0) or 0)
        return Ref(key, t, type_name(t), a.name or "", size, editable, start)

    def _collect_inline(self) -> None:
        inline = getattr(self.impl, "inline_assets", None)
        if inline is not None:
            self.inline_refs = [self._ref(a, -1) for a in inline]
        elif self.xfile is not None:
            from opent5.export.nodes import AssetIndex

            index = AssetIndex(self.xfile)
            top = {(r.type, r.name) for r in self.refs}
            for t in (T.IMAGE, T.MATERIAL, T.XMODEL, T.TECHSET, T.MAP_ENTS, T.LOCALIZE):
                for name, node in sorted(index.of(t).items()):
                    if (t, name) in top or not name:
                        continue
                    key = ("inline", t, name)
                    self._inline_nodes[key] = node
                    kinds = ["fields"]
                    if t == T.IMAGE:
                        kinds.insert(0, "image")
                    elif t == T.XMODEL:
                        kinds.insert(0, "geometry")
                    elif t == T.MAP_ENTS:
                        kinds.insert(0, "text")
                    self.inline_refs.append(Ref(key, t, type_name(t), name, 0, tuple(kinds)))
        self._by_key = {r.key: r for r in self.all_refs}

    # listing
    @property
    def all_refs(self) -> list[Ref]:
        return self.refs + self.inline_refs

    def ref(self, key) -> Ref | None:
        return self._by_key.get(key)

    def type_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.all_refs:
            out[r.type_name] = out.get(r.type_name, 0) + 1
        return out

    def node(self, ref: Ref):
        if ref.inline:
            if ref.key in self._inline_nodes:
                return self._inline_nodes[ref.key]
            found = getattr(self.impl, "_inline", {}).get(ref.key)
            return found[0] if found else None
        if self.xfile is not None:
            return self.xfile.assets[ref.key].data
        return None

    @property
    def native(self) -> bool:
        """True when the edit API handles inline assets itself."""
        return self.backend == "edit"

    # reading
    def text(self, ref: Ref) -> str:
        if ref.inline and not self.native:
            from opent5.export.entities import entity_text

            return entity_text(self.node(ref).get("entity_string"))
        return self.impl.text(ref.key)

    def original_text(self, ref: Ref) -> str:
        if hasattr(self.impl, "original_text") and (self.native or not ref.inline):
            return self.impl.original_text(ref.key)
        for c in self.impl.changes():
            if getattr(c, "index", None) == ref.key and c.kind == "text":
                return c.before
        return self.text(ref)

    def table(self, ref: Ref) -> list[list[str]]:
        return self.impl.table(ref.key)

    def localize(self, ref: Ref) -> tuple[str, str]:
        if ref.inline and not self.native:
            node = self.node(ref)
            return node.get("name") or "", node.get("value") or ""
        return self.impl.localize(ref.key)

    def image(self, ref: Ref) -> ImageData:
        if ref.inline and not self.native:
            return self.impl.image_node(self.node(ref))
        data = self.impl.image(ref.key)
        if isinstance(data, ImageData):
            return data
        return ImageData(normalise_image_info(data.info), data.rgba, data.reason)

    def mesh(self, kind: str, ref: Ref | None = None) -> MeshData:
        """Geometry comes from opent5.gui.geometry for both backends (read-only, and
        faster than the exporter for clipMap brushes)."""
        from opent5.gui import geometry

        if ref is not None and ref.inline:
            return geometry.model_mesh(self, self.node(ref), ref.name)
        return geometry.mesh(self, kind, None if ref is None else ref.key)

    def raw(self, ref: Ref) -> bytes | None:
        if ref.inline and not self.native:
            return None
        try:
            return self.impl.raw(ref.key)
        except EditError:
            return None

    def fields(self, ref: Ref) -> Any:
        if ref.inline and not self.native:
            return fields_of(self.node(ref))
        return self.impl.fields(ref.key)

    # editing
    def set_text(self, ref: Ref, text: str) -> None:
        self.impl.set_text(ref.key, text)

    def set_cell(self, ref: Ref, row: int, col: int, text: str) -> None:
        self.impl.set_cell(ref.key, row, col, text)

    @property
    def can_edit_rows(self) -> bool:
        return hasattr(self.impl, "add_row") and hasattr(self.impl, "remove_row")

    def add_row(self, ref: Ref, at: int | None = None) -> int:
        return self.impl.add_row(ref.key, None, at)

    def remove_row(self, ref: Ref, row: int) -> list[str]:
        return self.impl.remove_row(ref.key, row)

    def set_localize(self, ref: Ref, value: str) -> None:
        self.impl.set_localize(ref.key, value)

    def replace_image(self, ref: Ref, data) -> None:
        if ref.inline and not self.native:
            raise EditError(
                f"{ref.name}: an image loaded inside another asset; replacing it needs "
                "opent5.edit"
            )
        self.impl.replace_image(ref.key, data)

    def can_replace_image(self, ref: Ref, info: dict | None = None) -> tuple[bool, str]:
        if ref.inline and not self.native:
            return False, "Replacing an image loaded inside another asset needs opent5.edit."
        if info and info.get("replaceable") is False:
            return False, str(info.get("not_replaceable") or "This image cannot be replaced.")
        return True, ""

    @property
    def dirty(self) -> bool:
        return bool(self.impl.dirty)

    def changes(self) -> list[Change]:
        out = []
        for c in self.impl.changes():
            key = getattr(c, "index", None)
            if key is None:
                key = getattr(c, "key", None)
            ref = self._by_key.get(key)
            if ref is None and isinstance(key, int) and 0 <= key < len(self.refs):
                ref = self.refs[key]
            detail = getattr(c, "detail", "") or ""
            out.append(
                Change(
                    key,
                    c.kind,
                    c.before,
                    c.after,
                    name=ref.label if ref else str(key),
                    type_name=ref.type_name if ref else "",
                    detail=detail,
                )
            )
        return out

    def edited_keys(self) -> set:
        return {c.key for c in self.changes()}

    @property
    def can_undo(self) -> bool:
        return bool(self.impl.can_undo)

    @property
    def can_redo(self) -> bool:
        return bool(self.impl.can_redo)

    def undo(self):
        """Undo the last edit; returns the key of the asset it touched (or None)."""
        return _key_of(self.impl.undo())

    def redo(self):
        return _key_of(self.impl.redo())

    @property
    def can_save(self) -> bool:
        return self.backend == "edit"

    def save(self, new_path: str | Path, verify: bool = True) -> SaveReport:
        new_path = Path(new_path)
        if new_path.resolve() == self.path.resolve():
            raise EditError(f"refusing to overwrite the source zone {self.path}")
        for folder in env.zone_dirs():
            try:
                new_path.resolve().relative_to(folder.resolve())
            except ValueError:
                continue
            raise EditError(f"refusing to write into the game folder {folder}")
        r = self.impl.save(new_path, verify=verify)
        self.saved_path = new_path
        identical = bool(getattr(r, "identical", False))
        note = getattr(r, "signature_note", None)
        if self.signed and not identical:
            note = SIGNATURE_NOTE
        report = SaveReport(
            Path(r.path),
            int(r.bytes),
            int(r.assets_checked),
            int(r.assets_changed),
            bool(r.verified),
            list(r.problems or []),
            note,
        )
        report.sha1 = getattr(r, "sha1", "")
        report.identical = identical
        return report

    # search
    def search(
        self,
        text: str,
        names: bool = True,
        contents: bool = True,
        case: bool = False,
        limit: int = 2000,
    ) -> list[SearchHit]:
        if not text:
            return []
        needle = text if case else text.lower()

        def has(s: str) -> bool:
            return needle in (s if case else s.lower())

        hits: list[SearchHit] = []
        if names:
            for r in self.all_refs:
                if r.name and has(r.name):
                    hits.append(SearchHit(r, "name", snippet=r.name))
                    if len(hits) >= limit:
                        return hits
        if not contents:
            return hits
        for r in self.all_refs:
            try:
                if "text" in r.editable:
                    body = self.text(r)
                    if not has(body):
                        continue
                    for n, line in enumerate(body.splitlines(), 1):
                        if has(line):
                            col = (line if case else line.lower()).find(needle)
                            hits.append(
                                SearchHit(r, "text", line=n, column=col, snippet=line.strip()[:200])
                            )
                            if len(hits) >= limit:
                                return hits
                elif "table" in r.editable:
                    for ri, row in enumerate(self.table(r)):
                        for ci, cell in enumerate(row):
                            if cell and has(cell):
                                hits.append(
                                    SearchHit(r, "cell", row=ri, column=ci, snippet=cell[:200])
                                )
                elif "localize" in r.editable:
                    _key, value = self.localize(r)
                    if value and has(value):
                        hits.append(SearchHit(r, "value", snippet=value[:200]))
            except (EditError, ValueError):
                continue
            if len(hits) >= limit:
                break
        return hits

    def close(self) -> None:
        close = getattr(self.impl, "close", None)
        if close:
            close()


def _key_of(result) -> Any:
    """The asset key behind what undo/redo returned (a Change, a key or None)."""
    if result is None:
        return None
    key = getattr(result, "index", result)
    if isinstance(key, tuple) and len(key) == 3 and isinstance(key[0], int):
        return key[0]  # the adapter's stringtable cell: (asset, row, col)
    return key


def normalise_image_info(info: dict) -> dict:
    """opent5.edit image info -> the keys the image view shows."""
    out = dict(info)
    if isinstance(out.get("semantic"), int):
        out["semantic"] = _SEMANTICS.get(out["semantic"], str(out["semantic"]))
    if "kind" in out:
        out["cube"] = out["kind"] == "cube"
    pixels = out.get("pixels")
    if pixels and not out.get("source"):
        out["source"] = {"elsewhere": "not in this zone"}.get(pixels, pixels)
    if isinstance(out.get("notes"), list):
        out["notes"] = "; ".join(out["notes"])
    return out
