"""game_map_sp (16) and game_map_mp (17): GameWorldSp / GameWorldMp, 44 bytes,
each a name and a PathData. docs/research/structs-map.md section 6.

PathData: +4 nodeCount, +8 nodes (align 4, 0x80 x (nodeCount + 128); per
node +0x40 links, 12 x u16 at +0x3e), +0xc basenodes (RUNTIME, 16 x (nodeCount
+ 128)), +0x14 chainNodeForNode, +0x18 nodeForChainNode (2 x nodeCount each),
+0x1c/+0x20 pathVis, +0x24/+0x28 nodeTree (0x10 each; leaves with axis < 0
carry a u16 list at +0xc of u32 +0x8). Loaders: SP Ptr 0x24b8f8, MP Ptr
0x248998, PathData 0x23b1f0.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, items, register, runtime
from opent5.xfile.stream import Chunk, XStream

PATHNODE_SIZE = 0x80
EXTRA_NODES = 128


def gameworld_body(io: XStream, h: Chunk, node: dict) -> None:
    """Node: "header", "name", "nodes" (elements with "links"), "chain_node_for_node",
    "node_for_chain_node", "path_vis", "node_tree" (elements; leaves carry "nodes")."""
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    node_count = h.u32(4)
    total = node_count + EXTRA_NODES
    for n, element in (
        items(io, h, 8, 3, PATHNODE_SIZE, total, node, "nodes", kind="pathnode_t") or ()
    ):
        array(io, n, 0x40, 3, 12 * n.u16(0x3E), element, "links")
    runtime(io, h, 0xC, 15, 16 * total)  # basenodes
    array(io, h, 0x14, 1, 2 * node_count, node, "chain_node_for_node")
    array(io, h, 0x18, 1, 2 * node_count, node, "node_for_chain_node")
    array(io, h, 0x20, 0, h.u32(0x1C), node, "path_vis")
    for t, element in (
        items(io, h, 0x28, 3, 0x10, h.u32(0x24), node, "node_tree", kind="pathnode_tree_t") or ()
    ):
        if t.s32(0) < 0:
            array(io, t, 0xC, 1, 2 * t.u32(8), element, "nodes")
        else:
            # Interior node: two child pointers, converted only.
            io.convert(t, 0x8)
            io.convert(t, 0xC)
    io.pop()


@register
class GameWorldSpHandler(Handler):
    kind = "GameWorld"
    asset_type = AssetType.GAME_MAP_SP
    header_size = 0x2C

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        gameworld_body(io, header, node)


@register
class GameWorldMpHandler(Handler):
    kind = "GameWorld"
    asset_type = AssetType.GAME_MAP_MP
    header_size = 0x2C

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        gameworld_body(io, header, node)
