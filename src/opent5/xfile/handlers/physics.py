"""physpreset (1), physconstraints (2), destructibledef (3).
docs/research/structs-map.md section 10.

PhysPreset (0x54): +0 name, +0x1c sndAliasPrefix (strings). PhysConstraints
(0xa88): +0 name, +4 count, +8 data[16] (PhysConstraint 0xa8: +0x14, +0x24
strings, +0x8c material ref). DestructibleDef (0x18): name, model, pristineModel,
numPieces, pieces (align 4, 0x138 each: 5 stages of 0x30, then the piece tail).
Loaders: PhysPreset Ptr 0x24baa0; PhysConstraints Ptr 0x24a498, struct 0x24a2c8;
DestructibleDef Ptr 0x254628, struct 0x254360.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

PHYS_CONSTRAINT_SIZE = 0xA8
PHYS_CONSTRAINT_COUNT = 16


@register
class PhysPresetHandler(Handler):
    asset_type = AssetType.PHYSPRESET
    header_size = 0x54

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        prefix = st.string(h, 0x1C)
        st.pop()
        return {"name": name, "snd_alias_prefix": prefix, "header": h.bytes()}


def read_phys_constraint(st: XStream, c: Chunk) -> dict:
    return {
        "target_bone1": st.string(c, 0x14),
        "target_bone2": st.string(c, 0x24),
        "material": asset_ref(st, c, 0x8C, AssetType.MATERIAL),
        "raw": c.bytes(),
    }


@register
class PhysConstraintsHandler(Handler):
    asset_type = AssetType.PHYSCONSTRAINTS
    header_size = 0xA88

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        data = [
            read_phys_constraint(st, h.sub(8 + PHYS_CONSTRAINT_SIZE * i, PHYS_CONSTRAINT_SIZE))
            for i in range(PHYS_CONSTRAINT_COUNT)
        ]
        st.pop()
        return {"name": name, "count": h.u32(4), "data": data}


DESTRUCTIBLE_PIECE_SIZE = 0x138
DESTRUCTIBLE_STAGE_SIZE = 0x30


def read_stage(st: XStream, s: Chunk) -> dict:
    return {
        "break_effect": asset_ref(st, s, 0x10, AssetType.FX),
        "break_sound": st.string(s, 0x14),
        "break_notify": st.string(s, 0x18),
        "loop_sound": st.string(s, 0x1C),
        "spawn_model": [asset_ref(st, s, 0x20 + 4 * k, AssetType.XMODEL) for k in range(3)],
        "phys_preset": asset_ref(st, s, 0x2C, AssetType.PHYSPRESET),
    }


def read_piece(st: XStream, p: Chunk) -> dict:
    stages = [
        read_stage(st, p.sub(DESTRUCTIBLE_STAGE_SIZE * s, DESTRUCTIBLE_STAGE_SIZE))
        for s in range(5)
    ]
    return {
        "stages": stages,
        "phys_constraints": asset_ref(st, p, 0x10C, AssetType.PHYSCONSTRAINTS),
        "damage_sound": st.string(p, 0x114),
        "burn_effect": asset_ref(st, p, 0x118, AssetType.FX),
        "burn_sound": st.string(p, 0x11C),
        "raw": p.bytes(),
    }


@register
class DestructibleDefHandler(Handler):
    asset_type = AssetType.DESTRUCTIBLEDEF
    header_size = 0x18

    def read(self, st: XStream, h: Chunk) -> dict:
        st.push(Block.VIRTUAL)
        name = st.string(h, 0)
        model = asset_ref(st, h, 4, AssetType.XMODEL)
        pristine = asset_ref(st, h, 8, AssetType.XMODEL)
        count = h.u32(0xC)
        table = array(st, h, 0x10, 3, DESTRUCTIBLE_PIECE_SIZE * count)
        pieces = None
        if table is not None:
            pieces = [read_piece(st, p) for p in table.items(DESTRUCTIBLE_PIECE_SIZE, count)]
        st.pop()
        return {
            "name": name,
            "model": model,
            "pristine_model": pristine,
            "num_pieces": count,
            "pieces": pieces,
            "client_only": h.u32(0x14),
        }
