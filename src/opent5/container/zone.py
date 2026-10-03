"""A zone open for editing: its decompressed content as one buffer, saved
back with every untouched chunk carried verbatim.

    zone = Zone.open("mp_nuked.ff")
    zone.content[0x1234:0x1238] = b"\\x00\\x00\\x00\\x01"
    result = zone.save("mp_nuked_edited.ff")
    verify("mp_nuked_edited.ff", expected=zone.content)

The model. Opening keeps, for every chunk, its inflated length and its
original deflate stream (the chunk after decryption, before inflation).
Saving compares the current content against the content as opened:

* chunk 0, the 36-byte prefix, is always a chunk of its own, as in every
  retail zone; it is recompressed only if its bytes changed (the length
  field in it changes whenever the content length does);
* chunks that match from the front are kept, and so are chunks that match
  from the back once the edit is past, wherever they now sit;
* what is left in the middle is either re-deflated on the original
  boundaries (when its length did not change, and then only the chunks that
  differ are deflated) or re-split into XCHUNK_WRITE_SIZE pieces, which is
  what the original writer does.

Encryption is redone for every chunk because the nonces chain: a kept chunk
behind an edited one on the same stream gets a new nonce. A kept chunk with
only kept chunks before it gets its original nonce back, so an unmodified
zone saves byte-identical to the file it came from, ring-buffer gap bytes
and padding included.

The signature at 0x3c is copied unchanged. It cannot be regenerated, so an
edited zone loads only on a client whose signature check is patched out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opent5.container.fastfile import (
    CHUNKS_OFFSET,
    DEFAULT_LEVEL,
    SALSA20_KEY,
    STREAM_COUNT,
    XCHUNK_WRITE_SIZE,
    ZONE_SIZE_PREFIX,
    FastFile,
    FastFileError,
    assemble,
    carries_console_signature,
    check_header,
    declared_size,
    deflate,
    padded_length,
    read_fastfile,
    with_declared_size,
    zone_name_of,
)


@dataclass(frozen=True)
class SaveResult:
    data: bytes
    #: Chunks whose original deflate stream was carried over.
    kept: int
    #: Chunks deflated afresh.
    deflated: int
    #: Whether the output is byte-identical to the file that was opened.
    identical: bool
    #: Whether the header carries a console signature, which an edit voids.
    signed: bool

    @property
    def chunks(self) -> int:
        return self.kept + self.deflated


class Zone:
    """A fastfile opened for editing. `content` is a bytearray; change it
    freely, including its length, then `save`."""

    def __init__(self, fastfile: FastFile, source: bytes | None = None, key: bytes = SALSA20_KEY):
        self.key = key
        self.header = bytearray(fastfile.header)
        self._header = bytes(fastfile.header)
        self._original = fastfile.content
        self.content = bytearray(self._original)
        self._sizes = [len(c.body) for c in fastfile.chunks]
        self._deflated = [c.deflated for c in fastfile.chunks]
        self._starts = []
        at = 0
        for size in self._sizes:
            self._starts.append(at)
            at += size
        # Ring-gap bytes by file offset, carried only where the layout up to
        # them is unchanged.
        self._gaps = {c.offset - len(c.skipped): c.skipped for c in fastfile.chunks if c.skipped}
        self._gaps.update(dict(fastfile.trailing_skips))
        self._chunk_offsets = [c.offset for c in fastfile.chunks]
        self._total = fastfile.total_bytes
        self._end = fastfile.end
        self._source = source

    @classmethod
    def open(cls, source: str | Path | bytes, key: bytes = SALSA20_KEY) -> Zone:
        data = source if isinstance(source, bytes | bytearray) else Path(source).read_bytes()
        data = bytes(data)
        return cls(read_fastfile(data, key), data, key)

    @property
    def name(self) -> str:
        return zone_name_of(bytes(self.header)).decode("ascii", errors="replace")

    @property
    def chunk_sizes(self) -> list[int]:
        """The inflated size of every chunk as opened."""
        return list(self._sizes)

    @property
    def modified(self) -> bool:
        return bytes(self.header) != self._header or self.content != self._original

    # The plan: a list of (body, deflated-or-None) covering the content.

    def _matches(self, content: bytes, at: int, index: int) -> bool:
        size = self._sizes[index]
        start = self._starts[index]
        return (
            at + size <= len(content)
            and content[at : at + size] == self._original[start : start + size]
        )

    def plan(self, content: bytes) -> list[tuple[bytes, bytes | None]]:
        """How `content` will be chunked: (body, original deflate stream or
        None when it has to be deflated)."""
        count = len(self._sizes)
        if content == self._original:
            return [
                (self._original[s : s + n], d)
                for s, n, d in zip(self._starts, self._sizes, self._deflated, strict=True)
            ]
        if len(content) < ZONE_SIZE_PREFIX:
            raise FastFileError(f"a zone is at least {ZONE_SIZE_PREFIX} bytes, got {len(content)}")
        pieces: list[tuple[bytes, bytes | None]] = []
        # The prefix chunk is pinned, as the original writer pins it.
        first = self._sizes[0]
        pinned = first == ZONE_SIZE_PREFIX
        if pinned:
            head = content[:first]
            pieces.append((head, self._deflated[0] if self._matches(content, 0, 0) else None))
            index, at = 1, first
        else:
            index, at = 0, 0
        while index < count and self._matches(content, at, index):
            pieces.append((content[at : at + self._sizes[index]], self._deflated[index]))
            at += self._sizes[index]
            index += 1
        last, end = count, len(content)
        tail: list[tuple[bytes, bytes | None]] = []
        while last > index and end - self._sizes[last - 1] >= at:
            if not self._matches(content, end - self._sizes[last - 1], last - 1):
                break
            last -= 1
            end -= self._sizes[last]
            tail.append((content[end : end + self._sizes[last]], self._deflated[last]))
        tail.reverse()
        middle = content[at:end]
        original_middle = sum(self._sizes[index:last])
        if len(middle) == original_middle:
            for k in range(index, last):
                body = content[at : at + self._sizes[k]]
                pieces.append((body, self._deflated[k] if self._matches(content, at, k) else None))
                at += self._sizes[k]
        else:
            for step in range(0, len(middle), XCHUNK_WRITE_SIZE):
                pieces.append((middle[step : step + XCHUNK_WRITE_SIZE], None))
        return pieces + tail

    def build(self, derive_size: bool = True, recompress: bool = False) -> SaveResult:
        """The file `save` would write.

        derive_size makes the content's own length field agree with its
        length (a no-op unless the length changed). recompress deflates every
        chunk afresh instead of carrying the original streams; for an
        unmodified zone the result is still byte-identical, because level 9,
        memLevel 9 reproduces every retail chunk.
        """
        header = bytes(self.header)
        check_header(header)
        content = bytes(self.content)
        if derive_size:
            content = with_declared_size(content)
        plan = self.plan(content)
        deflated: list[bytes] = []
        kept = 0
        for body, stream in plan:
            if stream is not None and not recompress:
                deflated.append(stream)
                kept += 1
            else:
                deflated.append(deflate(body, DEFAULT_LEVEL))
        unchanged_layout = header == self._header and [len(d) for d in deflated] == [
            len(d) for d in self._deflated
        ]
        gaps = self._carried_gaps(deflated)
        # Same header and the same stored lengths: the same layout, so the
        # original length (and any padding that broke the rule) stands.
        total = self._total if unchanged_layout else 0
        data = assemble(header, deflated, self.key, total, gaps)
        identical = self._source is not None and data == self._source
        return SaveResult(
            data=data,
            kept=kept,
            deflated=len(deflated) - kept,
            identical=identical,
            signed=carries_console_signature(header),
        )

    def _carried_gaps(self, deflated: list[bytes]) -> dict[int, bytes]:
        """The original gap bytes that still sit at the same place: those
        before the first chunk whose stored length differs."""
        if not self._gaps:
            return {}
        limit = None
        for index, stream in enumerate(deflated):
            if index >= len(self._deflated) or len(stream) != len(self._deflated[index]):
                limit = self._chunk_offsets[index] if index < len(self._chunk_offsets) else None
                break
        if limit is None and len(deflated) == len(self._deflated):
            return dict(self._gaps)
        if limit is None:
            limit = self._end
        return {at: fill for at, fill in self._gaps.items() if at < limit}

    def save(self, path: str | Path, derive_size: bool = True, recompress: bool = False):
        result = self.build(derive_size=derive_size, recompress=recompress)
        Path(path).write_bytes(result.data)
        return result


def first_difference(one: bytes, other: bytes, step: int = 0x10000) -> int:
    """The first offset where two buffers differ, or the shorter length."""
    shorter = min(len(one), len(other))
    for start in range(0, shorter, step):
        if one[start : start + step] != other[start : start + step]:
            for at in range(start, min(start + step, shorter)):
                if one[at] != other[at]:
                    return at
    return shorter


@dataclass(frozen=True)
class Verified:
    chunks: int
    content_bytes: int
    terminators: int
    padded_as_original_writer: bool


def verify(
    source: str | Path | bytes, expected: bytes | None = None, key: bytes = SALSA20_KEY
) -> Verified:
    """Reopen a fastfile and check everything the loader relies on: every
    chunk decrypts and inflates, one terminator per stream, the zone's own
    length field matches its content, and (if given) the content is
    `expected`. Raises FastFileError naming the offset and the values."""
    data = source if isinstance(source, bytes | bytearray) else Path(source).read_bytes()
    fastfile = read_fastfile(bytes(data), key)
    if fastfile.terminators != STREAM_COUNT:
        raise FastFileError(
            f"wanted {STREAM_COUNT} terminators ending at {fastfile.end:#x}, "
            f"found {fastfile.terminators}"
        )
    if any(data[fastfile.end :]):
        raise FastFileError(f"the padding after {fastfile.end:#x} is not all zeros")
    content = fastfile.content
    if len(fastfile.chunks[0].body) != ZONE_SIZE_PREFIX:
        raise FastFileError(
            f"chunk 0 at {CHUNKS_OFFSET:#x} inflates to {len(fastfile.chunks[0].body)} bytes, "
            f"wanted the {ZONE_SIZE_PREFIX}-byte prefix alone"
        )
    if declared_size(content) != len(content) - ZONE_SIZE_PREFIX:
        raise FastFileError(
            f"the zone declares {declared_size(content)} bytes at content offset 0, "
            f"wanted {len(content) - ZONE_SIZE_PREFIX}"
        )
    if expected is not None and content != bytes(expected):
        at = first_difference(content, bytes(expected))
        raise FastFileError(
            f"content differs at {at:#x}: {len(content)} bytes read back, "
            f"{len(expected)} expected"
        )
    return Verified(
        chunks=len(fastfile.chunks),
        content_bytes=len(content),
        terminators=fastfile.terminators,
        padded_as_original_writer=len(data) == padded_length(fastfile.end),
    )
