import sys, struct
sys.path.insert(0, "src"); sys.path.insert(0, "tools"); sys.path.insert(0, "tools/loader_emu")
from pathlib import Path
from opent5.container.zone import Zone
from opent5.xfile import parse
from opent5.xfile.constants import AssetType
from opent5.convert.propclip import ClipMap

def load(p):
    data = Path(p).read_bytes()
    content = bytes(Zone.open(data).content) if data[:8] == b"IWff0100" else data
    x = parse(content, log=True)
    for a in x.assets:
        if a.type in (AssetType.COL_MAP_MP, AssetType.COL_MAP_SP):
            return x, a

def contiguity(p, label):
    x, a = load(p)
    cm = ClipMap(a.data)
    base = cm._brush_verts_base()
    running = 0
    bad = []
    for i in range(cm.num_brushes):
        br = cm.brush(i)
        if br.numverts and br.verts_ptr:
            inner = ((br.verts_ptr - 1) & 0x1fffffff) - base
            if inner != running * 12:
                bad.append((i, inner, running * 12, br.numverts))
            running += br.numverts
    nbv = struct.unpack_from(">I", a.data["header"], 88)[0]
    print(f"{label}: numBrushes {cm.num_brushes}, numBrushVerts {nbv}, running verts {running}, "
          f"contiguity mismatches {len(bad)}")
    for b in bad[:4]:
        print(f"    brush {b[0]}: verts_ptr inner {b[1]} vs running-count offset {b[2]} (numverts {b[3]})")
    return bad

contiguity("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/d_pak/mp_nuked.ff", "pristine (stock)")
contiguity("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/p_clip_move/mp_nuked.ff", "p_clip_move (SOLID on device)")
contiguity("/mnt/c/Users/bolst/Desktop/opent5-hwtest/nuked/p_clip_a/mp_nuked.ff", "p_clip_a (WALK-THROUGH on device)")
