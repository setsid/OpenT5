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

## Derived from game data, not copied

Struct layouts, offsets and enum values derived from the game executable and
the zones themselves are documented with their evidence in docs/research/.
They are facts about the format rather than copied expression, and are listed
here only when a value was first taken from another project and then
confirmed.
