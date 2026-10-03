"""sound (11), sound_patch (12) and snddriverglobals (29).
docs/research/structs-content.md section 12.

SndBank (40): +0 name, +4 aliasCount, +8 alias lists [nz] (align 4, LS 20 x
count, then each list), +0xc aliasIndex [nz], +0x18/+0x1c radverbs [nz],
+0x20/+0x24 snapshots [nz]. snd_alias_list_t (20): +0 name, +8 head [nz]
(align 4, LS 84 x +0xc). snd_alias_t (84): name, subtitle, secondaryname,
+0x10 soundFile [-1] (align 4, LS 8). SoundFile (8): +0 u, +4 type; type 1 ->
LoadedSound (60), else StreamedSound (24, PS3). SndPatch (20), SndDriverGlobals
(52). Loaders: SndBank Ptr 0x2466a0, struct 0x246310; SndPatch 0x2467b0;
SndDriverGlobals 0x2443a0.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, items, register
from opent5.xfile.stream import Chunk, XStream

SAT_LOADED = 1


def _sub(io: XStream, parent: Chunk, off: int, node: dict, key: str, kind: str):
    """A pointer to one sub-struct ([-1]): align 4 and return its node, or None."""
    if io.follows(parent, off):
        io.alloc(3)
        if io.reading:
            node[key] = {"_t": kind}
        return node[key]
    if io.reading:
        node[key] = None
    return None


def sound_file_body(io: XStream, sf: Chunk, node: dict) -> None:
    if sf.u8(4) == SAT_LOADED:
        loaded = _sub(io, sf, 0, node, "loaded", "LoadedSound")
        if loaded is not None:
            ls = io.load(60, loaded, "raw")
            io.string(ls, 0, loaded, "name")
            array(io, ls, 0x30, 3, 4 * ls.u32(0x2C), loaded, "seek_table", owned=True)
            io.push(Block.LARGE)
            io.push(Block.PHYSICAL)
            array(io, ls, 0x38, 2047, ls.u32(0x34), loaded, "data", owned=True)
            io.pop()
            io.pop()
    else:
        streamed = _sub(io, sf, 0, node, "streamed", "StreamedSound")
        if streamed is not None:
            ss = io.load(24, streamed, "raw")
            io.string(ss, 0, streamed, "filename")
            prime = _sub(io, ss, 4, streamed, "prime_snd", "PrimedSound")
            if prime is not None:
                ps = io.load(12, prime, "raw")
                io.string(ps, 0, prime, "name")
                io.push(Block.PHYSICAL)
                array(io, ps, 4, 2047, ps.u32(8), prime, "buffer", owned=True)
                io.pop()


def alias_body(io: XStream, a: Chunk, node: dict) -> None:
    io.string(a, 0, node, "name")
    io.string(a, 8, node, "subtitle")
    io.string(a, 12, node, "secondary_name")
    sound_file = _sub(io, a, 16, node, "sound_file", "SoundFile")
    if sound_file is not None:
        sound_file_body(io, io.load(8, sound_file, "raw"), sound_file)


@register
class SoundHandler(Handler):
    kind = "SndBank"
    asset_type = AssetType.SOUND
    header_size = 40

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        count = h.u32(4)
        for entry, alias_list in (
            items(io, h, 8, 3, 20, count, node, "alias_lists", owned=True, kind="snd_alias_list_t")
            or ()
        ):
            io.string(entry, 0, alias_list, "name")
            n = entry.s32(12)
            for a, alias in (
                items(io, entry, 8, 3, 84, n, alias_list, "aliases", owned=True, kind="snd_alias_t")
                or ()
            ):
                alias_body(io, a, alias)
        array(io, h, 12, 3, 4 * count, node, "alias_index", owned=True)
        array(io, h, 0x1C, 3, 96 * h.u32(0x18), node, "radverbs", owned=True)
        array(io, h, 0x24, 3, 348 * h.u32(0x20), node, "snapshots", owned=True)
        io.pop()


@register
class SoundPatchHandler(Handler):
    kind = "SndPatch"
    asset_type = AssetType.SOUND_PATCH
    header_size = 20

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        array(io, h, 8, 3, 4 * h.u32(4), node, "elements", owned=True)
        for f, element in items(io, h, 16, 3, 8, h.u32(12), node, "files", kind="SoundFile") or ():
            sound_file_body(io, f, element)
        io.pop()


#: SndDriverGlobals arrays: (name, count offset, pointer offset, element size), in load order.
DRIVER_ARRAYS = (
    ("groups", 4, 8, 80),
    ("curves", 12, 16, 100),
    ("pans", 20, 24, 60),
    ("snapshot_groups", 28, 32, 32),
    ("contexts", 36, 40, 40),
    ("masters", 44, 48, 176),
)


@register
class SndDriverGlobalsHandler(Handler):
    kind = "SndDriverGlobals"
    asset_type = AssetType.SNDDRIVERGLOBALS
    header_size = 52

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        for key, count_at, ptr_at, size in DRIVER_ARRAYS:
            array(io, h, ptr_at, 3, size * h.u32(count_at), node, key, owned=True)
        io.pop()
