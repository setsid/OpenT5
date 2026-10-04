"""Stage p_clip_move: MOVE an existing stock axial player-clip to the central road
crossing (the p_clip_a spot, 204,-47), keeping numBrushes unchanged. Diagnostic for
the collision R&D (docs/research/clipmap-add-brush-recipe.md). Local CPU only.
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
OUT = Path("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/p_clip_move/mp_nuked.ff")

def clipmap_asset(xf):
    return [a for a in xf.assets if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP)][0]

def header_counts(node):
    h = node["header"]
    return dict(
        numBrushes=struct.unpack_from(">H", h, 148)[0],
        numStaticModels=struct.unpack_from(">I", h, 16)[0],
        numBrushVerts=struct.unpack_from(">I", h, 88)[0],
    )

data = Path(PR).read_bytes()
content = bytes(Zone.open(data).content) if data[:8] == b"IWff0100" else data

rw = Rewrite(content)
asset = clipmap_asset(rw.xfile)
node = asset.data
cm = pc.ClipMap(node, rewrite=rw)
loc = pc.BspLocator.from_xfile(rw.xfile, node)

before = header_counts(node)
print("before:", before)

# choose an existing stock axial player-clip to move (brush 82, the canonical one
# the recipe compares to; a small perimeter clip whose removal is negligible).
MOVE = 82
assert cm.brush(MOVE).numsides == 0 and cm.brush(MOVE).contents == pc.PLAYER_CLIP_CONTENTS
print("moving brush", MOVE, "from", cm.brush(MOVE).mins, cm.brush(MOVE).maxs)

# new bounds: a wall across the central road at (204,-47): thin in X (travel axis),
# wide in Y (road width), tall in Z (covers crouch/stand; floor ~ -24 near centre).
mins = (180.0, -147.0, -48.0)
maxs = (228.0,  53.0, 112.0)
pc.move_clip_bsp(cm, loc, MOVE, mins, maxs)

# -- visible marker: MOVE an existing central collidable prop to the clip wall --
# render follows origin (Route-B); its own baked collision does not, which is fine
# because collision here is the moved clip. All float edits, no count change.
PROP = 28                      # a crate-sized central static model
TARGET = (204.0, -47.0, -20.0)  # on the road floor at the clip
sml = node["static_model_list"]           # raw bytes, 0x50 per cStaticModel_s
o = PROP * 0x50
old_origin = struct.unpack_from(">3f", sml, o + 8)
delta = tuple(TARGET[k] - old_origin[k] for k in range(3))

gfx = [a for a in rw.xfile.assets if a.type == AssetType.GFX_MAP][0].data
di = gfx["smodel_draw_insts"]              # list of {"raw": 0x2c}; origin at +0x4
insts = gfx["smodel_insts"]                # raw bytes, 0x28 each; mins@0 maxs@0xC
# match the draw inst by origin (unique float match)
j = None
for k in range(len(di)):
    raw = di[k]["raw"] if isinstance(di[k], dict) else di[k]
    org = struct.unpack_from(">3f", raw, 4)
    if all(abs(org[t] - old_origin[t]) < 0.5 for t in range(3)):
        j = k; break
if j is None:
    raise SystemExit(f"no GfxWorld draw inst matches prop {PROP} origin {old_origin}")
print(f"prop {PROP} old origin {tuple(round(v,1) for v in old_origin)} -> {TARGET}; gfx draw inst {j}")

# shift clipMap cStaticModel origin, absmin, absmax by delta
smlb = bytearray(sml)
for off in (8, 0x38, 0x44):
    v = struct.unpack_from(">3f", smlb, o + off)
    struct.pack_into(">3f", smlb, o + off, *(v[k] + delta[k] for k in range(3)))
node["static_model_list"] = bytes(smlb)
# shift gfx draw inst origin
draw = bytearray(di[j]["raw"] if isinstance(di[j], dict) else di[j])
org = struct.unpack_from(">3f", draw, 4)
struct.pack_into(">3f", draw, 4, *(org[k] + delta[k] for k in range(3)))
di[j]["raw"] = bytes(draw)
# shift the parallel smodel inst bounds (mins @0, maxs @0xC)
instsb = bytearray(insts)
io = j * 0x28
for off in (0, 0xC):
    v = struct.unpack_from(">3f", instsb, io + off)
    struct.pack_into(">3f", instsb, io + off, *(v[k] + delta[k] for k in range(3)))
gfx["smodel_insts"] = bytes(instsb)

res = rw.build(check=True)

# repack the edited content into a PS3 .ff container (as mapzone does).
zone = Zone.open(PR)
zone.content[:] = res.content
built = zone.build(derive_size=True)
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_bytes(built.data)
sha = hashlib.sha1(built.data).hexdigest()
print("wrote", OUT, len(built.data), "sha1", sha, "(content", len(res.content), "bytes)")

# container self-check and reparse the packed file
from opent5.container.zone import verify as ff_verify
v = ff_verify(built.data, expected=res.content)
print("container verify: chunks", v.chunks, "content_bytes", v.content_bytes, "terminators", v.terminators)
back = parse(bytes(Zone.open(OUT).content))
bnode = clipmap_asset(back).data
after = header_counts(bnode)
print("after :", after)
print("numBrushes unchanged:", before["numBrushes"] == after["numBrushes"])
print("numStaticModels unchanged:", before["numStaticModels"] == after["numStaticModels"])
print("numBrushVerts unchanged:", before["numBrushVerts"] == after["numBrushVerts"])

bcm = pc.ClipMap(bnode)
bloc = pc.BspLocator.from_xfile(back, bnode)
centre = tuple((mins[k] + maxs[k]) / 2 for k in range(3))
leaf = bloc.locate(centre)
print(f"\nmoved clip centre {centre} -> leaf {leaf}; brush {MOVE} reachable there? {MOVE in bcm.reachable_brushes(leaf)}")
print("moved brush bounds in reparsed file:", bcm.brush(MOVE).mins, bcm.brush(MOVE).maxs,
      "contents", hex(bcm.brush(MOVE).contents & 0xffffffff), "numsides", bcm.brush(MOVE).numsides)
# structural checks
print("check_world_leaf_refs:", len(pc.check_world_leaf_refs(bcm)))
print("check_leaf_contents_masks:", len(pc.check_leaf_contents_masks(bcm)))
