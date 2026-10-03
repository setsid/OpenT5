# R4: Prior art and licences

What already exists for T5 (Call of Duty: Black Ops) zones and for the
neighbouring engines (T4, T6, IW3-IW5), what each piece covers, under what
terms, and what OpenT5 can take from it.

Sources were read on 2026-10-03. Local checkouts are cited by path; web
sources by non-personal URL or by project name and host. This is not legal
advice; the licence section states the general position and where the risk
lies.

## Summary

- Nothing public reads T5 **PS3** zones past the container. OpenAssetTools,
  the most complete T5 tool, is PC-only for T5 and not endian-aware. The PS3
  struct layer has to be built from the zone bytes and `t5mp.elf` whatever
  licence OpenT5 picks.
- Almost the whole ecosystem is GPL-3.0 (OpenAssetTools, Greyhound, Husky,
  C2M, zonetool, CoD-FF-Tools, CoD-Research). RPCS3 is GPL-2.0-only, which
  cannot be combined with GPL-3.0 code. PSL1GHT and envytools are MIT.
- Decision (recorded in section 4): OpenT5 is GPL-3.0-or-later
  (`~/opent5/LICENSE`, `pyproject.toml` `license = { text =
  "GPL-3.0-or-later" }`). OpenAssetTools code may be reused where it saves
  real work, headers kept and each reuse logged in `docs/provenance.md`.
  PS3 layouts still have to be verified from zone bytes and the ELF, because
  OAT describes the PC build only.

## 1. OpenAssetTools (OAT)

Upstream: OpenAssetTools on GitHub; docs at https://openassettools.dev.
Local checkout: `~/oat`, one commit (`ecfdab3988217a4039991572804f4ebe1b6df76f`,
2026-09-19), shallow history.

### 1.1 Licence

- `~/oat/LICENSE` is the verbatim GNU GPL version 3 text;
  `~/oat/README.md` line 109: "OAT source code is licensed under GPLv3".
  The GitHub API reports `GPL-3.0` for the upstream repository.
- No per-file headers grant anything different (checked
  `src/Common/Game/T5/T5_Assets.h`, `src/ZoneLoading/Game/T5/*.cpp`).
- Tree completeness: every first-party directory is present (`src/`,
  `docs/`, `test/`, `tools/`, `raw/`), but the git submodules under
  `thirdparty/` (catch2, eigen, json, libtomcrypt, libtommath, lz4, stb,
  webwindowed, zlib) are not initialised (`git submodule status` prints a
  leading `-` for each). It is complete for reading; it will not build
  without `git submodule update --init`.

### 1.2 T5 coverage

Platform: **PC only**. `src/ZoneLoading/Game/T5/ZoneLoaderFactoryT5.cpp`
`InspectZoneHeader` accepts only magic `IWffu100` (unsigned) with version 473
and hard-codes `GameEndianness::LE`, `GamePlatform::PC`. There is no Salsa20
key, no `PHEEBs71` auth header handling and no big-endian path for T5. The
upstream file at the current head is identical in those lines (fetched via
the GitHub API on 2026-10-03). Note that the version number alone does not
tell PC from PS3: OAT's `ZoneConstantsT5.h` has `ZONE_VERSION = 473`, the
same value OpenT5 reads (big-endian) from the PS3 `IWff0100` header.

Console status upstream (OpenAssetTools issue tracker):

- Issue 298 "Add Support to Fastfile for PS3 and Xbox 360" (open): the
  maintainers state OAT "is not currently endian aware" and that console
  structs "may be different than PC structs which means there will need to
  be zone code generation per game, per platform".
- Issue 906 "Multi-platform support" (open, 2026-07-15): proposes a refactor
  so consoles become separate targets; a contributor offers Xbox 360 work
  for T5 and T6. A public Xbox 360 fork of OAT exists on GitHub
  ("OpenAssetTools-xenon"); its `xenon` branch has no T5 console code as of
  2026-10-03 (its T5 loader is the same PC-only file).
- PR 826 (closed) added dumping of T6 **server** zones for PS3, Wii U and
  Xenon; per its description these are PC-format unsigned zones with a
  different version number. It does not touch T5.
- IW3, IW4 and T6 Xbox 360 zones can be dumped only as a raw data dump
  (`ZoneLoaderFactoryT6.cpp` line 373: "Dumping xbox assets is not
  supported, making a full fastfile data dump").

Asset support for T5 (from `~/oat/docs/SupportedAssetTypes.md`):

| Asset | Dump | Load (link from disk) |
|---|---|---|
| PhysPreset | yes | yes |
| PhysConstraints | yes | no |
| XAnimParts | yes | yes |
| XModel | yes (XMODEL_EXPORT/XMODEL_BIN, OBJ, GLB/GLTF) | yes |
| Material | yes | yes |
| MaterialTechniqueSet | yes (shader bytecode only) | no |
| GfxImage | yes ("a few special image encodings are not yet supported") | no |
| MapEnts | yes | no |
| GfxLightDef, Font_s, LocalizeEntry, WeaponVariantDef, RawFile, StringTable | yes | yes |
| DestructibleDef, SndBank, SndPatch, clipMap_t, ComWorld, GameWorldSp/Mp, GfxWorld, MenuList, menuDef_t, SndDriverGlobals, FxEffectDef, FxImpactTable, PackIndex, XGlobals, ddlRoot_t, Glasses, EmblemSet | no | no |

The **zone loader** can walk every T5 asset type (the zone code is generated
for all 32 types listed in `src/ZoneCode/Game/T5/T5_Commands.txt`); "dump"
above is about writing a usable file, not about parsing.

### 1.3 What the T5 zone code describes

- `src/Common/Game/T5/T5.h`: the `XAssetType` enum (43 values,
  `ASSET_TYPE_XMODELPIECES` .. `ASSET_TYPE_EMBLEMSET`) and a `SubAssetType`
  enum. This is the **PC** enum; see 3.2 for the PS3 difference.
- `src/Common/Game/T5/T5_Assets.h` (5420 lines): full PC struct
  definitions for every asset. D3D9 handles appear as commented `void*`
  (e.g. `GfxTexture` at line 1803 lists `IDirect3DTexture9*` etc.;
  `MaterialVertexShaderProgram` line 1174 holds `IDirect3DVertexShader9*`).
  `GfxImage` (line 1880) has no `CellGcmTexture`; the PS3 layout differs.
- `src/ZoneCode/Game/T5/T5_Commands.txt` plus
  `src/ZoneCode/Game/T5/XAssets/*.txt` (one per asset): a small DSL telling
  the generator which XFile block each pointer loads into, which fields are
  strings, array sizes (e.g. `GfxImageLoadDef.data` sized by `resourceSize`),
  `reorder:` directives that fix load order where it differs from
  declaration order, `reusable` pointers and `condition never` fields. This is
  effectively a description of T5's **load order** for the PC build.
- `src/ZoneLoading/Game/T5/ZoneLoaderFactoryT5.cpp`: the seven XFile blocks
  in order: TEMP, RUNTIME, LARGE_RUNTIME, PHYSICAL_RUNTIME, VIRTUAL, LARGE,
  PHYSICAL (agrees with the COD Engine Research BO1 page, 2.1).
- `src/ZoneCommon/Game/T5/ZoneConstantsT5.h`: `OFFSET_BLOCK_BIT_COUNT = 3`,
  `INSERT_BLOCK = XFILE_BLOCK_VIRTUAL` (how pointer markers encode block
  and offset).

Usefulness to OpenT5: high as a **cross-reference** for field names, the
asset/sub-asset vocabulary and the ordering rules, which are likely to be
shared by the PS3 build where the struct is platform-independent. Low as
code: it is C++, little-endian, PC-layout, and the PS3 structs that matter
most (images, shaders, vertex buffers, world geometry) differ. Every layout
taken from OAT must be re-verified against PS3 zone bytes in any case.

## 2. Other T5 / neighbouring zone tools

### 2.1 Documentation

**COD Engine Research wiki** - https://codresearch.dev (MediaWiki; licence of
content not stated on the main page; treat as reference only).
- "FastFiles and Zone files (BO1)":
  https://codresearch.dev/index.php/FastFiles_and_Zone_files_(BO1).
  Header `IWff0100`, `DB_AuthHeader` with `PHEEBs71`, 32-byte name, 256-byte
  signature; Salsa20 keys for PS3 (starts `0C 99 B3 DD`) and Xbox 360 (starts
  `1A C1 D1 2D`); "each block is first compressed using best zlib
  compression and encrypted using salsa20"; first block is the 0x28-byte
  XFile header, later blocks 0x7FC0 bytes; XFile header with seven block
  sizes. It does **not** describe the nonce chain. OpenT5's
  `src/opent5/container/fastfile.py` already cites this page for the key and
  verified it against real zones.
- "Category:Assets": https://codresearch.dev/index.php/Category:Assets.
  Gives the BO1 asset enum per platform. Key rows: `material` 0x06 on all;
  `pixelshader` 0x07 on Xbox 360 and PS3, absent on PC; `vertexshader` 0x08
  on PS3 only; `techset` 0x08 (Xbox 360) / 0x09 (PS3) / 0x07 (PC); `image`
  0x09 / 0x0A / 0x08; `sound` 0x0A / 0x0B / 0x09. So the PS3 enum is the PC
  enum with two shader types inserted after `material`: every PS3 id from
  `techset` onward is PC id + 2. INFERRED until checked against a PS3 zone's
  asset list (R1 owns that check).
- "Image Asset": https://codresearch.dev/index.php/Image_Asset. Has a
  Black Ops `GfxImage` with `#ifdef PS3 CellGcmTexture texture; // size =
  0x18` at the start, then size/height/width/depth, a `GfxTexture` union,
  `name`, `hash`; many fields are `unknown`. For MW2 onward it states
  streamed images live in PAK files; for CoD4/WaW, streamed data is "at the
  end of the zone file". Nothing specific on the T5 `.pak` format.
- "Pixel Shader Asset" / "Vertex Shader Asset":
  https://codresearch.dev/index.php/Pixel_Shader_Asset and
  .../Vertex_Shader_Asset. PS3 `MaterialPixelShader` is 0x18 bytes (MW2 and
  earlier) with RSX-side fields `patchTable`, `commandListOffset`,
  `commandDwordCount` and a reference to RSX method 0x408e4. The "Black Ops"
  entries show `cachedPart`/`physicalPart` with u16 sizes, which is the
  Xbox 360 shape (INFERRED from the field names; not the PS3 layout).
- "Technique Set Asset":
  https://codresearch.dev/index.php/Technique_Set_Asset. BO1
  `MAX_TECHNIQUES` 71 on PS3/Xbox, 130 on PC.
- "XAnim Asset": https://codresearch.dev/index.php/XAnim_Asset. BO1
  `XAnimParts` field list; no PS3 notes.
- No BO1 clipMap or GfxWorld page (only BO2/MW3/Ghosts variants exist).

**CoD-Research** (GitHub, GPL-3.0, "Wiki and research hub for the Call of
Duty series"). Its `Black-Ops-COD7/` folder holds Xbox 360 dvar/RPC notes and
a network auth write-up; nothing on zones. Not useful.

**"Black Ops Decryption"** - https://pastebin.com/QEkwa0Re. A C# routine
with no licence or author header. Uses the Xbox 360 key (starts `1A C1 D1 2D`),
checks `IWff` / `0100` / `PHEEBs71` and starts reading at 0x13C. Builds a
200 x 20-byte IV table from a 32-byte "feed" (each byte repeated 4 times),
keeps four block counters, takes an 8-byte IV per section, SHA-1s the
decrypted section and XORs the hash back into the table. This is the same
nonce chain OpenT5's `fastfile.py` documents for PS3, published for Xbox 360.
Reference only (no licence).

**OpenAssetTools docs** - https://openassettools.dev. Usage and component
guide; no T5 console material.

### 2.2 T5 fastfile viewers (rawfile level)

**Black Ops Fast File viewer** (local copy `~/ff-viewer`; a GitHub
repository holding a decompiled copy of a closed 2011-era tool). PS3 and
Xbox 360. The C# in
`~/ff-viewer/Black Ops Fast File viewer Source Code/FF/Form1.cs` is
decompiler output (dnSpy token comments) of a WinForms shell; all format work
is in a closed native `FF32.dll` reached by P/Invoke
(`Viewer_LoadFastFile`, `Viewer_GetDecompressedData`, `Viewer_SetData`,
`Viewer_Save`, `Keys_SetKey(int Platform)`, declared at lines 761-810). It lists and
edits rawfiles (GSC/CSC/CFG) and loads/saves `.zone` "block files"; it
cannot sign. No licence; the README says the original was unpacked without
permission. **Not reusable** as code or text.
- Caution: its `README.md` lists a "PS3" Salsa20 key starting `46 D3 F9 97`.
  That is **not** the T5 PS3 key (COD Engine Research and OpenT5's verified
  key start `0C 99 B3 DD`). Its "Xbox 360" key matches the wiki's Xbox 360
  key. Where `46D3...` comes from is unknown (INFERRED: another game or
  build); do not use it.

**FastForge** (forum release on se7ensins, date not confirmed; thread
"COD BO1 (XBOX 360) FastForge"). Described as a from-scratch BO1 Xbox 360
.ff editor that decrypts, inflates, edits GSC and rebuilds. Source and
licence not found; forum page returned 403 to automated fetch. Xbox 360 is
big-endian, Treyarch-built and shares the nonce chain, so it is the nearest
living relative of OpenT5's container code; reference only.

**ffManager, Tom FastFile Extractor, BlackOpsFFLibrary.NET** (2011-2013 forum
releases on se7ensins / NextGenUpdate / itsmods). Rawfile extract/inject for
BO1 console zones. Closed or of unclear licence. Historical reference only.

### 2.3 Neighbouring console zone tools

**CoD-FF-Tools** (GitHub, GPL-3.0, C#). Parses, edits and rebuilds CoD4,
WaW (T4) and MW2 zones on PS3, Xbox 360, PC and Wii. Black Ops is **not**
supported. Relevant because T4 PS3 is Treyarch's previous PS3 zone format:
- `docs/SupportedFormats.md`: console zones use 64 KB raw-deflate blocks
  with a 2-byte length prefix (unencrypted pre-T5); console asset enums
  carry `pixelshader`/`vertexshader` types that PC lacks; WaW PS3 zone
  header has 7 block sizes, Wii 8.
- `Call of Duty FastFile Editor/ZoneParsers/ImageParser.cs`: PS3 `GfxImage`
  read backwards from the name; a `CellGcmTexture` format byte (0x87 for
  DXT3) sits 48 bytes before the name; dimensions are big-endian u16.
- Parsers for XAnimParts, Material, TechSet, clipMap (view only) on T4/IW3
  PS3, in `Call of Duty FastFile Editor/Models/` and `ZoneParsers/`.
Usefulness: good **reference** for how Treyarch's PS3 structs looked one
game earlier; GPL-3.0, so its code can be ported under OpenT5's licence.

**zonetool** (GitHub organisation "ZoneTool", GPL-3.0, C++). PC fastfile
linker/dumper for IW4/IW5 running inside the game process. No T5, no console.
Reference only for the general "dump to raw files, rebuild from zone_source
CSV" workflow. A 64-bit successor ("x64-zt", GPL-3.0) covers later IW titles,
not T5.

**atian-cod-tools** (GitHub, licence not detected by GitHub). Fastfile
dumpers for Black Ops 3 and later. Not relevant to T5.

### 2.4 PC in-memory exporters (T5 PC, not zones)

| Tool | What | T5 | Licence | Reuse |
|---|---|---|---|---|
| Greyhound (GitHub; fork of Wraith Archon) | Reads assets from a running PC game's memory: xmodels, xanims, images, effects | Black Ops PC | GPL-3.0 | Reference for PC struct offsets and xanim decoding; PC memory layout, not zone layout |
| Wraith Archon (original) | Same, closed freeware | Black Ops PC | Closed | None |
| Lime | In-game xmodel exporter (OBJ/MA/XE) | BO1 PC | Closed | None |
| Husky (GitHub) | BSP (map geometry) extractor from memory | Black Ops PC (INFERRED from its game list) | GPL-3.0 | Reference for GfxWorld vertex/index layout on PC |
| C2M (GitHub) | Map exporter | various (T5 INFERRED) | GPL-3.0 | Reference only |

None of these read zone files or PS3 data.

### 2.5 Treyarch's Black Ops Mod Tools (PC)

Shipped on Steam as "Call of Duty: Black Ops - Mod Tools" (PC only). Its
components and their files (INFERRED from community guides cited below and
from the general IW-engine tool pipeline; confirm by inspecting an install):

- Radiant (level editor): `.map` sources; compiled by `cod2map`/`cod2rad`
  into `.d3dbsp`.
- Asset Manager: edits `.gdt` asset databases (text) that describe
  materials, weapons, models, anims.
- Converter: turns `xmodel_export`/`xanim_export` text (from Maya/max
  exporters) and source images into binary `xmodel`/`xanim` and `.iwi`
  images under `raw/`.
- `linker_pc.exe`: reads `zone_source/*.csv` and `raw/` and writes **PC**
  `zone/*.ff` (unsigned `IWffu100`). It does not write PS3 zones.

**LinkerMod** (GitHub, no licence file, so all rights reserved). A large set
of patches and helpers for these tools: `components/linker_pc`,
`asset_util`, `asset_viewer`, `cod2map`, `cod2rad`, `D3DBSP_Lib`, `radiant_mod`
etc. Reference only.

Community guides: "A Little Introduction To Modding In Call Of Duty: Black
Ops" (Steam Community guide id 2494448328); Neoseeker "Call of Duty: Black
Ops - Mod Tools" tutorial thread. `pc-route.md` covers the
PC-to-PS3 conversion question.

### 2.6 PS3 platform references (not CoD-specific)

| Project | URL | Licence | What it gives OpenT5 |
|---|---|---|---|
| RPCS3 | https://github.com/RPCS3/rpcs3 | GPL-2.0-only ("Most files", per its README) | RSX vertex/fragment program decoders: `rpcs3/Emu/RSX/Program/RSXVertexProgram.h`, `RSXFragmentProgram.h`, `VertexProgramDecompiler.cpp`, `FragmentProgramDecompiler.cpp`, `CgBinaryProgram.h`; GCM texture formats and swizzle in `rpcs3/Emu/RSX/`. Reference only: GPL-2.0-only code cannot be combined with GPL-3.0 OpenT5. |
| PSL1GHT | https://github.com/ps3dev/PSL1GHT | MIT | Open PS3 SDK: `ppu/include/rsx/gcm_sys.h` (CellGcm-style texture struct and format constants), `nv40.h`, `rsx_program.h`, `tools/cgcomp`. MIT, so constants and small definitions can be reused with attribution. |
| envytools | https://github.com/envytools/envytools | MIT | NV40-family (RSX is NV47-derived) register and shader ISA documentation. Reference. |

## 3. PS3-specific items requested

### 3.1 GfxImage and `.pak` streaming
- Only partial public material: the COD Engine Research Black Ops
  `GfxImage` with a leading 0x18-byte `CellGcmTexture` on PS3 (2.1), and
  CoD-FF-Tools' T4/IW3 PS3 parser showing the GCM format byte (2.3).
- No public description of T5 PS3 `.pak` image packs was found. The wiki's
  "data will be found in PAK files" note is for MW2/MW3/Ghosts/AW.
  T5 `.pak` format must come from the files on disc and from the ELF
  (textures.md).
- OAT's T5 `GfxImage`/`GfxImageLoadDef` (`T5_Assets.h` line 1880,
  `XAssets/GfxImage.txt`) shows the PC fields `streaming`, `delayLoadPixels`,
  `loadDef` with `resourceSize`; useful names, PC layout.

### 3.2 Techsets and shaders (RSX programs)
- PS3 has separate `pixelshader` and `vertexshader` asset types in the BO1
  enum (2.1); PC has none (OAT `T5.h` uses sub-assets instead). INFERRED
  until confirmed in a PS3 zone.
- PS3 `MaterialPixelShader` RSX fields (`patchTable`, `commandListOffset`,
  `commandDwordCount`) are documented for MW2 and earlier only.
- To decode RSX microcode, RPCS3 (reference only) and envytools/PSL1GHT (MIT)
  are the available sources. Nothing public decodes T5 PS3 techsets.

### 3.3 XAnim on PS3
- No PS3-specific XAnim material found. COD Engine Research gives the BO1
  `XAnimParts` field list (platform-neutral); OAT has a PC XAnim dumper
  (`src/ObjWriting`, T5 support listed in 1.2) and Greyhound decodes PC
  in-memory anims. Endianness and any PS3 packing differences must be
  checked against zone bytes.

### 3.4 clipMap / GfxWorld
- Nothing public for T5 on any platform: OAT cannot dump either for T5;
  COD Engine Research has no BO1 pages for them. Husky handles PC
  in-memory GfxWorld only.

### 3.5 Nonce / IV chain
- Not on the COD Engine Research BO1 page.
- Published for Xbox 360 in the "Black Ops Decryption" paste (2.1); the same
  scheme (200 x 20-byte table seeded from the 32-byte zone name, per-stream
  block index, SHA-1 of plaintext XORed back) is already implemented and
  verified on PS3 zones in `src/opent5/container/fastfile.py`.
- The closed `FF32.dll` behind the Black Ops Fast File viewer must also
  implement it (it reads PS3 zones), but its code is not available.

## 4. Licence: the decision and its consequences

### 4.1 The decision

OpenT5 is licensed GPL-3.0-or-later. `~/opent5/LICENSE` holds the licence
text and `~/opent5/pyproject.toml` line 10 reads
`license = { text = "GPL-3.0-or-later" }`. OpenAssetTools code may be reused
where it saves real work.

### 4.2 What reuse this allows

- **OpenAssetTools** (GPL-3.0): code, the `T5_Assets.h` struct definitions,
  the `ZoneCode/Game/T5` command files and the generator's output may be
  ported or translated into OpenT5. Most likely candidates: the object
  writers (XMODEL_EXPORT/XMODEL_BIN, OBJ, GLTF in `src/ObjWriting`), the
  XAnim and material dumpers, image conversion (`src/ObjImage`), and field
  names and load order from `src/ZoneCode/Game/T5/`.
- **Other GPL-3.0 projects** under the same terms: Greyhound, Husky, C2M,
  zonetool, CoD-FF-Tools (its T4/IW3 PS3 parsers), CoD-Research.
- **MIT/BSD**: PSL1GHT constants (`gcm_sys.h`, `nv40.h`), envytools material,
  numpy. Keep their copyright notices.
- **Still not allowed:**
  - RPCS3 (GPL-2.0-only for most files): cannot be combined with GPL-3.0
    code. Read it, then write OpenT5's own decoder.
  - The Black Ops Fast File viewer and `FF32.dll` (no licence, decompiled
    from a closed tool), LinkerMod (no licence file), the "Black Ops
    Decryption" paste (no licence), Wraith Archon, Lime, FastForge (closed or
    unknown). Facts learned from them, such as an algorithm or a constant,
    can be used. Their text cannot.
  - Game material: no `.ff`, `.pak`, ELF bytes or extracted assets go into
    the repository (shared rules).

### 4.3 Obligations that follow

1. **Keep headers and notices.** A ported OAT file or function keeps the
   OAT copyright/licence notice, adapted to a Python comment, and states that
   it was modified, with the date (GPL-3.0 section 5a). OAT files mostly
   carry no per-file header, so the notice is "derived from OpenAssetTools,
   <path>, GPL-3.0" plus the upstream commit.
2. **Log each reuse** in `~/opent5/docs/provenance.md`: OpenT5 file and
   symbol, source project and path, upstream commit (the local checkout is
   `ecfdab3988217a4039991572804f4ebe1b6df76f`), what was taken, and what was
   changed. This record is what shows where any given line came from.
3. **Source for binaries.** Anything distributed as a binary, including the
   PyInstaller executable, must come with the complete corresponding source
   (GPL-3.0 section 6): the OpenT5 source at the exact tag the exe was built
   from, the build spec and scripts, and the source or exact versions of the
   bundled Python and libraries. Do this by publishing the exe next to the
   tagged source on the same release page, or by shipping a written offer.
   The exe also bundles numpy (BSD) and the Python runtime (PSF), so their
   licence texts go in the bundle as well (a `licenses/` folder).
4. **GPL notice in the GUI.** An interactive program must show an
   appropriate legal notice (GPL-3.0 section 0, "Appropriate Legal
   Notices"): an About box naming `opent5.APP_NAME`, the copyright line,
   "licensed under GPL-3.0-or-later, with NO WARRANTY", and where to get the
   source. The CLI `--version` output should say the same in one line.
5. **No additional restrictions.** OpenT5 cannot add terms that forbid
   commercial use or redistribution of the GPL parts.
6. **Verification is still needed.** The licence makes OAT's text usable but
   not correct for PS3. Every struct taken from OAT must be checked against
   PS3 zone bytes (offset + hex) or the ELF before it is relied on, and
   marked INFERRED until it is.

### 4.4 Most useful items, in order
1. COD Engine Research BO1 pages (container, per-platform asset enum,
   Black Ops PS3 `GfxImage` with `CellGcmTexture`).
2. OpenAssetTools T5 (`T5.h`, `T5_Assets.h`, `ZoneCode/Game/T5/`,
   `src/ObjWriting`): names, load order and exporters, now portable.
3. CoD-FF-Tools: the closest working PS3 zone parser (T4/IW3), for PS3
   `GfxImage` and techset shapes one generation earlier; GPL-3.0, portable.
4. PSL1GHT `gcm_sys.h` (MIT) for GCM texture formats; RPCS3 RSX program
   code as reference only for shader decoding.
5. The "Black Ops Decryption" paste, as independent confirmation of the
   nonce chain on Xbox 360.
