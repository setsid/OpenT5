"""The texture-pack workflow: match a folder of PNG/DDS files to a zone's image assets and
replace them in one pass (docs/texture-pack.md).

This builds on the single-image replace of ``opent5.edit`` (docs/edit-api.md): it finds which
stored image each file is meant for (``opent5.texpack.naming``), replaces the matching ones
with ``Document.replace_image`` (same size by default, another power-of-two size with
``resize``), and saves a new zone and, when streamed images changed, its ``.pak`` beside it.
Nothing is ever written into the game folders named in ``.env``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opent5.edit import Document, EditError, game_folders
from opent5.edit import images as im
from opent5.formats import texture as tx
from opent5.texpack.naming import SUFFIXES, Match, NameIndex, PackMap, TexpackError, load_map
from opent5.xfile.constants import AssetType as T


@dataclass
class FileResult:
    """What became of one file in the pack."""

    file: str
    #: "replaced", "resized", "skipped" or "failed" (and "would-replace" / "would-resize"
    #: in a dry run).
    status: str
    image: str | None = None
    #: How the name matched: "stored-name", "cleaned", "map"; empty when nothing matched.
    how: str = ""
    reason: str = ""
    #: The matched image's format and size (when an image matched).
    format: str | None = None
    width: int | None = None
    height: int | None = None
    #: The new pixels' size.
    input_width: int | None = None
    input_height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {"file": self.file, "status": self.status}
        for key in ("image", "how", "reason", "format"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.width is not None:
            out["size"] = [self.width, self.height]
        if self.input_width is not None:
            out["input_size"] = [self.input_width, self.input_height]
        return out


@dataclass
class PackResult:
    """The outcome of applying (or previewing) a texture pack."""

    zone: str
    zone_name: str
    pack_dir: str
    dry_run: bool
    files: list[FileResult] = field(default_factory=list)
    output: str | None = None
    #: The save report as a plain dict (verified, sha1, identical, signature_note, paks).
    report: dict[str, Any] = field(default_factory=dict)

    def _count(self, *status: str) -> int:
        return sum(1 for f in self.files if f.status in status)

    @property
    def replaced(self) -> int:
        return self._count("replaced", "resized", "would-replace", "would-resize")

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def failed(self) -> int:
        return self._count("failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone": self.zone_name,
            "zone_path": self.zone,
            "pack_dir": self.pack_dir,
            "dry_run": self.dry_run,
            "output": self.output,
            "counts": {
                "files": len(self.files),
                "replaced": self.replaced,
                "skipped": self.skipped,
                "failed": self.failed,
            },
            "files": [f.to_dict() for f in self.files],
            "report": self.report,
        }


def _image_refs(doc: Document) -> list:
    """Every image asset, top-level and loaded inline, deduplicated by name."""
    seen: set[str] = set()
    refs = []
    for r in list(doc.assets) + list(doc.inline_assets):
        if r.type == T.IMAGE and r.name and r.name not in seen:
            seen.add(r.name)
            refs.append(r)
    return refs


def _node_of(doc: Document, ref) -> dict:
    """The GfxImage node behind an image ref (its schema view, without decoding pixels)."""
    if ref.inline:
        return doc._inline[ref.index][0]
    return doc.xfile.assets[ref.index].data


def _image_meta(doc: Document, ref) -> dict:
    """Format, size, pixel location and replaceability of an image, with no pak reads."""
    return im.info(_node_of(doc, ref), doc.zone_name)


def _pack_files(pack_dir: Path) -> list[Path]:
    if not pack_dir.is_dir():
        raise TexpackError(f"{pack_dir}: expected a folder of PNG/DDS files, found none")
    return sorted(p for p in pack_dir.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES)


def _read_pixels(path: Path):
    """The file as replace_image input: DDS bytes untouched, else an RGBA array from the PNG."""
    raw = path.read_bytes()
    if raw[:4] == b"DDS ":
        return raw
    return tx.read_png(raw)


def list_images(zone_ff: str | Path, replaceable_only: bool = False) -> list[dict[str, Any]]:
    """Every image a texture pack could target: name, format, size, where the pixels live and
    whether (and why not) it can be replaced. ``name`` is the raw stored name to put in a map
    file; the file-name heuristic is in ``docs/texture-pack.md``."""
    doc = Document.open(str(zone_ff))
    out = []
    for ref in _image_refs(doc):
        meta = _image_meta(doc, ref)
        if replaceable_only and not meta["replaceable"]:
            continue
        out.append(
            {
                "name": ref.name,
                "format": meta["format"],
                "width": meta["width"],
                "height": meta["height"],
                "kind": meta["kind"],
                "pixels": meta["pixels"],
                "replaceable": meta["replaceable"],
                "reason": meta.get("not_replaceable"),
            }
        )
    out.sort(key=lambda d: d["name"])
    return out


def write_template(zone_ff: str | Path, path: str | Path) -> Path:
    """Write a JSON map template (suggested file name -> stored image name) for every
    replaceable image, so a modder knows what to call their PNGs. Never writes into a game
    folder."""
    import json

    from opent5.texpack.naming import suggested_filename

    target = Path(path)
    _check_target(target)
    entries = {suggested_filename(d["name"]): d["name"] for d in list_images(zone_ff, True)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(entries, indent=1) + "\n", encoding="utf-8")
    return target


def _check_target(path: Path) -> None:
    resolved = path.expanduser().resolve()
    for folder in game_folders():
        try:
            resolved.relative_to(folder.resolve())
        except ValueError:
            continue
        raise TexpackError(
            f"{path}: expected a path outside the game folders, found one in {folder}"
        )


def _match_file(stem: str, file_name: str, index: NameIndex, pmap: PackMap | None) -> Match:
    if pmap is not None:
        mapped = pmap.get(stem, file_name)
        if mapped is not None:
            found = index.match(mapped)
            if found.name is not None:
                return Match(found.name, "map")
            return Match(None, "no-match", f"map points at {mapped!r}: {found.reason}")
    return index.match(stem)


def _plan_file(doc, ref, data, meta, resize: bool, allow_shared: bool) -> tuple[str, str]:
    """Classify a matched file without changing anything: returns (status, reason)."""
    if not meta["replaceable"]:
        return "failed", meta.get("not_replaceable", "cannot be replaced")
    size = im.input_size(data)
    same = size is None or size == (meta["width"], meta["height"])
    if not same and not resize:
        hint = " (pass resize to change its size)" if meta["pixels"] == "pak" else ""
        return "failed", (
            f"expected {meta['width']}x{meta['height']} pixels, found {size[0]}x{size[1]}{hint}"
        )
    if meta["pixels"] == "pak":
        level = [p for p in meta.get("parts", []) if not p["shared"]]
        if not level and not allow_shared:
            return "failed", "every part is in a shared pak; pass allow_shared to write it"
    return ("would-resize" if not same else "would-replace"), ""


def apply_pack(
    zone_ff: str | Path,
    pack_dir: str | Path,
    out_dir: str | Path | None = None,
    map_file: str | Path | None = None,
    resize: bool = False,
    allow_shared: bool = False,
    dry_run: bool = False,
) -> PackResult:
    """Replace every image in ``zone_ff`` that a file in ``pack_dir`` matches, and save a new
    zone (and its ``.pak`` when streamed images changed) under ``out_dir``. With ``dry_run``
    nothing is written: each file is classified so the mapping can be checked first.

    ``map_file`` (JSON or CSV) overrides the name heuristic; ``resize`` lets a streamed image
    take another power-of-two size; ``allow_shared`` writes parts that live in a shared pak
    (that changes the image in every zone that uses the pak)."""
    zone_ff = str(zone_ff)
    pack_dir = Path(pack_dir)
    pmap = load_map(map_file) if map_file else None
    files = _pack_files(pack_dir)

    doc = Document.open(zone_ff)
    refs = {r.name: r for r in _image_refs(doc)}
    index = NameIndex(list(refs))
    result = PackResult(
        zone=zone_ff, zone_name=doc.zone_name, pack_dir=str(pack_dir), dry_run=dry_run
    )

    for path in files:
        fr = FileResult(file=str(path), status="skipped")
        match = _match_file(path.stem, path.name, index, pmap)
        if match.name is None:
            fr.status = "skipped"
            fr.reason = match.reason
            if match.how == "ambiguous":
                fr.reason += " -> " + ", ".join(match.candidates)
            result.files.append(fr)
            continue
        ref = refs[match.name]
        fr.image, fr.how = match.name, match.how
        meta = _image_meta(doc, ref)
        fr.format, fr.width, fr.height = meta["format"], meta["width"], meta["height"]
        try:
            data = _read_pixels(path)
        except (tx.TextureError, OSError) as exc:
            fr.status, fr.reason = "failed", f"cannot read the image: {exc}"
            result.files.append(fr)
            continue
        size = im.input_size(data)
        if size:
            fr.input_width, fr.input_height = size
        status, reason = _plan_file(doc, ref, data, meta, resize, allow_shared)
        if status == "failed":
            fr.status, fr.reason = "failed", reason
            result.files.append(fr)
            continue
        if dry_run:
            fr.status = status
            result.files.append(fr)
            continue
        try:
            doc.replace_image(ref.index, data, allow_shared=allow_shared, resize=resize)
            fr.status = "resized" if status == "would-resize" else "replaced"
        except EditError as exc:
            fr.status, fr.reason = "failed", str(exc)
        result.files.append(fr)

    if not dry_run and result.replaced:
        target = Path(out_dir) / f"{doc.zone_name}.ff" if out_dir else None
        if target is None:
            raise TexpackError("out_dir is required to save (or pass dry_run=True to preview)")
        _check_target(target)
        try:
            report = doc.save(target, verify=True)
        except EditError as exc:
            raise TexpackError(str(exc)) from None
        result.output = str(report.path)
        result.report = _report_dict(report)
    return result


def preview_pack(
    zone_ff: str | Path,
    pack_dir: str | Path,
    map_file: str | Path | None = None,
    resize: bool = False,
    allow_shared: bool = False,
) -> PackResult:
    """A dry run of ``apply_pack``: what would be replaced, skipped or fail, without writing."""
    return apply_pack(
        zone_ff, pack_dir, None, map_file, resize=resize, allow_shared=allow_shared, dry_run=True
    )


def _report_dict(report) -> dict[str, Any]:
    out = {
        "verified": report.verified,
        "bytes": report.bytes,
        "sha1": report.sha1,
        "identical": report.identical,
        "assets_changed": report.assets_changed,
        "signature_note": report.signature_note,
        "problems": report.problems,
    }
    paks = (report.details or {}).get("paks")
    if paks:
        out["paks"] = [
            {
                "path": p.get("path"),
                "bytes": p.get("bytes"),
                "sha1": p.get("sha1"),
                "edited_entries": p.get("edited_entries"),
                "appended_entries": p.get("appended_entries"),
                "shared": p.get("shared"),
                "note": p.get("note"),
            }
            for p in paks
        ]
    return out
