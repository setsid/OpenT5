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
from opent5.xfile.handlers.base import Handler, array, blob, register, runtime
from opent5.xfile.stream import Chunk, XStream

PATHNODE_SIZE = 0x80
EXTRA_NODES = 128


def read_gameworld(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    name = st.string(h, 0)
    node_count = h.u32(4)
    total = node_count + EXTRA_NODES
    nodes = array(st, h, 8, 3, PATHNODE_SIZE * total)
    links = None
    if nodes is not None:
        links = []
        for node in nodes.items(PATHNODE_SIZE, total):
            links.append(blob(array(st, node, 0x40, 3, 12 * node.u16(0x3E))))
    runtime(st, h, 0xC, 15, 16 * total)
    chain_for_node = array(st, h, 0x14, 1, 2 * node_count)
    node_for_chain = array(st, h, 0x18, 1, 2 * node_count)
    path_vis = array(st, h, 0x20, 0, h.u32(0x1C))
    tree_count = h.u32(0x24)
    tree = array(st, h, 0x28, 3, 0x10 * tree_count)
    leaves = None
    if tree is not None:
        leaves = []
        for t in tree.items(0x10, tree_count):
            if t.s32(0) < 0:
                leaves.append(blob(array(st, t, 0xC, 1, 2 * t.u32(8))))
            else:
                # Interior node: two child pointers, converted only.
                st.convert(t, 0x8)
                st.convert(t, 0xC)
                leaves.append(None)
    st.pop()
    return {
        "name": name,
        "path": {
            "node_count": node_count,
            "nodes": blob(nodes),
            "node_links": links,
            "chain_node_count": h.u32(0x10),
            "chain_node_for_node": blob(chain_for_node),
            "node_for_chain_node": blob(node_for_chain),
            "vis_bytes": h.u32(0x1C),
            "path_vis": blob(path_vis),
            "node_tree_count": tree_count,
            "node_tree": blob(tree),
            "node_tree_leaves": leaves,
        },
    }


@register
class GameWorldSpHandler(Handler):
    asset_type = AssetType.GAME_MAP_SP
    header_size = 0x2C

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_gameworld(st, header)


@register
class GameWorldMpHandler(Handler):
    asset_type = AssetType.GAME_MAP_MP
    header_size = 0x2C

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_gameworld(st, header)
