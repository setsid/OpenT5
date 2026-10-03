# Provenance

What OpenT5 takes from elsewhere, and under what terms. Every entry names the
source project, the path inside it, what was taken, and where it lives here.
Copied code keeps its original attribution and licence header intact.

## Licence

OpenT5 is GPL-3.0-or-later. That allows code to be adapted from
OpenAssetTools, which is GPL-3.0.

## Sources

| OpenT5 file | Source project | Source path | What was taken | Changes |
|---|---|---|---|---|
| docs/research/structs-content.md | OpenAssetTools @ ecfdab39 | src/Common/Game/T5/T5_Assets.h | Struct and field names for content asset types (layouts and offsets were measured from t5mp.elf and zone bytes) | Names only; PS3 offsets differ and are the document's own |
| docs/research/structs-map.md | OpenAssetTools @ ecfdab39 | src/Common/Game/T5/T5_Assets.h | Field names for GfxWorld, ComWorld and other map types where PS3 data gives no name (sizes, order and offsets measured from t5mp.elf and zone bytes) | Names only |
| src/opent5/export/ (material sampler names) | OpenAssetTools @ ecfdab39 | src/Common/Utils/Djb2.h, src/Common/Game/T5/CommonT5.h | The sampler-name hash algorithm (case-insensitive djb2 variant) and the sampler names it is applied to | Reimplemented in Python; confirmed against stored hashes in mp_nuked (docs/extract.md section 5) |
| src/opent5/export/ (path nodes), docs/extract.md | OpenAssetTools @ ecfdab39 | src/Common/Game/T5/T5_Assets.h | pathnode_constant_t field names and order | Names only; offsets confirmed against the loader and mp_nuked data |

## Derived from game data, not copied

Struct layouts, offsets and enum values derived from the game executable and
the zones themselves are documented with their evidence in docs/research/.
They are facts about the format rather than copied expression, and are listed
here only when a value was first taken from another project and then
confirmed.
