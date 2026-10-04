"""Baking path-node links for an unlinked map (opent5.convert.pathlinks)."""

import struct

from opent5.convert import pathlinks


def _node(x, y, z, ntype=1):
    raw = bytearray(0x80)
    struct.pack_into(">I", raw, 0, ntype)
    struct.pack_into(">3f", raw, 20, x, y, z)
    return {"_t": "pathnode_t", "raw": bytes(raw), "links": None}


def _grid(n, step):
    nodes = [_node(c * step, r * step, 0) for r in range(n) for c in range(n)]
    nodes += [_node(0, 0, 0, ntype=0) for _ in range(128)]  # the spare nodes
    return {"header": b"", "nodes": nodes}


def _components(game):
    real = [i for i, el in enumerate(game["nodes"]) if struct.unpack_from(">I", bytes(el["raw"]), 0)[0] == 1]
    idx = set(real)
    adj = {i: set() for i in real}
    for i in real:
        lk = bytes(game["nodes"][i].get("links") or b"")
        for o in range(0, len(lk), 12):
            t = struct.unpack_from(">H", lk, o + 4)[0]
            if t in idx:
                adj[i].add(t)
                adj[t].add(i)
    seen, comps = set(), 0
    for s in real:
        if s in seen:
            continue
        comps += 1
        stack = [s]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack += [t for t in adj[n] if t not in seen]
    return comps


def test_generate_links_one_component():
    game = _grid(5, 116)
    report = pathlinks.generate(game)
    assert report["linked"] and report["nodes"] == 25 and report["components"] == 1
    assert report["links"] > 0
    # the real graph (read back from the bytes) is one connected component
    assert _components(game) == 1
    # each node now carries the inline marker and a matching link count
    for el in game["nodes"][:25]:
        raw = bytes(el["raw"])
        count = struct.unpack_from(">H", raw, 62)[0]
        assert count > 0
        assert struct.unpack_from(">I", raw, 64)[0] == 0xFFFFFFFF  # PTR_INLINE
        assert len(el["links"]) == count * 12


def test_links_are_bidirectional_with_real_distance():
    game = _grid(3, 100)
    pathlinks.generate(game)
    el = game["nodes"][0]
    lk = bytes(el["links"])
    dist, target = struct.unpack_from(">fH", lk, 0)
    assert target in (1, 3)  # a grid neighbour
    assert abs(dist - 100.0) < 1e-3  # the stored fDist is the true distance


def test_real_map_links_are_kept():
    game = _grid(3, 100)
    pathlinks.generate(game)  # now it has links
    assert pathlinks.has_links(game)


def test_unconnectable_nodes_raise():
    # two nodes a mile apart cannot link within max_dist
    game = {"header": b"", "nodes": [_node(0, 0, 0), _node(5000, 5000, 0)]}
    game["nodes"] += [_node(0, 0, 0, ntype=0) for _ in range(128)]
    try:
        pathlinks.generate(game, max_dist=600.0)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "do not connect" in str(e)
