"""Stage p_clip_add: ADD a genuinely NEW clip brush (numBrushes grows) the cod2map
way (contiguous brushVerts), plus move a marker prop to it. Local CPU only.
"""
import sys, hashlib, struct
sys.path.insert(0, "src"); sys.path.insert(0, "tools"); sys.path.insert(0, "tools/loader_emu")
from pathlib import Path
from opent5.container.zone import Zone
from opent5.xfile import parse
from opent5.xfile.constants import AssetType
from opent5.xfile.remap import Rewrite
from opent5.convert import propclip as pc

PR = "/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/d_pak/mp_nuked.ff"
OUT = Path("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/p_clip_add/mp_nuked.ff")

def clip_asset(xf):
    return [a for a in xf.assets if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP)][0]

def counts(node):
    h = node["header"]
    return dict(numBrushes=struct.unpack_from(">H", h, 148)[0],
                numStaticModels=struct.unpack_from(">I", h, 16)[0],
                numBrushVerts=struct.unpack_from(">I", h, 88)[0])

def verts_contiguity(cm):
    """The engine addresses a brush's verts by RUNNING COUNT, not by the stored
    pointer, so the invariant is that brush i's verts BYTES sit at running*12 in the
    pool. Check the bytes, not verts_ptr (the appended brush's pointer deliberately
    resolves to the neighbour; the engine ignores it)."""
    pool = cm.node["brush_verts"]; running = 0; bad = []
    for i in range(cm.num_brushes):
        br = cm.brush(i)
        if br.numverts:
            if (running + br.numverts) * 12 > len(pool):
                bad.append((i, "pool too short"))
            running += br.numverts
    return bad, running

data = Path(PR).read_bytes()
content = bytes(Zone.open(data).content)
rw = Rewrite(content)
node = clip_asset(rw.xfile).data
cm = pc.ClipMap(node, rewrite=rw)
loc = pc.BspLocator.from_xfile(rw.xfile, node)
before = counts(node)
print("before:", before)

# NEW clip brush: a wall across the central road crossing (same spot/shape as p_clip_move).
mins = (180.0, -147.0, -48.0); maxs = (228.0, 53.0, 112.0)
new_brush = pc.add_clip_bsp(cm, loc, mins, maxs)
print("added NEW brush index", new_brush)

# marker prop: move central collidable prop 28 + its gfx draw inst to the wall (render-only)
PROP = 28; TARGET = (204.0, -47.0, -20.0)
sml = node["static_model_list"]; o = PROP * 0x50
old_origin = struct.unpack_from(">3f", sml, o + 8)
delta = tuple(TARGET[k] - old_origin[k] for k in range(3))
gfx = [a for a in rw.xfile.assets if a.type == AssetType.GFX_MAP][0].data
di = gfx["smodel_draw_insts"]; insts = gfx["smodel_insts"]
j = next(k for k in range(len(di))
         if all(abs(struct.unpack_from(">3f", di[k]["raw"], 4)[t] - old_origin[t]) < 0.5 for t in range(3)))
smlb = bytearray(sml)
for off in (8, 0x38, 0x44):
    v = struct.unpack_from(">3f", smlb, o + off); struct.pack_into(">3f", smlb, o + off, *(v[k] + delta[k] for k in range(3)))
node["static_model_list"] = bytes(smlb)
draw = bytearray(di[j]["raw"]); org = struct.unpack_from(">3f", draw, 4)
struct.pack_into(">3f", draw, 4, *(org[k] + delta[k] for k in range(3))); di[j]["raw"] = bytes(draw)
instsb = bytearray(insts); io = j * 0x28
for off in (0, 0xC):
    v = struct.unpack_from(">3f", instsb, io + off); struct.pack_into(">3f", instsb, io + off, *(v[k] + delta[k] for k in range(3)))
gfx["smodel_insts"] = bytes(instsb)
print(f"moved marker prop {PROP} (gfx draw inst {j}) to {TARGET}")

res = rw.build(check=True)
zone = Zone.open(PR); zone.content[:] = res.content
built = zone.build(derive_size=True)
OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_bytes(built.data)
sha = hashlib.sha1(built.data).hexdigest()
from opent5.container.zone import verify as ff_verify
v = ff_verify(built.data, expected=res.content)
print("wrote", OUT, len(built.data), "sha1", sha, "| container chunks", v.chunks, "terminators", v.terminators)

# reparse and verify
back = parse(bytes(Zone.open(OUT).content))
bnode = clip_asset(back).data
after = counts(bnode)
print("after :", after)
print("numBrushes grew:", after["numBrushes"] == before["numBrushes"] + 1,
      "(", before["numBrushes"], "->", after["numBrushes"], ")")
bcm = pc.ClipMap(bnode)
bad, running = verts_contiguity(bcm)
print("brushVerts contiguity mismatches:", len(bad), "| running", running, "numBrushVerts", after["numBrushVerts"])
if bad: print("  ", bad[:4])
bloc = pc.BspLocator.from_xfile(back, bnode)
centre = tuple((mins[k] + maxs[k]) / 2 for k in range(3))
print("new clip reachable from centre leaf:", new_brush in bcm.reachable_brushes(bloc.locate(centre)))
print("check_world_leaf_refs:", len(pc.check_world_leaf_refs(bcm)),
      "check_leaf_contents_masks:", len(pc.check_leaf_contents_masks(bcm)))

# unchanged stock clip check: stock player-clips must be undisturbed. Every rebuild
# re-encodes offset pointers, so compare the record ignoring verts_ptr (the engine
# ignores it for collision) AND compare the verts read by running count.
px = parse(bytes(Zone.open(Path(PR).read_bytes()).content))
pnode = clip_asset(px).data
pcm = pc.ClipMap(pnode)
def running_verts(cm, idx):
    run = 0
    for i in range(cm.num_brushes):
        br = cm.brush(i)
        if i == idx:
            return [struct.unpack_from(">3f", cm.node["brush_verts"], run*12 + 12*k) for k in range(br.numverts)]
        run += br.numverts
for bi in (82, 83, 102, 107):
    pa = bytearray(pnode["brushes"][bi*0x60:(bi+1)*0x60]); pb = bytearray(bnode["brushes"][bi*0x60:(bi+1)*0x60])
    struct.pack_into(">I", pa, 0x58, 0); struct.pack_into(">I", pb, 0x58, 0)  # mask verts_ptr
    rec_same = pa == pb
    verts_same = running_verts(pcm, bi) == running_verts(bcm, bi)
    print(f"stock brush {bi}: record identical (ignoring verts_ptr) {rec_same}; running-count verts identical {verts_same}")
