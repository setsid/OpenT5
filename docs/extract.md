# Extracting a zone to open formats

`tools/dump_zone.py` writes every asset of one zone to a folder, headless, in formats other
tools read: PNG, Wavefront OBJ/MTL, JSON, CSV and plain text. Code: `src/opent5/export/`.
Tests: `tests/test_export_formats.py` (synthetic), `tests/test_export_zones.py` (mp_nuked
counts, skipped without `.env`).

    .venv/bin/python tools/dump_zone.py mp_nuked               # -> out/extract/mp_nuked/
    .venv/bin/python tools/dump_zone.py mp_firingrange --out /tmp/fr
    .venv/bin/python tools/dump_zone.py path/to/zone.ff --no-images --no-previews
    .venv/bin/python tools/dump_zone.py --zone-file inflated.zone --name mp_nuked

The zone and its `.pak` files are only read. `out/` is git-ignored; nothing extracted belongs
in the repository. mp_nuked takes about three minutes (most of it reading streamed images
from the paks and rendering the previews).

Conventions below: "zone offset" is an offset in the inflated zone stream (36-byte prefix
included), as in `docs/research/`. Every layout claim gives the mp_nuked bytes it was read
from. Coordinates are written as the game stores them: inches, Z up.

## 1. Output

| Path | What |
|---|---|
| `manifest.json` | every asset -> the files written for it, counts, failures with the reason, timings |
| `images/<name>.png` | each GfxImage, level 0; cube maps as `_px _nx _py _ny _pz _nz`; volumes as one image with the slices stacked; normal maps rebuilt to RGB (section 5) |
| `materials/<name>.json` | technique set, texture slots (slot name, image name, PNG path, sampler state, semantic), constants, state bits |
| `techsets/<name>.json`, `shaders/*.vs.bin`, `*.ps.bin`, `shaders/index.json` | techniques, passes (vertex declaration, shader names, arguments) and the raw RSX programs (`VS0u` / `PS0u` blobs) |
| `world/<map>.obj` + `.mtl` | every GfxWorld surface, one OBJ group per surface, `usemtl` = the material; the MTL points `map_Kd`, `map_bump`, `map_Ks` at the exported PNGs of the colour, normal and specular slots |
| `world/static_models.json` | every static model: model name, origin, angles (pitch, yaw, roll), axes, scale, cull distance |
| `world/<map>_static_models.obj` + `.mtl` | every static model's LOD0 placed in the world |
| `world/surfaces.json`, `world/gfx_map.json` | per-surface ranges and flags; counts, sky, lightmaps, probes, bounds |
| `collision/<map>_brushes.obj` | every clipMap brush as convex faces, one group per brush named with its contents bits |
| `collision/<map>_triangles.obj` | the clipMap collision triangles (terrain and patches) |
| `collision/collision.json` | counts, brushes by contents, collision materials, static models, submodels, dynamic entities |
| `map_ents/<map>.ents`, `map_ents/entities.json` | the entity string as text, and parsed (one dict per entity, with `origin_vec` / `angles_vec`) |
| `game_map/paths.json` | path nodes: type, script strings, origin, angle, links |
| `com_map/lights.json`, `lightdefs/*.json` | primary lights (type, colour, direction, origin, radius, cone, light def) |
| `xmodels/<model>/` | `<model>.obj` + `.mtl` (LOD0), `bones.json`, `model.json` (LODs, bounds, surfaces, materials) |
| `rawfiles/...`, `stringtables/*.csv`, `localize/localize.json` | scripts inflated, tables as CSV, strings |
| `<type>/<name>.json` | everything else (fx, sound, xanim, physpreset, destructibledef, glasses, weapon, menus ...), generically: nested assets become `{"asset", "name"}`, alias links `{"ref", "name"}`, byte arrays over 4 KiB go to `<name>.blobs/*.bin` |
| `previews/*.png` | renders of the world, world plus static models, brushes and collision triangles, from above and at an angle (section 8) |

Names are made safe for file systems and OBJ (`/ \ : * ~` and spaces become `_`); two names
that collide get `~2`, `~3`. The manifest keeps the real names. An asset whose name starts
with `,` is a reference to an asset defined in another zone: the zone holds only a stub, and
the manifest marks it `"reference": true`.

## 2. Finding assets and resolving references

A zone lists only its top-level assets (mp_nuked: 529). Most images, materials, techsets,
shaders and models are loaded inline inside other assets, so `export.nodes.AssetIndex` walks
the parsed data and classifies each node by its header size and keys (image 0x70, material
0x80, xmodel 0xf8, techset 292, vertex shader 16, pixel shader 12). mp_nuked: 1036 images,
585 materials, 293 models, 104 techsets, 234 vertex and 980 pixel shaders.

Asset references by alias (`AssetLink`) name a block position that holds a pointer to the
asset, and the parse leaves them unresolved. `AliasResolver` rebuilds the map from the event
log: for every reference field loaded inline (-1 or -2), the log has the POINTER event of the
field, PUSH TEMP, the INSERT of the alias slot (for -2) and the READ of the asset's header in
TEMP; the asset's name is the first STRING after the header (or the offset-pointed string when
the name field is not -1). The field's own block position, and the slot, then both name that
asset. Example: GfxWorld surface 0 (zone 0x1dec3e3) has material `81307cf1` at +0x40, i.e.
VIRTUAL + 0x1307cf0, which is the +0x0 field of a `materialMemory` entry that loaded
`*33n_69n(wc/jun_art_stone_stucco_white01:wc/rus_art_window_metal02_trans)` inline. Result on
mp_nuked: all 3494 surface materials and all 4209 static model references resolve
(`tests/test_export_zones.py::test_alias_references_resolve`).

Offset pointers to data loaded earlier (shared vertex buffers, brush sides, planes) are read
through `MemoryMap`, built from the READ, STRING and TAIL events: block position -> file
offset.

## 3. Vertex formats

### 3.1 World (GfxWorld)

`draw.vd.vertices` (+0x20c, count +0x208) is 16 bytes per vertex: float x, y, z and a float of
+-1. Vertex 0, zone 0x102e4f9: `c40c6000 c5114000 43280000 3f800000` = (-561.5, -2324, 168), 1.

`draw.vld.data` (+0x218, PHYSICAL) holds the other attributes, in one run per vertex group.
A GfxSurface (0x60 bytes) gives: +0x0 mins, +0xc the group's byte offset in the layer data,
+0x10 maxs, +0x1c the group's first vertex, +0x20 the surface's first vertex within the
group, +0x24 vertexCount u16, +0x26 triCount u16, +0x28 baseIndex, +0x40 material, +0x44
lightmap index. Indices (u16) are relative to the group's first vertex. Surfaces 0 and 1
(zone 0x1dec3e3, 0x1dec443):

    c40ca000 c5119000 43050000 00000000   mins; layer offset 0
    c40c2000 c5111000 43290000 00000000   maxs; group first vertex 0
    00000000 00060004 00000000 481c4000   first in group 0; 6 verts, 4 tris; baseIndex 0
    ...
    c40ca000 c5063000 43050000 00000000   surface 1: layer offset 0
    c40c2000 c5059000 43510000 00000000   group first vertex 0
    00000006 00050003 0000000c 481c4000   first in group 6; 5 verts, 3 tris; baseIndex 12

Surface 2 starts the next group: layer offset 0x1b8 = 440, first vertex 11, so the first
group's stride is 440 / 11 = 40 bytes. Over mp_nuked's 243 groups the stride (run length /
vertex count, always whole) is 28 (165 groups), 36 (28), 40 (25), 48 (10), 44 (8), 52 (6),
56 (1). The first 28 bytes are the same in every stride; the rest are per-layer extras (second
UV sets; INFERRED from the values, not used). Layer data at zone 0x12df279:

    ffffffff 3e300000 3f380000 3db76000 3ec44000 000003ff 00200800 | 3d8ba400 3f3e1e1e ff8080ff
    colour   u 0.172  v 0.719  lmap u   lmap v   normal   tangent  | extra (stride 40)

Colour is R, G, B, A bytes. Normal and tangent are CMP, the RSX's packed 11:11:10 signed
normalised vector: x bits 0..10, y 11..21, z 22..31 (`000003ff` = +X, the normal of surface 0,
which is a 2-inch-thick wall facing X; `00200800` = -Y).

Check: for all 117181 world triangles the averaged vertex normal points opposite to the
triangle's (v1 - v0) x (v2 - v0); the game's front faces are clockwise. The OBJ swaps the
last two indices of every triangle so that OBJ's counter-clockwise front faces agree with the
normals.

### 3.2 Models (XSurface, 0x5c bytes)

`flags & 7` selects the vertex stream format (structs-map.md section 9 has the sizes the
loader reads). Measured from data:

| Format | verts0 (+0x1c) | stream (+0x24) |
|---|---|---|
| `flags & 1 == 0` (0, 2, 4, 6) | 16: float x, y, z, binormal sign | 0, 2: normal, tangent, half u, v, colour (16); 4, 6: none |
| 1 | 8: packed position (below) | normal, tangent, half u, v, colour (16) |
| 5 | 8: packed position | normal, tangent, half u, v (12) |
| 3 | 16: packed position, normal, tangent | half u, v, colour (8) |
| 7 | 16: packed position, normal, tangent | half u, v (4) |

Packed position: four s16 (x, y, z, binormal sign +-32767); position = offset + s16 x 2^e /
32768, with the offset the three floats at surface +0x48 and e the per-axis bytes at +0x55,
+0x56, +0x57. Example `mp_nuked_fence` surface 0, zone 0x57a87, tail
`... 00000000 35080000 41ffee4d 4b060606`: offset (0, 5.1e-7, 31.99), e = 6, so the scale is
1/512. Its first vertex (verts0 at zone 0x57bf7) `39ff 0000 3fff 7fff` -> (29.0, 0, 63.99).
Over the surface the s16 range is x +-16896, y +-512, z +-16384, i.e. x +-33, y +-1, z 0 .. 64,
the model's bounds (XModel +0xb4 mins `c203ff84 bf7fc0f7 bc0597f8`, +0xc0 maxs `4203ff84
3f7fc108 427ff6a6` at zone 0x5795d + 0xb0 = (-33, -1, -0.008) .. (33, 1, 64)). Stream
`001ff800 7fc00000 b51c3000` = normal (0, 1, 0) (the fence is a plane facing Y), tangent
(0, 0, 1), u, v = (-0.32, 0.125) as half floats. The meaning of the byte at +0x54 (0x4b in
every surface seen) is unknown.

Checks: every LOD0 surface of the 289 non-reference models resolves and indexes inside its
vertices; the school bus decodes inside its XModel bounds; the rendered bus, police car,
ceiling fan, bookcase and a skinned character are recognisable and upright. Positions of
skinned and multi-bone rigid surfaces are model space (the bind pose); no bone transform is
applied.

LOD table: XModel +0x28, four entries of 0x1c bytes: +0 distance f32, +4 surface count u16,
+6 first surface u16. `mp_nuked_fence`: (1000, 1, 0), (2000, 1, 1), (4000, 1, 2), (8000, 1, 3).

### 3.3 Static model placement

GfxStaticModelDrawInst is 0x2c bytes on PS3: +0x0 cull distance, +0x4 origin (3 x f32), +0x10
three axes as CMP words (forward, left, up), +0x1c scale, +0x20 model (alias), +0x24 flags.
First instance, zone 0x1e3e223:

    45fa0000 c4184666 c3570000 c2766666   cull 8000; origin (-609.1, -215.0, -61.6)
    00355bc4 001e2155 7fc00000 3f800000   axes (0.94, 0.33, 0), (-0.33, 0.94, 0), (0, 0, 1); scale 1
    80003045 00000000 00000101            model -> p_jun_wood_stack

The axes carry about three significant digits; the exporter re-orthonormalises them and gives
angles as the game's (pitch, yaw, roll). World position = origin + scale x (p . axes).

## 4. Materials and texture slots

Each material texture definition carries a 32-bit hash of its sampler name. The hash is
`R_HashString`: start 0, then for each byte `h = (h * 33) ^ (c | 0x20)` (OpenAssetTools,
`src/Common/Utils/Djb2.h` and `src/Common/Game/T5/CommonT5.h`). It reproduces the stored values
and their first/last characters (+4, +5 of the definition): `colorMap` 0xa0ab1041 (469 uses in
mp_nuked), `normalMap` 0x59d30d0f (400), `specularMap` 0x34ecccb3 (378), `colorMap1` ..
`colorMap3`, `normalMap1/2`, `specularMap1/2`, `detailMap`, and `Detail_Map`, `Normal_Map`,
`Specular_Map` (the hash ignores case; the capital comes from the stored first character).
Hashes with no known name keep the hash and the two characters (mp_nuked: D..r, R..p, R..k,
F..k, R..e, plus seven single uses).

## 5. Images

Decoding is `opent5.formats.texture` (textures.md). The exporter adds:

- streamed images: the largest part whose pak is present is decoded (pak slot table in
  textures.md 2.4; slot 0 is `<zone>.pak` beside the zone, then the update and disc folders
  from `.env`); when larger parts are missing the manifest notes the size exported;
- normal maps (semantic 5, DXT5/DXT3): X is in alpha and Y in green; the PNG is an ordinary
  RGB tangent-space normal map with Z = sqrt(1 - x^2 - y^2);
- cube maps as six faces in the order +X -X +Y -Y +Z -Z (INFERRED, textures.md 4);
- X32_FLOAT as grey (value x 255, clamped).

mp_nuked: 1009 of 1036 images decode (724 from `mp_nuked.pak`, 175 from `ui_mp.pak`, 101
deferred in the zone, 5 from `images_low.pak`, 4 from `common.pak`); every streamed image
found its largest part. mp_firingrange: 1176 of 1211 (the other 35 are `,` references). The 27 that do not are all `,`
references to images in other zones (for example `,$white`, `,$identitynormalmap`,
`,fxt_smk_def_1`): the struct here has no pixels. A sample of 30 decoded at random was checked
by eye (colour maps, rebuilt normal maps, channel-packed specular maps, a desert vista).

## 6. Collision (clipMap)

Brushes (cbrush_t 0x60): +0x0 mins, +0xc contents, +0x10 maxs, +0x1c side count, +0x20 offset
pointer to the brush's cbrushside_t run (12 bytes: +0 plane pointer, +4, +8 flags), each plane
a cplane_s (normal, dist; 20 bytes, in the GfxWorld plane array). A brush is the intersection
of its six axial planes (from mins / maxs) and its sides; each face is a large square on its
plane clipped by all the others, kept when its area is over 0.01 square inches. mp_nuked:
5890 brushes, every one with faces. Triangles: `verts` (12 bytes) indexed by `triIndices`
(3 x u16): 4644 vertices, 7492 triangles.

## 7. Paths, lights, entities

Path nodes are `pathnode_t` (0x80) with the PC `pathnode_constant_t` layout (OpenAssetTools
`T5_Assets.h`): +0 type, +4 spawnflags, +6 .. +0xe script strings (targetname,
script_linkname, script_noteworthy, target, animscript), +0x14 origin, +0x20 angle, +0x2c
radius, +0x3e link count, +0x40 links (12 bytes: +0 distance, +4 node). The +0x3e/+0x40 pair
is the one the loader uses (structs-map.md 6). mp_nuked node 0: pathnode at (1748, 384, -40)
with four links; all 316 origins lie inside the map.

ComPrimaryLight is 220 bytes, the PC layout (type, shadow flag, exponent, priority, cull
distance, colour, direction, origin, radius, cone cosines, ..., +0xd8 light def name).
mp_nuked light 2: type 2 at (-817.5, -344, 109), radius 120, `white_light`.

The entity string is written as is, and parsed as `{ "key" "value" ... }` blocks: 1106
entities in mp_nuked (first `worldspawn`, then spawn points such as `mp_tdm_spawn` at
`-1633.8 1192.2 -24`).

## 8. Checking by eye

`export.preview.render` is a small numpy rasteriser (flat shading, z-buffer, orthographic):
top view and a view from above the south-west. The view is cropped to the playable area (spawn
points and path nodes, padded 1500 inches), since the world also holds distant scenery and
the sky. mp_nuked's previews show the cul-de-sac at the north end of the street, the bus in
it, the two houses with their garages and yards either side of the street, fences, and the
rocks and trees around the edge; the collision triangles show the same street and
cul-de-sac. Brush previews show solid brushes only (contents bit 0) and leave out any wider
than three quarters of the play area: the clipMap also holds sky, clip and other volumes
(for example a 4096 x 5632 inch box with contents 0x10000000) that would cover everything
from above.

## 9. Open items

- Rigid multi-bone and skinned model positions are written in model space; per-bone vertex
  lists (`XRigidVertList`) and blend weights are not applied (not needed for a static pose).
- Techniques shared by offset pointer are listed as the raw pointer, not by name.
- GfxImage +0x6c hash, XSurface +0x54 byte, the per-layer extras of world vertices, and slot
  2 of the pak table are not decoded.
- Streamed cube or volume images are reported, not decoded (none in mp_nuked or
  mp_firingrange).
