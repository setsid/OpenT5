"""xanim (4): XAnimParts, 104 bytes. docs/research/structs-content.md section 10.

Load order: name, names [nz] (align 2, 2 x boneCount[11]), notify [nz] (align
4, 8 x notifyCount), deltaPart [nz] (align 4), dataByte, dataShort, dataInt,
randomDataShort, randomDataByte, randomDataInt (all [nz]), indices [nz]
(byte indices when numframes <= 255). Loaders: Ptr 0x244db8, struct 0x244728,
delta part 0x23bfb0, translation frames 0x235dd0, quaternion frames 0x23a588.

Node: "header", "name", one key per array (bytes), "delta_part" (a node: "raw",
"trans" and "quat" sub-nodes holding "head", "frame0" or "frames_head",
"indices", "frames").
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, register
from opent5.xfile.stream import Chunk, XStream


def _part(io: XStream, parent: Chunk, off: int, node: dict, key: str, kind: str) -> dict | None:
    """An [nz] pointer to a sub-node; returns the node to fill, or None."""
    if io.follows(parent, off, owned=True):
        io.alloc(3)
        if io.reading:
            node[key] = {"_t": kind}
        return node[key]
    if io.reading:
        node[key] = None
    return None


def delta_part_body(io: XStream, numframes: int, node: dict) -> None:
    dp = io.load(8, node, "raw")
    index_size = 1 if numframes <= 255 else 2
    trans = _part(io, dp, 0, node, "trans", "XAnimPartTrans")
    if trans is not None:
        head = io.load(4, trans, "head")
        size, small = head.u16(0), head.u8(2)
        if size == 0:
            io.load(12, trans, "frame0")
        else:
            frames = io.load(28, trans, "frames_head")
            io.load((size + 1) * index_size, trans, "indices")
            if small:
                array(io, frames, 24, 0, 3 * (size + 1), trans, "frames", owned=True)
            else:
                array(io, frames, 24, 3, 6 * (size + 1), trans, "frames", owned=True)
    quat = _part(io, dp, 4, node, "quat", "XAnimDeltaPartQuat")
    if quat is not None:
        head = io.load(4, quat, "head")
        size = head.u16(0)
        if size == 0:
            io.load(4, quat, "frame0")
        else:
            frames = io.load(4, quat, "frames_head")
            io.load((size + 1) * index_size, quat, "indices")
            array(io, frames, 0, 3, 4 * (size + 1), quat, "frames", owned=True)


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


def xanim_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    numframes = h.u16(14)
    array(io, h, 64, 1, 2 * h.u8(35), node, "names", owned=True)
    array(io, h, 96, 3, 8 * h.u8(36), node, "notify", owned=True)
    delta = _part(io, h, 100, node, "delta_part", "XAnimDeltaPart")
    if delta is not None:
        delta_part_body(io, numframes, delta)
    for key, off, count, mask, size in _data_arrays(h):
        array(io, h, off, mask, size * count, node, key, owned=True)
    if numframes <= 255:
        array(io, h, 92, 0, h.u32(44), node, "indices", owned=True)
    else:
        array(io, h, 92, 1, 2 * h.u32(44), node, "indices", owned=True)
    io.note(node, "numframes", numframes)
    io.pop()


@register
class XAnimHandler(Handler):
    kind = "XAnimParts"
    asset_type = AssetType.XANIM
    header_size = 104

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        xanim_body(io, header, node)
