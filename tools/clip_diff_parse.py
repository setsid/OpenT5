"""Parse two compiled PC .ff files and diff their col_map_mp clipMaps, to read off
exactly what cod2map/linker do to the clipMap when clip brushes are added. The two
maps are identical but for N worldspawn clip boxes (+ their misc_models). Scratch
for the collision compiler-diff R&D; PC little-endian; not product code.
"""
from __future__ import annotations
import struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from opent5.convert.pc import read_pc_fastfile, parse_pc  # noqa: E402
from opent5.xfile.constants import AssetType  # noqa: E402

CB = 0x60  # cbrush_t
CLEAF = 0x2C
CLBN = 0x14  # cLeafBrushNode_s
ARRAYS = ["planes","static_model_list","materials","brushsides","nodes","leafs",
          "leafbrush_nodes","leafbrushes","leafsurfaces","verts","brush_verts",
          "uinds","tri_indices","borders","partitions","aabb_trees","cmodels","brushes"]

def clipmap_node(path: str) -> dict:
    content = read_pc_fastfile(Path(path).read_bytes())
    x = parse_pc(content, log=True)
    for a in x.assets:
        if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP):
            return a.data
    raise SystemExit(f"{path}: no col_map_mp")

def U32(b,o): return struct.unpack_from("<I", b, o)[0]
def S32(b,o): return struct.unpack_from("<i", b, o)[0]
def U16(b,o): return struct.unpack_from("<H", b, o)[0]
def S16(b,o): return struct.unpack_from("<h", b, o)[0]
def F32(b,o): return struct.unpack_from("<f", b, o)[0]
def V3(b,o):  return tuple(round(v,1) for v in struct.unpack_from("<3f", b, o))

def brush(node, i):
    b = node["brushes"]; o=i*CB
    return dict(i=i, mins=V3(b,o), maxs=V3(b,o+0x10), contents=U32(b,o+0xC),
                numsides=U32(b,o+0x1C), numverts=U32(b,o+0x54),
                cflags=[S32(b,o+0x24+4*k) for k in range(6)])

def leaf(node, i):
    b=node["leafs"]; o=i*CLEAF
    return dict(i=i, firstCollAabb=U16(b,o), collAabbCount=U16(b,o+2),
                brushContents=U32(b,o+4), terrainContents=U32(b,o+8),
                mins=V3(b,o+12), maxs=V3(b,o+24), leafBrushNode=S32(b,o+36),
                cluster=S16(b,o+40))

def lbn(node, i):
    b=node["leafbrush_nodes"]
    # leafbrush_nodes may be a list of dicts (handler) or raw bytes
    if isinstance(b, list):
        raw=b[i]["raw"] if isinstance(b[i],dict) else b[i]
    else:
        raw=b[i*CLBN:(i+1)*CLBN]
    axis=raw[0]; count=S16(raw,2); contents=S32(raw,4); data=U32(raw,8)
    return dict(i=i, axis=axis, count=count, contents=contents, data=data,
                dist=F32(raw,8), range=F32(raw,0xC),
                child=[U16(raw,0x10),U16(raw,0x12)])

def lbn_count(node):
    b=node["leafbrush_nodes"]
    return len(b) if isinstance(b,list) else len(b)//CLBN

def main():
    a_path=sys.argv[1]; b_path=sys.argv[2]
    A=clipmap_node(a_path); B=clipmap_node(b_path)
    print("=== array byte sizes (base -> props) ===")
    for k in ARRAYS:
        av=A.get(k); bv=B.get(k)
        asz=(len(av) if not isinstance(av,list) else len(av)) if av is not None else None
        bsz=(len(bv) if not isinstance(bv,list) else len(bv)) if bv is not None else None
        unit={"brushes":CB,"leafs":CLEAF,"brush_verts":12,"leafbrushes":2,"planes":0x14,
              "brushsides":0xC,"nodes":8,"static_model_list":80,"leafbrush_nodes":CLBN}.get(k)
        def n(sz): return "" if sz is None or not unit else f"({sz//unit} elems)"
        flag="   <== CHANGED" if asz!=bsz else ""
        print(f"  {k:20s} {str(asz):>8} {n(asz):>12}  ->  {str(bsz):>8} {n(bsz):>12}{flag}")

    na=U16(A["header"],148); nb=U16(B["header"],148)
    print(f"\n=== numBrushes {na} -> {nb}  (+{nb-na}) ===")
    print("new brushes (contents/mins/maxs/numsides/numverts):")
    for i in range(na, nb):
        br=brush(B,i)
        print(f"  brush {i}: contents={br['contents']:#010x} numsides={br['numsides']} "
              f"numverts={br['numverts']} {br['mins']}..{br['maxs']} cflags[0]={br['cflags'][0]:#x}")

    # which leaves now reference the new brushes, and how (via the kd-tree)
    nlbn_a=lbn_count(A); nlbn_b=lbn_count(B)
    print(f"\n=== leafbrushNodes {nlbn_a} -> {nlbn_b} (+{nlbn_b-nlbn_a}) ===")
    for i in range(nlbn_a, min(nlbn_b, nlbn_a+16)):
        n=lbn(B,i)
        print(f"  lbn {i}: axis={n['axis']} count={n['count']} contents={n['contents']:#010x} "
              f"data={n['data']:#010x} dist={n['dist']:.1f} child={n['child']}")

    nlb_a=U32(A["header"],64); nlb_b=U32(B["header"],64)
    print(f"\n=== numLeafBrushes (pool) {nlb_a} -> {nlb_b} (+{nlb_b-nlb_a}) ===")
    poolB=B["leafbrushes"]
    tail=[U16(poolB,2*k) for k in range(nlb_a, nlb_b)]
    print(f"  appended pool entries (brush indices): {tail}")

    # leaves: find which changed brushContents or leafBrushNode
    nleaf_a=U32(A["header"],48); nleaf_b=U32(B["header"],48)
    print(f"\n=== numLeafs {nleaf_a} -> {nleaf_b} ===")
    changed=[]
    for i in range(min(nleaf_a,nleaf_b)):
        la=leaf(A,i); lb=leaf(B,i)
        if la["leafBrushNode"]!=lb["leafBrushNode"] or la["brushContents"]!=lb["brushContents"]:
            changed.append((i,la,lb))
    print(f"  leaves with changed leafBrushNode/brushContents: {len(changed)}")
    for i,la,lb in changed[:16]:
        print(f"  leaf {i}: leafBrushNode {la['leafBrushNode']}->{lb['leafBrushNode']}  "
              f"brushContents {la['brushContents']:#x}->{lb['brushContents']:#x}  "
              f"mins{lb['mins']} maxs{lb['maxs']}")

    # new leaves (if any)
    if nleaf_b>nleaf_a:
        print(f"  NEW leaves {nleaf_a}..{nleaf_b-1}:")
        for i in range(nleaf_a,nleaf_b):
            lb=leaf(B,i)
            print(f"    leaf {i}: leafBrushNode={lb['leafBrushNode']} brushContents={lb['brushContents']:#x} "
                  f"{lb['mins']}..{lb['maxs']} cluster={lb['cluster']}")

    # nodes/planes/brushsides deltas summary already in array table
    npl_a=S32(A["header"],8); npl_b=S32(B["header"],8)
    nbs_a=U32(A["header"],32); nbs_b=U32(B["header"],32)
    nnode_a=U32(A["header"],40); nnode_b=U32(B["header"],40)
    print(f"\n=== planeCount {npl_a}->{npl_b} (+{npl_b-npl_a})  numBrushSides {nbs_a}->{nbs_b} (+{nbs_b-nbs_a})  numNodes {nnode_a}->{nnode_b} (+{nnode_b-nnode_a}) ===")

if __name__=="__main__":
    main()
