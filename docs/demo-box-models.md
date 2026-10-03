# Demo: the box with stock props (`mp_opent5box_props`)

The sealed box of docs/demo-box.md with eight stock props placed in Radiant as
`misc_model` entities, built with the PC Mod Tools and converted with the static model
rules of docs/convert.md section 12. Every prop is an XModel of PS3 mp_nuked, so its
materials, techsets and images (and their `.pak`) are already on the console.

Status: built and checked offline (parse, the game's own loader emulated, verify, previews).
Not run on a console or an emulator.

## 1. The map

`tools/testmap.py` box (half 512, height 256, no objective entities) plus:

| Model | Origin | Yaw |
|---|---|---|
| p_glo_sandbag | -240 320 0 | 0 |
| p_us_mailbox | -80 320 0 | 90 |
| mp_nuked_fence | 80 320 0 | 0 |
| p_glo_cardboardbox_4 | 240 320 0 | 30 |
| p_dest_trashcan_metal | -240 -320 0 | 0 |
| p_glo_potted_plant_01 | -80 -320 0 | 0 |
| p_jun_wood_stack | 80 -320 0 | 45 |
| p_glo_barricade_wood_barb | 240 -320 0 | 90 |

Each entity is `"classname" "misc_model"`, `"model"`, `"origin"`, `"angles" "0 yaw 0"`,
`"modelscale" "1"`. The PC tools found each model under the Mod Tools' `raw/xmodel/`.

## 2. Build (PC Mod Tools, from WSL)

`tools/testmap.py build mp_opent5box_props` with its map writer replaced by one that adds
the table above (a scratch script; the testmap tool has no prop option). cod2map, cod2rad
`-fast` and linker_pc each exit 0; the PC zone is 580 128 bytes, sha1 `be6e02c6...`. It
holds the 8 XModels and their techsets besides the world (23 assets), and parses exactly
with the `PC` platform (all seven blocks end at the header sizes): 8 draw instances,
8 instances, 8 clipMap static models.

Files the build created in the Steam game folder (all new, all named `mp_opent5box_props`;
delete them to remove):

```
raw/maps/mp/mp_opent5box_props.d3dbsp
raw/maps/mp/mp_opent5box_props.d3dpoly
raw/maps/mp/mp_opent5box_props.d3dprt
raw/maps/mp/mp_opent5box_props.radtrans
zone/English/mp_opent5box_props.ff
zone_source/mp_opent5box_props.csv
zone_source/english/assetinfo/mp_opent5box_props.csv
zone_source/english/assetinfo/mp_opent5box_props_dep.txt
zone_source/english/assetinfo/mp_opent5box_props_xmodel.csv
zone_source/english/assetlist/mp_opent5box_props.csv
```

The `.map` and the `.bat` files went to `C:\o5\smodels\` (outside the game).

## 3. Convert

Until the calls of docs/convert.md 12.2 are wired into `convert_map`, a scratch script
wraps `world.convert_gfx` (to `smodels.take` the PC arrays first) and `Splice.build` (to
convert, re-point the clipMap list and `smodels.place`), then runs
`convert_map(pc, mp_nuked, lighting="baked", name="mp_opent5box_props")`: 9.2 s, output
`mp_opent5box_props.ff`, 23 834 688 bytes, sha1 `1a6673e1...`.

`static_models` in convert.json: 8 instances, one of each model; the clipMap list names the
same 8 models. Pointers: 53 158 resolved through the base, 27 through the PC zone.

## 4. Offline checks

| Check | Result |
|---|---|
| reparse | exact; written back identically (`opent5 verify`: parse exact, rewrite from the parse identical) |
| game loader, emulated (`tools/convert_map.py oracle`) | consumed 43 248 463 of 43 248 463 bytes; every block ends at its header size; 53 185 pointers converted, the same fields and values as the product parser, none outside its block; 22.7 s |
| `opent5 verify --against mp_nuked` | ok; asset counts 529 vs 530 (the compass material the named map adds, section 10) |
| placement (`tools/dump_zone.py`, `world/static_models.json`) | the eight models at the origins and yaws above (cardboard box 30.02 degrees: the CMP axes carry about three digits), scale 1 |
| previews | the box with its ceiling removed and the props placed: two rows of four along y = +-320, each recognisable from above (sandbag, mailbox, fence edge-on, box turned 30 degrees; round trash can, plant, wood stack at 45 degrees, barricade at 90) |
| GUI | the world view shows the box (24 vertices, 12 triangles; static models are not drawn by that view); the eight models open in the model view from the same zone |

## 5. On a device: what to look for

Load `mp_opent5box_props` (the named map, docs/demo-box-named.md for how a named map is
reached) in tdm or dm. Expected: the box as before, with the props standing on the floor
in two rows along the north and south walls, lit like the room (INFERRED: from the light
grid at each instance's lightingOrigin and its groundLighting colour). Collision: the
clipMap list carries the 8 models, so players should not walk through them (INFERRED).

Possible failures and what they would mean:

| Seen | Likely cause |
|---|---|
| props missing, box fine | culling (smodelVisData sizes, cell aabb tree indexes from the PC map) or a draw instance field |
| props black | their lighting (lightingHandle, groundLighting, light grid) |
| props at wrong angles | the CMP axes (12 627 of 12 627 words match PS3 mp_nuked, so unlikely) |
| walking through props | the clipMap static model list or its model words |
| crash on load | report the point; the loader itself consumed the zone exactly offline |

## 6. Open items

- Props must be XModels of the base zone (docs/convert.md 12.5).
- PC GfxWorld RUNTIME sizes are INFERRED (docs/convert.md 12.3).
