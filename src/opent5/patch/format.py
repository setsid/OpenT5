"""The .o5patch container: a manifest plus one record per changed asset.

A patch is one file, whole and compressed. Its only payload is the new content of the
assets the author changed; it carries no stock game data (docs/patch-format.md). The layout
is deliberately plain so the format can be read without the rest of OpenT5:

    bytes 0..8      MAGIC
    byte  8         format version (so a reader rejects a newer file before inflating)
    bytes 9..       zlib stream of the body

    body = u32 manifest_json_len, manifest_json (UTF-8)
           u32 change_count
           change_count times:
               u32 meta_json_len, meta_json (UTF-8)
               u32 blob_len, blob

Every integer is big-endian, to match the fastfile the patch is built from. The blob is the
new content exactly as the matching editor takes it: rawfile bytes, CSV for a stringtable,
the value for a localize entry, or a PNG for an image.
"""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any

MAGIC = b"O5PATCH\x00"
FORMAT_VERSION = 1
SUFFIX = ".o5patch"

_U32 = struct.Struct(">I")


class PatchFormatError(Exception):
    """A .o5patch file that cannot be read: what was expected and what was found."""


@dataclass
class PatchChange:
    """One changed asset: where it is, how to apply it, and the new content."""

    #: Asset-list index in the source zone (stable: the source sha1 is verified on apply).
    index: int
    #: Asset type name and asset name, checked against the source on apply.
    type_name: str
    name: str | None
    #: "text", "table", "localize" or "image": which editor reproduces the change.
    op: str
    #: The new content, exactly as that editor reads it.
    blob: bytes

    def meta(self) -> dict[str, Any]:
        return {"index": self.index, "type": self.type_name, "name": self.name, "op": self.op}

    @property
    def size(self) -> int:
        return len(self.blob)


@dataclass
class Manifest:
    """What the patch was built from and what it produces."""

    source_zone: str
    source_ff_sha1: str
    source_content_sha1: str
    result_content_sha1: str
    signed: bool
    assets_changed: int
    created: str
    tool_version: str
    generator: str
    format_version: int = FORMAT_VERSION
    #: Carried through for a reader; not part of the identity checks.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "format_version": self.format_version,
            "generator": self.generator,
            "tool_version": self.tool_version,
            "created": self.created,
            "source_zone": self.source_zone,
            "source_ff_sha1": self.source_ff_sha1,
            "source_content_sha1": self.source_content_sha1,
            "result_content_sha1": self.result_content_sha1,
            "signed": self.signed,
            "assets_changed": self.assets_changed,
        }
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        known = {
            "format_version",
            "generator",
            "tool_version",
            "created",
            "source_zone",
            "source_ff_sha1",
            "source_content_sha1",
            "result_content_sha1",
            "signed",
            "assets_changed",
        }
        try:
            return cls(
                source_zone=data["source_zone"],
                source_ff_sha1=data["source_ff_sha1"],
                source_content_sha1=data["source_content_sha1"],
                result_content_sha1=data["result_content_sha1"],
                signed=bool(data.get("signed", False)),
                assets_changed=int(data.get("assets_changed", 0)),
                created=data.get("created", ""),
                tool_version=data.get("tool_version", ""),
                generator=data.get("generator", ""),
                format_version=int(data.get("format_version", FORMAT_VERSION)),
                extra={k: v for k, v in data.items() if k not in known},
            )
        except KeyError as exc:
            raise PatchFormatError(f"the manifest is missing the field {exc.args[0]!r}") from None


def write_patch(manifest: Manifest, changes: list[PatchChange]) -> bytes:
    """Serialise a manifest and its changes into one compressed .o5patch file."""
    manifest_json = json.dumps(manifest.to_dict(), separators=(",", ":")).encode("utf-8")
    parts = [_U32.pack(len(manifest_json)), manifest_json, _U32.pack(len(changes))]
    for change in changes:
        meta_json = json.dumps(change.meta(), separators=(",", ":")).encode("utf-8")
        parts += [_U32.pack(len(meta_json)), meta_json, _U32.pack(len(change.blob)), change.blob]
    body = b"".join(parts)
    return MAGIC + bytes([manifest.format_version]) + zlib.compress(body, 9)


def read_patch(data: bytes) -> tuple[Manifest, list[PatchChange]]:
    """Parse a .o5patch file. Raises PatchFormatError on anything unexpected."""
    data = bytes(data)
    if data[: len(MAGIC)] != MAGIC:
        raise PatchFormatError(
            f"expected the patch magic {MAGIC!r} at offset 0, found {data[: len(MAGIC)]!r}"
        )
    version = data[len(MAGIC)]
    if version > FORMAT_VERSION:
        raise PatchFormatError(
            f"expected patch format version at most {FORMAT_VERSION}, found {version} "
            "(made by a newer OpenT5)"
        )
    try:
        body = zlib.decompress(data[len(MAGIC) + 1 :])
    except zlib.error as exc:
        raise PatchFormatError(f"the patch body does not inflate: {exc}") from None

    view = memoryview(body)
    pos = 0

    def take(n: int, what: str) -> memoryview:
        nonlocal pos
        if pos + n > len(view):
            raise PatchFormatError(
                f"the patch ends early reading {what}: wanted {n} bytes at {pos}, "
                f"found {len(view) - pos}"
            )
        chunk = view[pos : pos + n]
        pos += n
        return chunk

    def take_u32(what: str) -> int:
        return _U32.unpack(take(4, what))[0]

    manifest = Manifest.from_dict(
        json.loads(bytes(take(take_u32("the manifest length"), "the manifest")))
    )
    count = take_u32("the change count")
    changes: list[PatchChange] = []
    for n in range(count):
        meta = json.loads(bytes(take(take_u32(f"change {n} meta length"), f"change {n} meta")))
        blob = bytes(take(take_u32(f"change {n} blob length"), f"change {n} blob"))
        changes.append(
            PatchChange(
                index=int(meta["index"]),
                type_name=meta.get("type", ""),
                name=meta.get("name"),
                op=meta["op"],
                blob=blob,
            )
        )
    return manifest, changes
