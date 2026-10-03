"""Build, apply and describe mod patches (docs/patch-format.md).

The payload is per-asset: for every asset whose editable content differs between the stock
zone and the edited zone, the patch stores the new content alone. Applying replays those
same edits on the user's own stock copy through opent5.edit.Document, which re-lays the zone
out and remaps every pointer exactly as the original edit did, so the result is byte for byte
the edited zone. The re-layout is never stored, so a localize patch is a few hundred bytes
rather than the megabytes a byte-level delta of the shifted content would hold.

Correctness is guaranteed at build time: ``export`` replays its own captured edits on a fresh
stock copy and refuses unless the rebuild equals the edited content exactly. A change the
per-asset form cannot express (an unsupported asset type, or a shared-string "all" edit that
only reproduces byte for byte as a stored-string edit) is therefore caught and named before
any patch is written, so a patch that would not round-trip is never produced.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opent5 import APP_NAME, __version__
from opent5.edit import Document, EditError
from opent5.patch.format import (
    Manifest,
    PatchChange,
    PatchFormatError,
    read_patch,
    write_patch,
)
from opent5.xfile.constants import AssetType as T

#: Asset types whose change the patch can express, and the op that reproduces it.
_TEXT_TYPES = (T.RAWFILE, T.MAP_ENTS, T.COL_MAP_MP, T.COL_MAP_SP)


class PatchError(Exception):
    """A patch that cannot be built or applied: what was expected and what was found."""


# -- result objects (CLI text/JSON and a GUI dialog) ---------------------------------------


@dataclass
class ChangeInfo:
    index: int
    type_name: str
    name: str | None
    op: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "type": self.type_name,
            "name": self.name,
            "op": self.op,
            "size": self.size,
        }


@dataclass
class CreateResult:
    """What ``create`` produced: the patch bytes and everything a front end shows."""

    patch: bytes
    source_zone: str
    source_ff_sha1: str
    source_content_sha1: str
    result_content_sha1: str
    signed: bool
    changes: list[ChangeInfo]
    source_bytes: int
    edited_bytes: int

    @property
    def patch_bytes(self) -> int:
        return len(self.patch)

    @property
    def signature_note(self) -> str | None:
        if not self.signed:
            return None
        return (
            "The edited zone changed its content, so its console signature no longer matches: "
            "the rebuilt zone loads only on a client with the signature check patched out."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_zone": self.source_zone,
            "source_ff_sha1": self.source_ff_sha1,
            "source_content_sha1": self.source_content_sha1,
            "result_content_sha1": self.result_content_sha1,
            "signed": self.signed,
            "assets_changed": len(self.changes),
            "changes": [c.to_dict() for c in self.changes],
            "source_bytes": self.source_bytes,
            "edited_bytes": self.edited_bytes,
            "patch_bytes": self.patch_bytes,
            "signature_note": self.signature_note,
        }


@dataclass
class ApplyResult:
    """What ``apply`` produced, for CLI text/JSON and a GUI dialog."""

    source_zone: str
    #: The source the patch expects, and the user's file as found.
    expected_ff_sha1: str
    expected_content_sha1: str
    found_ff_sha1: str
    found_content_sha1: str
    verified: bool
    changes: list[ChangeInfo]
    output: str | None
    output_bytes: int
    output_sha1: str
    signature_note: str | None
    reproduces_target: bool
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_zone": self.source_zone,
            "expected_ff_sha1": self.expected_ff_sha1,
            "expected_content_sha1": self.expected_content_sha1,
            "found_ff_sha1": self.found_ff_sha1,
            "found_content_sha1": self.found_content_sha1,
            "verified": self.verified,
            "assets_changed": len(self.changes),
            "changes": [c.to_dict() for c in self.changes],
            "output": self.output,
            "output_bytes": self.output_bytes,
            "output_sha1": self.output_sha1,
            "reproduces_target": self.reproduces_target,
            "signature_note": self.signature_note,
            "notes": self.notes,
            "problems": self.problems,
        }


@dataclass
class PatchInfo:
    """A patch described without applying it (the ``info`` command)."""

    manifest: Manifest
    changes: list[ChangeInfo]

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.manifest.to_dict())
        out["changes"] = [c.to_dict() for c in self.changes]
        return out


# -- helpers --------------------------------------------------------------------------------


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _open(src: str | Path | bytes, label: str) -> tuple[Document, bytes]:
    """Open a zone and return the document and the raw .ff bytes (for its sha1)."""
    if isinstance(src, bytes | bytearray | memoryview):
        raw = bytes(src)
        doc = Document.open(raw)
    else:
        path = Path(src)
        if not path.is_file():
            raise PatchError(f"{label}: expected a fastfile, found no such file: {path}")
        raw = path.read_bytes()
        doc = Document.open(raw, name=path.stem)
    if doc.parse_problems:
        raise PatchError(f"{label}: the zone does not parse exactly: {doc.parse_problems[0]}")
    return doc, raw


def _table_csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("latin-1")


def _rows_from_csv(blob: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(blob.decode("latin-1"), newline="")))


def _op_for(type_: int) -> str | None:
    if type_ == T.LOCALIZE:
        return "localize"
    if type_ == T.STRINGTABLE:
        return "table"
    if type_ in _TEXT_TYPES:
        return "text"
    if type_ == T.IMAGE:
        return "image"
    return None


def _capture(edited: Document, index: int, op: str) -> bytes:
    if op == "localize":
        return edited.localize(index)[1].encode("latin-1")
    if op == "table":
        return _table_csv(edited.table(index))
    if op == "text":
        return edited.text(index).encode("latin-1")
    if op == "image":
        from opent5.formats import texture as tx

        img = edited.image(index)
        if img.rgba is None:
            raise PatchError(
                f"asset {index} ({edited.assets[index].name}): its pixels are not in the zone "
                f"({img.reason or 'elsewhere'}); this patch format version does not carry "
                "streamed .pak image edits"
            )
        return tx.write_png(img.rgba)
    raise PatchError(f"asset {index}: no way to capture a {op!r} change")


def _apply_one(doc: Document, change: PatchChange) -> None:
    """Replay one recorded change on ``doc`` (the user's stock copy)."""
    index = change.index
    try:
        ref = doc.asset(index)
    except EditError as exc:
        raise PatchError(
            f"the patch names asset {index}, which the source zone lacks: {exc}"
        ) from None
    if ref.type_name != change.type_name or (change.name is not None and ref.name != change.name):
        raise PatchError(
            f"asset {index}: expected {change.type_name} {change.name!r} as in the patch, "
            f"found {ref.type_name} {ref.name!r}"
        )
    op, blob = change.op, change.blob
    if op == "localize":
        doc.set_localize(index, blob.decode("latin-1"))
    elif op == "text":
        doc.set_text(index, blob.decode("latin-1"))
    elif op == "image":
        from opent5.formats import texture as tx

        doc.replace_image(index, tx.read_png(blob))
    elif op == "table":
        rows = _rows_from_csv(blob)
        while len(doc.table(index)) > len(rows):
            doc.remove_row(index, len(doc.table(index)) - 1)
        for r, row in enumerate(rows):
            have = doc.table(index)
            if r >= len(have):
                doc.add_row(index, row)
                continue
            for c, cell in enumerate(row):
                if have[r][c] != cell:
                    doc.set_cell(index, r, c, cell)
    else:
        raise PatchError(f"asset {index}: unknown patch op {op!r}")


def _owning_asset(doc: Document, offset: int) -> str:
    """The asset whose span holds a content offset, for a clear refusal message."""
    for ref in doc.assets:
        start = ref.file_start
        if start is not None and start <= offset < start + ref.size:
            return f"asset {ref.index} ({ref.type_name} {ref.name!r})"
    return f"content offset {offset:#x}"


def _first_difference(a: bytes, b: bytes) -> int:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i
    return min(len(a), len(b))


# -- export / create ------------------------------------------------------------------------


def create(stock: str | Path | bytes, edited: str | Path | bytes, *, progress=None) -> CreateResult:
    """Build a patch capturing the difference from ``stock`` to ``edited``.

    Both are stock and edited copies of the same zone. The result holds the patch bytes and a
    summary for a front end. Raises PatchError when the two zones cannot be diffed into a
    patch that reproduces ``edited`` exactly.
    """
    stock_doc, stock_raw = _open(stock, "stock")
    edited_doc, _edited_raw = _open(edited, "edited")

    if stock_doc.zone_name != edited_doc.zone_name:
        raise PatchError(
            f"expected the same zone on both sides, found stock {stock_doc.zone_name!r} and "
            f"edited {edited_doc.zone_name!r}"
        )
    if len(stock_doc.assets) != len(edited_doc.assets):
        raise PatchError(
            f"expected the same asset list on both sides, found {len(stock_doc.assets)} assets "
            f"in stock and {len(edited_doc.assets)} in edited (adding or removing a top-level "
            "asset is not supported by this patch format version)"
        )

    edited_content = edited_doc.content
    changes: list[PatchChange] = []
    for i, (a, b) in enumerate(zip(stock_doc.assets, edited_doc.assets, strict=True)):
        if (a.type, a.name) != (b.type, b.name):
            raise PatchError(
                f"asset {i}: expected {a.type_name} {a.name!r}, found {b.type_name} {b.name!r} "
                "(the asset list was reordered or retyped)"
            )
        op = _op_for(a.type)
        if op is None:
            continue
        if not _differs(stock_doc, edited_doc, i, op):
            continue
        changes.append(PatchChange(i, a.type_name, a.name, op, _capture(edited_doc, i, op)))

    _prove(stock, edited_content, changes)

    manifest = Manifest(
        source_zone=stock_doc.zone_name,
        source_ff_sha1=_sha1(stock_raw),
        source_content_sha1=_sha1(stock_doc.content),
        result_content_sha1=_sha1(edited_content),
        signed=stock_doc.signed,
        assets_changed=len(changes),
        created=datetime.date.today().isoformat(),
        tool_version=__version__,
        generator=f"{APP_NAME} {__version__}",
    )
    return CreateResult(
        patch=write_patch(manifest, changes),
        source_zone=stock_doc.zone_name,
        source_ff_sha1=manifest.source_ff_sha1,
        source_content_sha1=manifest.source_content_sha1,
        result_content_sha1=manifest.result_content_sha1,
        signed=stock_doc.signed,
        changes=[ChangeInfo(c.index, c.type_name, c.name, c.op, c.size) for c in changes],
        source_bytes=len(stock_doc.content),
        edited_bytes=len(edited_content),
    )


def export(stock: str | Path | bytes, edited: str | Path | bytes, *, progress=None) -> bytes:
    """The .o5patch bytes for the difference from ``stock`` to ``edited``."""
    return create(stock, edited, progress=progress).patch


def _differs(stock: Document, edited: Document, index: int, op: str) -> bool:
    if op == "localize":
        return stock.localize(index) != edited.localize(index)
    if op == "table":
        return stock.table(index) != edited.table(index)
    if op == "text":
        return stock.text(index) != edited.text(index)
    if op == "image":
        import numpy as np

        a, b = stock.image(index).rgba, edited.image(index).rgba
        if a is None or b is None:
            # Pixels live outside the zone; fall back to the stored header bytes.
            na, _, _ = stock._node(index, T.IMAGE)  # noqa: SLF001 - sibling-package read
            nb, _, _ = edited._node(index, T.IMAGE)  # noqa: SLF001
            return bytes(na["header"]) != bytes(nb["header"])
        return a.shape != b.shape or not np.array_equal(a, b)
    return False


def _prove(stock: str | Path | bytes, edited_content: bytes, changes: list[PatchChange]) -> None:
    """Replay the captured changes on a fresh stock copy; the rebuild must equal the edited
    content exactly, or the patch is refused (naming the asset that still differs)."""
    doc, _ = _open(stock, "stock")
    for change in changes:
        _apply_one(doc, change)
    rebuilt = doc.build()
    if rebuilt == edited_content:
        return
    at = _first_difference(rebuilt, edited_content)
    where = _owning_asset(doc, at) if at < len(rebuilt) else "the end of the zone"
    raise PatchError(
        "the edit cannot be captured as a per-asset patch: replaying the captured changes did "
        f"not reproduce the edited zone ({where}, content offset {at:#x}; {len(rebuilt)} bytes "
        f"rebuilt against {len(edited_content)} expected). This happens for an asset type the "
        "patch format does not yet carry, or a shared-string change made with 'all'."
    )


# -- apply ----------------------------------------------------------------------------------


def apply(
    patch: bytes,
    user_stock: str | Path | bytes,
    out: str | Path | None = None,
    *,
    verify: bool = True,
    progress=None,
) -> ApplyResult:
    """Apply a patch to the user's own stock zone and write ``out`` (when given).

    The user's source is verified against the patch's manifest first: its decompressed-content
    sha1 must match (that is what makes the result reproducible), and the .ff sha1 is reported
    too. A mismatch is refused with expected vs found, so a patch never applies to the wrong
    file or to one already modified.
    """
    manifest, changes = _read(patch)
    doc, raw = _open(user_stock, "the stock zone")
    found_ff = _sha1(raw)
    found_content = _sha1(doc.content)

    notes: list[str] = []
    if found_content != manifest.source_content_sha1:
        raise PatchError(
            "the stock zone is not the source this patch was built from. Expected content sha1 "
            f"{manifest.source_content_sha1} (.ff sha1 {manifest.source_ff_sha1}), found content "
            f"sha1 {found_content} (.ff sha1 {found_ff}). Point apply at an unmodified "
            f"{manifest.source_zone} and try again."
        )
    if doc.zone_name != manifest.source_zone:
        raise PatchError(
            f"expected the zone {manifest.source_zone!r} the patch was built for, found "
            f"{doc.zone_name!r}"
        )
    if found_ff != manifest.source_ff_sha1:
        notes.append(
            "the stock .ff sha1 differs from the patch's, but its decompressed content matches, "
            "so the result is still reproduced exactly (the .ff was repacked)."
        )

    for change in changes:
        _apply_one(doc, change)

    content = doc.build()
    reproduces = _sha1(content) == manifest.result_content_sha1
    problems: list[str] = []
    if not reproduces:
        problems.append(
            "the rebuilt content does not match the patch's recorded result sha1; the patch may "
            "be corrupt"
        )

    output = None
    out_bytes = 0
    out_sha1 = ""
    signature_note = None
    verified = reproduces
    change_infos = [ChangeInfo(c.index, c.type_name, c.name, c.op, c.size) for c in changes]
    if out is not None:
        try:
            report = doc.save(out, verify=verify, progress=progress)
        except EditError as exc:
            raise PatchError(str(exc)) from None
        output = str(report.path)
        out_bytes = report.bytes
        out_sha1 = report.sha1
        signature_note = report.signature_note
        problems += list(report.problems)
        verified = reproduces and (report.verified if verify else True)
    elif doc.signed:
        signature_note = (
            "The rebuilt zone changed its content, so its console signature no longer matches: "
            "it loads only on a client with the signature check patched out."
        )

    return ApplyResult(
        source_zone=manifest.source_zone,
        expected_ff_sha1=manifest.source_ff_sha1,
        expected_content_sha1=manifest.source_content_sha1,
        found_ff_sha1=found_ff,
        found_content_sha1=found_content,
        verified=verified,
        changes=change_infos,
        output=output,
        output_bytes=out_bytes,
        output_sha1=out_sha1,
        signature_note=signature_note,
        reproduces_target=reproduces,
        notes=notes,
        problems=problems,
    )


# -- info -----------------------------------------------------------------------------------


def info(patch: bytes) -> PatchInfo:
    """Describe a patch without applying it."""
    manifest, changes = _read(patch)
    return PatchInfo(
        manifest=manifest,
        changes=[ChangeInfo(c.index, c.type_name, c.name, c.op, c.size) for c in changes],
    )


def _read(patch: bytes) -> tuple[Manifest, list[PatchChange]]:
    try:
        return read_patch(patch)
    except PatchFormatError as exc:
        raise PatchError(f"not a patch this tool reads: {exc}") from None
