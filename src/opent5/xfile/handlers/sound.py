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
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream

SAT_LOADED = 1


def read_sound_file(st: XStream, sf: Chunk) -> dict:
    kind = sf.u8(4)
    out: dict = {"type": kind, "exists": sf.u8(5), "u": sf.u32(0)}
    if kind == SAT_LOADED:
        if st.follows(sf, 0):
            st.alloc(3)
            ls = st.load(60)
            loaded: dict = {"name": st.string(ls, 0), "raw": ls.bytes(), "seek_table": None}
            if st.follows(ls, 0x30, owned=True):
                st.alloc(3)
                loaded["seek_table"] = st.load(4 * ls.u32(0x2C)).bytes()
            st.push(Block.LARGE)
            st.push(Block.PHYSICAL)
            loaded["data"] = None
            if st.follows(ls, 0x38, owned=True):
                st.alloc(2047)
                loaded["data"] = st.load(ls.u32(0x34)).bytes()
            st.pop()
            st.pop()
            out["loaded"] = loaded
    elif st.follows(sf, 0):
        st.alloc(3)
        ss = st.load(24)
        streamed: dict = {"filename": st.string(ss, 0), "raw": ss.bytes(), "prime_snd": None}
        if st.follows(ss, 4):
            st.alloc(3)
            ps = st.load(12)
            prime: dict = {"name": st.string(ps, 0), "size": ps.u32(8), "buffer": None}
            st.push(Block.PHYSICAL)
            if st.follows(ps, 4, owned=True):
                st.alloc(2047)
                prime["buffer"] = st.load(ps.u32(8)).bytes()
            st.pop()
            streamed["prime_snd"] = prime
        out["streamed"] = streamed
    return out


def read_alias(st: XStream, a: Chunk) -> dict:
    out = {
        "name": st.string(a, 0),
        "subtitle": st.string(a, 8),
        "secondary_name": st.string(a, 12),
        "sound_file": None,
        "raw": a.bytes(),
    }
    if st.follows(a, 16):
        st.alloc(3)
        out["sound_file"] = read_sound_file(st, st.load(8))
    return out


@register
class SoundHandler(Handler):
    asset_type = AssetType.SOUND
    header_size = 40

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        count = h.u32(4)
        lists = None
        if st.follows(h, 8, owned=True):
            st.alloc(3)
            table = st.load(20 * count)
            lists = []
            for entry in table.items(20, count):
                alias_list: dict = {"name": st.string(entry, 0), "aliases": None}
                if st.follows(entry, 8, owned=True):
                    n = entry.s32(12)
                    st.alloc(3)
                    aliases = st.load(84 * n)
                    alias_list["aliases"] = [read_alias(st, a) for a in aliases.items(84, n)]
                lists.append(alias_list)
        alias_index = None
        if st.follows(h, 12, owned=True):
            st.alloc(3)
            alias_index = st.load(4 * count).bytes()
        radverbs = None
        if st.follows(h, 0x1C, owned=True):
            st.alloc(3)
            radverbs = st.load(96 * h.u32(0x18)).bytes()
        snapshots = None
        if st.follows(h, 0x24, owned=True):
            st.alloc(3)
            snapshots = st.load(348 * h.u32(0x20)).bytes()
        st.pop()
        return {
            "name": name,
            "alias_count": count,
            "alias_lists": lists,
            "alias_index": alias_index,
            "pack_hash": h.u32(0x10),
            "pack_location": h.u32(0x14),
            "radverbs": radverbs,
            "snapshots": snapshots,
        }


@register
class SoundPatchHandler(Handler):
    asset_type = AssetType.SOUND_PATCH
    header_size = 20

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        elements = None
        if st.follows(h, 8, owned=True):
            st.alloc(3)
            elements = st.load(4 * h.u32(4)).bytes()
        files = None
        if st.follows(h, 16):
            n = h.u32(12)
            st.alloc(3)
            table = st.load(8 * n)
            files = [read_sound_file(st, f) for f in table.items(8, n)]
        st.pop()
        return {"name": name, "elements": elements, "files": files}


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
    asset_type = AssetType.SNDDRIVERGLOBALS
    header_size = 52

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        out: dict = {"name": st.string(h, 0)}
        for key, count_at, ptr_at, size in DRIVER_ARRAYS:
            out[key] = None
            if st.follows(h, ptr_at, owned=True):
                st.alloc(3)
                out[key] = st.load(size * h.u32(count_at)).bytes()
        st.pop()
        return out
