"""Mapping a texture-pack file name to a zone's stored image name.

Stored image names carry two decorations the linker adds (docs/research/textures.md 2.1,
5): a leading marker of one or two tildes and a ``-g`` tag (``~-g``, ``~~-g``) on names the
material system generates, and a trailing semantic suffix ``_c`` colour, ``_n`` normal or
``_s`` specular. A modder names a PNG after the thing it is, without having to type the
marker. So matching works on a normalised key:

    ~-gmp_nuked_sign_c   ->  core "mp_nuked_sign", suffix "_c"
    mp_nuked_sign.png    ->  core "mp_nuked_sign", suffix ""      -> matches
    mp_nuked_sign_c.png  ->  core "mp_nuked_sign", suffix "_c"    -> matches

The raw stored name is always accepted too (``~-gmp_nuked_sign_c.png``), and an explicit map
file (JSON or CSV) overrides the heuristic for exact control. When a bare core matches more
than one stored image (a ``_c`` and a ``_n`` of the same thing) the file is reported
ambiguous and left alone, so the modder adds the suffix to say which they mean.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: A leading run of tildes and an optional ``-g`` / ``g`` tag (the generated-name marker).
_MARKER = re.compile(r"^~+(?:-?g)?")
#: A trailing colour / normal / specular suffix.
_SUFFIX = re.compile(r"(_[cns])$")

#: File extensions a texture pack may hold.
SUFFIXES = (".png", ".dds")


class TexpackError(Exception):
    """A texture-pack input that cannot be used (a bad map file, an unreadable pack)."""


def normalise(name: str) -> tuple[str, str]:
    """``(core, suffix)`` of a stored image name or a file stem: lower-cased, the leading
    marker removed, the trailing ``_c`` / ``_n`` / ``_s`` split off (``""`` when absent)."""
    text = _MARKER.sub("", name.lower())
    match = _SUFFIX.search(text)
    if match:
        return text[: match.start()], match.group(1)
    return text, ""


@dataclass(frozen=True)
class Match:
    """The outcome of matching one file name against the zone's images."""

    #: The stored image name, or ``None`` when nothing (or more than one thing) matched.
    name: str | None
    #: "stored-name", "cleaned", "map" (from a map file), "no-match" or "ambiguous".
    how: str
    #: Empty on a match; otherwise why the file was left alone.
    reason: str = ""
    #: The candidate stored names when the match was ambiguous.
    candidates: tuple[str, ...] = ()


class NameIndex:
    """The stored image names of one zone, indexed for matching (``build`` from a list)."""

    def __init__(self, names: list[str]):
        self._by_raw: dict[str, str] = {}
        self._by_core: dict[str, list[tuple[str, str]]] = {}
        for name in names:
            self._by_raw.setdefault(name.lower(), name)
            core, suffix = normalise(name)
            self._by_core.setdefault(core, []).append((suffix, name))

    def match(self, stem: str) -> Match:
        raw = self._by_raw.get(stem.lower())
        if raw is not None:
            return Match(raw, "stored-name")
        core, suffix = normalise(stem)
        cands = self._by_core.get(core, [])
        if not cands:
            return Match(None, "no-match", f"no image with the name {stem!r} (cleaned {core!r})")
        if suffix:
            narrowed = [name for s, name in cands if s == suffix]
            if len(narrowed) == 1:
                return Match(narrowed[0], "cleaned")
            if not narrowed:
                have = ", ".join(sorted({s or "(none)" for s, _ in cands}))
                return Match(
                    None,
                    "no-match",
                    f"core {core!r} exists but not with suffix {suffix}; have {have}",
                )
            return Match(
                None,
                "ambiguous",
                f"{len(narrowed)} images share {core}{suffix}",
                tuple(sorted(narrowed)),
            )
        if len(cands) == 1:
            return Match(cands[0][1], "cleaned")
        names = tuple(sorted(name for _, name in cands))
        suffixes = ", ".join(sorted({s or "(none)" for s, _ in cands}))
        return Match(
            None,
            "ambiguous",
            f"{len(names)} images share the core {core!r}; add a suffix ({suffixes})",
            names,
        )


def suggested_filename(name: str) -> str:
    """The PNG file name a template suggests for a stored image: the cleaned core and its
    suffix, so ``~-gus_art_color_white_c`` -> ``us_art_color_white_c.png``."""
    core, suffix = normalise(name)
    stem = (core + suffix) or name.lower()
    return f"{stem}.png"


@dataclass
class PackMap:
    """An explicit file-name to stored-image-name map from a JSON or CSV file."""

    entries: dict[str, str] = field(default_factory=dict)

    def get(self, stem: str, file_name: str) -> str | None:
        for key in (file_name, stem):
            if key in self.entries:
                return self.entries[key]
        low = {k.lower(): v for k, v in self.entries.items()}
        return low.get(file_name.lower()) or low.get(stem.lower())


def load_map(path: str | Path) -> PackMap:
    """Read a map file: JSON ``{"file": "image", ...}`` or CSV ``file,image`` rows (a
    ``file,image`` header row is skipped). The key may be a file name or its stem."""
    p = Path(path)
    if not p.is_file():
        raise TexpackError(f"{p}: expected a map file, found none")
    text = p.read_text(encoding="utf-8")
    entries: dict[str, str] = {}
    if p.suffix.lower() == ".json" or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TexpackError(f"{p}: not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            kind = type(data).__name__
            raise TexpackError(f"{p}: expected a JSON object of file -> image, found {kind}")
        for key, value in data.items():
            if not isinstance(value, str):
                raise TexpackError(f"{p}: entry {key!r}: expected an image name string")
            entries[str(key)] = value
    else:
        for row in csv.reader(io.StringIO(text)):
            cells = [c.strip() for c in row if c.strip()]
            if len(cells) < 2:
                continue
            header = cells[0].lower() in ("file", "filename")
            header = header and cells[1].lower() in ("image", "asset", "name")
            if header:
                continue
            entries[cells[0]] = cells[1]
    if not entries:
        raise TexpackError(f"{p}: no file -> image entries found")
    return PackMap(entries)
