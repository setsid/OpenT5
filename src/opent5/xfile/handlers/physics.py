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
from opent5.xfile.handlers.base import Handler, asset_ref, items, register
from opent5.xfile.stream import Chunk, XStream

PHYS_CONSTRAINT_SIZE = 0xA8
PHYS_CONSTRAINT_COUNT = 16


@register
class PhysPresetHandler(Handler):
    asset_type = AssetType.PHYSPRESET
    header_size = 0x54

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        io.string(h, 0x1C, node, "snd_alias_prefix")
        io.pop()


def phys_constraint(io: XStream, c: Chunk, node: dict) -> None:
    """PhysConstraint (0xa8): strings +0x14, +0x24, material +0x8c."""
    io.string(c, 0x14, node, "target_bone1")
    io.string(c, 0x24, node, "target_bone2")
    asset_ref(io, c, 0x8C, AssetType.MATERIAL, node, "material")


@register
class PhysConstraintsHandler(Handler):
    """Node: "header", "name", "data" (16 constraint nodes, sliced from the header)."""

    asset_type = AssetType.PHYSCONSTRAINTS
    header_size = 0xA88

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        data = node.setdefault("data", {})
        for i in range(PHYS_CONSTRAINT_COUNT):
            c = h.sub(8 + PHYS_CONSTRAINT_SIZE * i, PHYS_CONSTRAINT_SIZE)
            phys_constraint(io, c, data.setdefault(i, {}))
        io.pop()


DESTRUCTIBLE_PIECE_SIZE = 0x138
DESTRUCTIBLE_STAGE_SIZE = 0x30


def stage(io: XStream, s: Chunk, node: dict) -> None:
    asset_ref(io, s, 0x10, AssetType.FX, node, "break_effect")
    io.string(s, 0x14, node, "break_sound")
    io.string(s, 0x18, node, "break_notify")
    io.string(s, 0x1C, node, "loop_sound")
    for k in range(3):
        asset_ref(io, s, 0x20 + 4 * k, AssetType.XMODEL, node, f"spawn_model{k}")
    asset_ref(io, s, 0x2C, AssetType.PHYSPRESET, node, "phys_preset")


def piece(io: XStream, p: Chunk, node: dict) -> None:
    stages = node.setdefault("stages", {})
    for k in range(5):
        c = p.sub(DESTRUCTIBLE_STAGE_SIZE * k, DESTRUCTIBLE_STAGE_SIZE)
        stage(io, c, stages.setdefault(k, {}))
    asset_ref(io, p, 0x10C, AssetType.PHYSCONSTRAINTS, node, "phys_constraints")
    io.string(p, 0x114, node, "damage_sound")
    asset_ref(io, p, 0x118, AssetType.FX, node, "burn_effect")
    io.string(p, 0x11C, node, "burn_sound")


@register
class DestructibleDefHandler(Handler):
    asset_type = AssetType.DESTRUCTIBLEDEF
    header_size = 0x18

    def body(self, io: XStream, h: Chunk, node: dict) -> None:
        io.push(Block.VIRTUAL)
        io.string(h, 0, node, "name")
        asset_ref(io, h, 4, AssetType.XMODEL, node, "model")
        asset_ref(io, h, 8, AssetType.XMODEL, node, "pristine_model")
        pieces = items(io, h, 0x10, 3, DESTRUCTIBLE_PIECE_SIZE, h.u32(0xC), node, "pieces")
        for p, element in pieces or ():
            piece(io, p, element)
        io.pop()
