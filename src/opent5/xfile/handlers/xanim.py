"""xanim (4): XAnimParts, 104 bytes. docs/research/structs-content.md section 10.

Load order: name, names [nz] (align 2, 2 x boneCount[11]), notify [nz] (align
4, 8 x notifyCount), deltaPart [nz] (align 4), dataByte, dataShort, dataInt,
randomDataShort, randomDataByte, randomDataInt (all [nz]), indices [nz]
(byte indices when numframes <= 255). Loaders: Ptr 0x244db8, struct 0x244728,
delta part 0x23bfb0, translation frames 0x235dd0, quaternion frames 0x23a588.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, register
from opent5.xfile.stream import Chunk, XStream


def read_delta_part(st: XStream, numframes: int) -> dict:
    dp = st.load(8)
    index_size = 1 if numframes <= 255 else 2
    trans = None
    if st.follows(dp, 0, owned=True):
        st.alloc(3)
        head = st.load(4)
        size, small = head.u16(0), head.u8(2)
        trans = {"size": size, "small_trans": small}
        if size == 0:
            trans["frame0"] = st.load(12).bytes()
        else:
            frames = st.load(28)
            trans["mins_size"] = frames.data[0:24].tobytes()
            trans["indices"] = st.load((size + 1) * index_size).bytes()
            if st.follows(frames, 24, owned=True):
                if small:
                    st.alloc(0)
                    trans["frames"] = st.load(3 * (size + 1)).bytes()
                else:
                    st.alloc(3)
                    trans["frames"] = st.load(6 * (size + 1)).bytes()
    quat = None
    if st.follows(dp, 4, owned=True):
        st.alloc(3)
        head = st.load(4)
        size = head.u16(0)
        quat = {"size": size}
        if size == 0:
            quat["frame0"] = st.load(4).bytes()
        else:
            frames = st.load(4)
            quat["indices"] = st.load((size + 1) * index_size).bytes()
            if st.follows(frames, 0, owned=True):
                st.alloc(3)
                quat["frames"] = st.load(4 * (size + 1)).bytes()
    return {"trans": trans, "quat": quat}


def _data_arrays(h: Chunk):
    """(key, pointer offset, count, align mask, element size) for the six data arrays,
    in load order."""
    return (
        ("data_byte", 68, h.u16(4), 0, 1),
        ("data_short", 72, h.u16(6), 1, 2),
        ("data_int", 76, h.u16(8), 3, 4),
        ("random_data_short", 80, h.u32(40), 1, 2),
        ("random_data_byte", 84, h.u16(10), 0, 1),
        ("random_data_int", 88, h.u16(12), 3, 4),
    )


def read_xanim(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    out: dict = {"name": st.string(h, 0)}
    numframes = h.u16(14)
    out["numframes"] = numframes
    out["bone_count"] = h.data[0x18:0x24].tolist()
    out["notify_count"] = h.u8(36)
    out["index_count"] = h.u32(44)
    out["names"] = None
    if st.follows(h, 64, owned=True):
        st.alloc(1)
        names = st.load(2 * h.u8(35))
        out["names"] = [names.u16(2 * i) for i in range(h.u8(35))]
    out["notify"] = None
    if st.follows(h, 96, owned=True):
        st.alloc(3)
        out["notify"] = st.load(8 * h.u8(36)).bytes()
    out["delta_part"] = None
    if st.follows(h, 100, owned=True):
        st.alloc(3)
        out["delta_part"] = read_delta_part(st, numframes)
    for key, off, count, mask, size in _data_arrays(h):
        out[key] = None
        if st.follows(h, off, owned=True):
            st.alloc(mask)
            out[key] = st.load(size * count).bytes()
    out["indices"] = None
    if st.follows(h, 92, owned=True):
        if numframes <= 255:
            st.alloc(0)
            out["indices"] = st.load(h.u32(44)).bytes()
        else:
            st.alloc(1)
            out["indices"] = st.load(2 * h.u32(44)).bytes()
    out["header"] = h.bytes()
    st.pop()
    return out


@register
class XAnimHandler(Handler):
    asset_type = AssetType.XANIM
    header_size = 104

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_xanim(st, header)
