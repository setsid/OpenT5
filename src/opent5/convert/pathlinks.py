"""Path-node links for a converted map whose PathData has none.

A map built by our generators carries path nodes but no links: cod2map does not connect
them, and the stock "Connect Paths" step runs the PC game (``g_connectpaths``, linker_pc.dll),
which our pipeline cannot. The retail engine does not link nodes at load; it reads the baked
links, so a map with no links has every node as its own island and drops at load with
"Path nodes are not connected." (t5mp.elf 0x935211).

So the converter bakes the links itself, in the stock PathData format read from mp_nuked: for
each node a ``pathlink_s`` array (``opent5.xfile`` pathlink_s: fDist, nodeNum, then zeros) and
``constant.totalLinkCount`` (pathnode_t +0x3E), with the ``constant.Links`` pointer (+0x40)
set inline so the writer emits the array. Links are bidirectional and carry the true distance.
Only a map whose nodes are all unlinked is touched; a real map keeps its baked links.

The graph must be a single connected component, or the engine still drops the map. This module
links nodes by distance (widening until the graph connects) and refuses to proceed otherwise.
"""

from __future__ import annotations

import math
import struct

from opent5.xfile.constants import PTR_INLINE

PATHNODE_TYPE_STD = 1  # a standard path node (type 0 entries are the 128 spare nodes)
TYPE_OFF = 0
ORIGIN_OFF = 20  # pathnode_t constant.vOrigin (0x14), a vec3
TOTAL_LINK_COUNT_OFF = 62  # constant.totalLinkCount (0x3E)
LINKS_PTR_OFF = 64  # constant.Links (0x40)
PATHLINK_SIZE = 12


def _real_nodes(nodes: list) -> list[tuple[int, dict, tuple]]:
    out = []
    for i, el in enumerate(nodes):
        raw = bytes(el["raw"])
        if struct.unpack_from(">I", raw, TYPE_OFF)[0] == PATHNODE_TYPE_STD:
            out.append((i, el, struct.unpack_from(">3f", raw, ORIGIN_OFF)))
    return out


def _components(adj: dict[int, set]) -> list[set]:
    seen: set = set()
    comps = []
    for start in adj:
        if start in seen:
            continue
        stack = [start]
        comp = set()
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.add(n)
            stack += [t for t in adj[n] if t not in seen]
        comps.append(comp)
    return comps


def _graph(real: list, link_dist: float):
    adj: dict[int, set] = {i: set() for i, _, _ in real}
    dist: dict[tuple[int, int], float] = {}
    for a in range(len(real)):
        ia, _, pa = real[a]
        for b in range(a + 1, len(real)):
            ib, _, pb = real[b]
            d = math.dist(pa, pb)
            if d <= link_dist:
                adj[ia].add(ib)
                adj[ib].add(ia)
                dist[(ia, ib)] = dist[(ib, ia)] = d
    return adj, dist


def has_links(game: dict) -> bool:
    """True when any path node already carries links (a real map; leave it alone)."""
    for el in game.get("nodes") or ():
        if struct.unpack_from(">H", bytes(el["raw"]), TOTAL_LINK_COUNT_OFF)[0]:
            return True
    return False


def generate(game: dict, max_dist: float = 600.0) -> dict:
    """Bake bidirectional links into every standard path node of ``game`` (a parsed PS3
    GameWorldMp) so the graph is one connected component. Raises ``ValueError`` if the nodes
    cannot be connected within ``max_dist``. Returns a report."""
    real = _real_nodes(game.get("nodes") or [])
    if not real:
        return {"nodes": 0, "links": 0, "components": 0, "linked": False}
    # Widen the link distance until the graph is a single component (or give up).
    link_dist = 160.0
    adj, dist = _graph(real, link_dist)
    while len(_components(adj)) > 1 and link_dist < max_dist:
        link_dist = min(link_dist + 40.0, max_dist)
        adj, dist = _graph(real, link_dist)
    comps = _components(adj)
    if len(comps) != 1:
        sizes = sorted((len(c) for c in comps), reverse=True)
        raise ValueError(
            f"path nodes: {len(real)} nodes do not connect within {max_dist:g} units "
            f"({len(comps)} components {sizes}); the map needs nodes bridging the gaps"
        )
    links = 0
    for i, el, _ in real:
        targets = sorted(adj[i])
        blob = bytearray()
        for t in targets:
            blob += struct.pack(">fHbb", dist[(i, t)], t, 0, 0) + bytes(4)
        el["links"] = bytes(blob)
        raw = bytearray(el["raw"])
        struct.pack_into(">H", raw, TOTAL_LINK_COUNT_OFF, len(targets))
        struct.pack_into(">I", raw, LINKS_PTR_OFF, PTR_INLINE)
        el["raw"] = bytes(raw)
        links += len(targets)
    return {
        "nodes": len(real),
        "links": links // 2,
        "components": 1,
        "link_dist": link_dist,
        "linked": True,
    }
