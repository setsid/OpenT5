# Sun and fog in mp_nuked: static zone data or script-driven?

Status: probe for v0.3.0. Covers where the sun and the fog come from in the retail PS3
mp_nuked zone (`nuked/d_pak/mp_nuked.ff`, sha1 6d5a4a0e), what the editor can change by
editing the zone, and what would only change by editing the map script. Evidence is from
`tools/stage_sun.py` and reads of the zone's own assets through `opent5.edit.document`.

## Summary

| Question | Answer |
|---|---|
| Where is the sun's direction and colour held? | Twice as static zone data: the ComWorld primary light at `sunPrimaryLightIndex` (index 1, type 1), and the GfxWorld embedded `sun_light` (GfxLight). Both held the same values. |
| Can the editor change the sun? | Yes, as static-data field edits (`Document.set_field`): the primary light's and the gfx sun light's `dir`, `color`, `diffuseColor`, `specularColor`. `tools/stage_sun.py` does this and saves through save-with-verify. |
| Will a sun edit show on device? | For the real-time sun only: sun-shadow direction and the sun's diffuse/specular colour on dynamic entities (players, dynamic models, viewmodel). The baked lightmaps on static world surfaces do NOT change, so static geometry keeps its original shade. |
| Is the sun overridden by GSC at runtime? | No. No `.gsc`/`.csc` in the zone calls setSunLight or writes the sun direction/colour. `sun/mp_nuked.sun` sets only the lens-flare sprite (sprite, flare, blind, glare, fx position), not the lit scene. |
| Where is the fog held? | Not in the zone's render data. It is set by GSC: the createart tweakfile (`maps/mp/createart/mp_nuked_art.gsc`) issues `scr_fog_*` dvars and one `setVolFog(...)` call. |
| Can the editor change the fog? | Only by editing the map script rawfile text (`Document.set_text` on the `.gsc`), not by editing the gfx_map. There is no fog field on any zone struct. |

## 1. The sun

### 1.1 What holds it

The sun's direction and colour are static zone data, held in two places with identical
values:

- **ComWorld primary light, index 1** (`com_map` asset; `ComPrimaryLight`, 0xDC bytes).
  `sunPrimaryLightIndex` in the GfxWorld header is 1, and `primary_lights[1]` is the only
  type-1 (directional) light:
  - `dir` = (-0.6027, -0.5240, 0.6018)
  - `color` = (14.0, 13.72, 12.46), near white
  - `diffuseColor` / `specularColor` the same RGB.
- **GfxWorld embedded `sun_light`** (`gfx_map` asset; GfxLight, loaded at header +0x100).
  Same `dir`; `diffuseColor` / `specularColor` = (13.88, 13.26, 10.65).

The GfxWorld header field `sunColorFromBsp` is (0,0,0) and `sunLight` (the pointer at
+0xEC/236) is -1 (inline), so the embedded `sun_light` node is the gfx-side sun, not a
separate pointed-to light.

The direction follows the engine convention `forward.x = cos(pitch)cos(yaw)`,
`forward.y = cos(pitch)sin(yaw)`, `forward.z = -sin(pitch)`: the retail worldspawn
`"sundirection" "-37 221 0"` reproduces the stored `dir` exactly.

### 1.2 Worldspawn keys are compile inputs, not runtime fields

The worldspawn (first entity in the clipMap entity string) carries:

```
"sundirection" "-37 221 0"
"suncolor" "0.996078 0.976471 0.886275"
"sunlight" "14"
"sunshadowintensity" "7"
"_sunshadowcolor" "0.709804 0.803922 0.984314"
"ambientintensity" "0.1"
"_ambientcolor" "0.803922 0.815686 0.988235"
"newsun" "1"
```

These are inputs the linker/light compiler reads to bake the lightmaps and to compute the
primary light and the gfx sun light. They are not read as live values at runtime, and the
baked result is already in the lightmap images. Editing the worldspawn text in the zone
would therefore not re-light anything without a recompile.

### 1.3 What a sun edit changes on device

Editing the primary light and the gfx sun light (as `tools/stage_sun.py` does) changes the
**real-time** sun only:

- the direction of sun shadows cast by dynamic entities (players, dynamic models), and the
  sun-shadow-map direction;
- the sun diffuse and specular colour on dynamic entities and the viewmodel.

It does **not** change the baked lightmaps, so static world surfaces keep their original
brightness and colour. This is the honest limit of a static-data sun edit without a
lightmap recompile: the scene's real-time sun swings and warms, the baked floor and walls
do not.

### 1.4 Not overridden by GSC

No script in the zone sets the sun lighting. Searched every `.gsc`/`.csc` rawfile: there is
no `setSunLight`, no sun direction/colour write. `sun/mp_nuked.sun` is sprite-only:

```
r_sunsprite_shader "sun_flare"
r_sunflare_shader "sun_flare"
r_sunsprite_size "16"
r_sun_fx_position "-54.353 221 0"
r_sunflare_min_size "148" ... r_sunglare_fadeout "0.5"
```

So the static sun edit is not undone at runtime.

## 2. The fog

### 2.1 Fog is script state, not zone render data

There is no fog field on the GfxWorld or any other zone struct that the renderer reads
directly. Fog is engine state set by GSC. The only fog source in the zone is the createart
tweakfile `maps/mp/createart/mp_nuked_art.gsc`, which in `main()` sets the baseline
exponential-fog dvars:

```
setdvar("scr_fog_exp_halfplane", "3759.28");
setdvar("scr_fog_exp_halfheight", "243.735");
setdvar("scr_fog_nearplane", "601.593");
setdvar("scr_fog_red", "0.806694");
setdvar("scr_fog_green", "0.962521");
setdvar("scr_fog_blue", "0.9624");
setdvar("scr_fog_baseheight", "-475.268");
```

and then a volumetric-fog layer:

```
start_dist = 874.792; half_dist = 2740; half_height = 364.404; base_height = 125.986;
fog_r = 0.776471; fog_g = 0.588235; fog_b = 0.47451; fog_scale = 5.68252;
sun_col_r = 0.803922; ... sun_dir_x = -0.432962; sun_dir_y = -0.395847; sun_dir_z = 0.809845;
setVolFog(start_dist, half_dist, half_height, base_height, fog_r, fog_g, fog_b, fog_scale,
          sun_col_r, sun_col_g, sun_col_b, sun_dir_x, sun_dir_y, sun_dir_z, sun_start_ang,
          sun_stop_ang, time, max_fog_opacity);
```

No `setExpFog` call appears anywhere in the zone. Fog is therefore script-driven at
runtime, held as dvars and the volumetric-fog layer, not as a baked or static field.

### 2.2 What this means for the editor

The editor could change the fog only by editing the map script rawfile text, not the
gfx_map. The `.gsc` rawfiles are editable as text (`Document.set_text`), so changing the
`scr_fog_*` values or the `setVolFog(...)` arguments in `mp_nuked_art.gsc` is the route. An
edit takes effect only if that script runs at runtime in the shipped build.

Caveat on that last point: `mp_nuked.gsc main()` calls `mp_nuked_fx`, `_load::main()` and
`mp_nuked_amb`, not createart directly; createart sets `level.tweakfile = true` and reads
as a developer tweakfile. Whether the retail build executes createart at runtime (directly
or via `_load`) is not settled from the `.ff` alone and would need the `_load` source or a
device check. Either way, fog is script state, so it is not a gfx_map edit.

## 3. Bearing on the editor

- A sun editor would be a static-data field edit on the ComWorld primary light and the
  GfxWorld sun light. It is honest to show it changes the real-time sun (shadows and
  dynamic-entity colour) and not the baked lightmaps.
- A fog editor would have to edit the map script text and depends on that script running at
  runtime. It is not a gfx_map or zone-struct edit, and is out of scope for a geometry or
  lighting editor that works on zone render data.
