# Demo: a converted box whose walls use a texture no PS3 zone has

The box of docs/demo-box.md with its four walls in `blockout_test_wood`, a PC Mod Tools
material whose colour map `~-gblockout_wood_test_c` occurs in none of the 178 PS3 zones on
this machine (disc, update and DLC; string search of every inflated zone for
`blockout_wood_test` and `blockout_test_wood`: 0 hits). The new material and its image are
stored in the map's own zone (docs/convert.md section 11); no pak is written and no stock file
changes. Everything below is offline: nothing was run on a console or an emulator.

## 1. Building the PC map

The box `.map` of box-map.md 1.1 with `us_art_wall_vinylsiding_white` replaced by
`blockout_test_wood` on the four inner wall faces (floor and ceiling unchanged), compiled under
the name `mp_opent5box_tex` with the three command lines of box-map.md 1.2 (`cod2map`,
`cod2rad -fast`, `linker_pc`, each through `launcher_ldr.exe` from a `.bat`, `cmd.exe /c` from
WSL). All three exit 0. The PC tools' image converter (`converter.exe`) was not used: it keeps
a conversion cache in the tools folder, which would change existing files; the texture is an
existing PC asset, and own art goes through `overrides` (section 4) instead.

PC zone `zone/English/mp_opent5box_tex.ff`: 8 assets (rawfile, com_map, techsets
`wc_l_sm_r0c0` and `wc_l_sm_r0c0n0s0x0`, gfx_map, game_map_mp, col_map_mp, rawfile), parses
exactly with the `PC` platform. The linker's asset info lists the new material and its two
images:

```
image,$identitynormalmap        |wc/blockout_test_wood|material|...
image,~-gblockout_wood_test_c   |wc/blockout_test_wood|material|...
material,wc/blockout_test_wood  |maps/mp/mp_opent5box_tex.d3dbsp|gfx_map|...
```

In the zone the material is inline in the GfxWorld's material memory (`ffffffff 70030000`:
inline, memory 880 = 44 x 16 + 6 x 8 + 128), with techset `wc_l_sm_r0c0`, two texture
definitions (normalMap `$identitynormalmap`, colorMap `~-gblockout_wood_test_c`) and seven
state bits. `~-gblockout_wood_test_c` is a 0x34-byte PC GfxImage `feffffff 03020301 ...
00020002 0100 ...` (2D, colour, category 3, 512 x 512) whose loadDef `00000000 44585431
00000000` says DXT1 with no pixels: they are `raw/images/~-gblockout_wood_test_c.iwi` (IWI 13,
174 824 bytes; it is in no `main/*.iwd`). `$identitynormalmap` carries its 4 pixel bytes
(1 x 1 A8R8G8B8).

## 2. Converting

Until `materials.py` is wired into `mapzone.convert_map`, the conversion runs from a scratch
copy of `mapzone.py` with exactly the calls of docs/convert.md 11.5, base the disc's
`mp_nuked.ff`, `--name mp_opent5box_tex`, default (baked) lighting, image folder the PC game
folder, shared zones code_post_gfx_mp and common_mp. 13 s.

| Material | Action |
|---|---|
| `wc/jun_art_concrete_base02` (floor) | reused: the base's PS3 material |
| `wc/pent_art_wall_creampaint02` (ceiling) | reused: the base's PS3 material |
| `wc/blockout_test_wood` (walls) | new PS3 material; techset `wc_l_sm_r0c0` (same name, a top-level techset of mp_nuked; reads colorMap only); state bits from the stock `wc/blockout_test_fabric01` (same techset); memory 912 = 44 x 16 + 6 x 8 + 160 |

| Image | Action |
|---|---|
| `$identitynormalmap` | reused: the base's placeholder `,$identitynormalmap` (alias `80060365`) |
| `~-gblockout_wood_test_c` | converted from the `.iwi`: DXT1 (0x86), 10 levels, 512 x 512, 174 848 bytes (174 776 + padding to 128) in the end-of-zone block |

The new PS3 material (0x80):

```
ffffffff 00000052 00040101 00000000 00001000 01000010 00100000 20000015   name, flags, sortKey, drawSurf, surface types
00ff01ff 02ffffff 03030303 ... 06ff02ff                                  stateBitsEntry[71] (template)
02 01 07 39 00 00 000000 | 8000396d ffffffff ffffffff ffffffff           counts, flags | techset alias, tables inline
```

Texture definitions `59d30d0f 6e700105 00000000 80060365` (normalMap -> placeholder) and
`a0ab1041 63701302 00000000 ffffffff` (colorMap, image inline). GfxImage
`860a0200 0001aae4 02000200 00010000 ... 03020301 0002ab00 02000200 0001 ... ffffffff ...
ffffffff 7fff012a`: DXT1, 10 levels, 512 x 512, mapType 3, semantic 2, category 3, deferred,
0x2ab00 bytes, name -1, the PC hash swapped.

## 3. Validation

| Check | Result |
|---|---|
| Content | 43 406 966 bytes; reparses exactly; `write(parse)` identical; 53 176 offset and alias pointers, 0 unresolved; 53 150 resolved through the base, 26 through the PC zone |
| Surfaces | surface 2 (walls) `809fa5e1` -> material memory element 182 (the new one, +0), `wc/blockout_test_wood`; surfaces 0 and 1 -> stock elements 104 and 140; materialMemoryCount 183 at GfxWorld +0x28c |
| Emulated loader (`tools/convert_map.py oracle`, t5mp.elf's XFile loader) | consumed exactly; blocks end at the header (RUNTIME 0x3d0e80, PHYSICAL_RUNTIME 0xa08200, VIRTUAL 0x1295c74, PHYSICAL 0xd0e0c8); 53 176 conversions, same fields and values as the product parser, none outside its block; 20 s |
| `opent5 verify ... --against mp_nuked` | container 886 chunks, 4 terminators; parse exact; rewrites identically (530 assets: the compass adds one) |
| Pixels | the deferred pixels read back from the converted zone equal the `.iwi` levels byte for byte (largest first) plus 72 bytes of padding; decoded to PNG and viewed: the grey 4 x 4 check of the blockout texture, mean RGB (93, 89.5, 85) |
| Determinism | two runs, same sha1 (`2c1f67cd...`) |

## 4. Own art without the PC converter

`convert_materials(..., overrides={"~-gblockout_wood_test_c": rgba})` draws a 256 x 256 RGBA
array (four coloured quadrants, white frame and diagonal) instead of the `.iwi`: DXT1, 9
levels, 43 776 bytes in the end-of-zone block, header keeping the PC image's mapType,
semantic, category and hash. Same checks: reparse exact, 0 unresolved, emulated loader
consumed exactly with the same 53 176 conversions; the image decoded from the converted zone
shows the quadrants, frame and diagonal.

## 5. Files created in the Steam game folder (all new, all named `mp_opent5box_tex`)

```
zone_source/mp_opent5box_tex.csv
raw/maps/mp/mp_opent5box_tex.d3dbsp
raw/maps/mp/mp_opent5box_tex.d3dpoly
raw/maps/mp/mp_opent5box_tex.d3dprt
raw/maps/mp/mp_opent5box_tex.radtrans
zone/English/mp_opent5box_tex.ff
zone_source/english/assetlist/mp_opent5box_tex.csv
zone_source/english/assetinfo/mp_opent5box_tex.csv
zone_source/english/assetinfo/mp_opent5box_tex_dep.txt
zone_source/english/assetinfo/mp_opent5box_tex_xmodel.csv
```

Outside it: `C:\o5\tex\` (the `.map` and three `.bat` files). Delete these to remove the
test map.

## 6. Not checked offline, open items

- Rendering: that the RSX techset draws the converted material as intended, and that the
  copied drawSurf (its materialSortedIndex comes from the PC linker's own order) is
  re-sorted at load time (INFERRED from 181 of 182 nuked drawSurfs being the PC bytes).
- Duplicate image names: an image the map converts under a name that a zone loaded at the
  same time also has (avoided here by the placeholder rule for code_post_gfx_mp and
  common_mp; other zones are not checked).
- Layered materials: secondary-layer samplerState 0x13 (PC) against 0x12 (PS3) is kept
  from PC; normal maps given as overrides are encoded as colour (no X-in-alpha layout).
- A techset only in another PS3 zone (not the base, not as a placeholder) is refused; copying
  a techset with its shaders between zones is not done.
- The PS3-only texturelist asset is left as it is (its meaning is INFERRED; the new image
  is not streamed).
