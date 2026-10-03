"""weapon (26): WeaponVariantDef (228) -> WeaponDef (2056). docs/research/structs-content.md
section 13. Sizes and pointer offsets are those of PC T5, so the PC field names
apply (OpenAssetTools, src/Common/Game/T5/T5_Assets.h); fields the document
does not name individually are keyed by their offset.

Loaders: Ptr 0x253e30, WeaponVariantDef 0x253790, WeaponDef 0x252308,
FlameTable 0x249810.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, array, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

WEAPON_DEF_SIZE = 2056
FLAME_TABLE_SIZE = 476


def _sub(io: XStream, parent: Chunk, off: int, node: dict, key: str) -> dict | None:
    if io.follows(parent, off):
        io.alloc(3)
        if io.reading:
            node[key] = {}
        return node[key]
    if io.reading:
        node[key] = None
    return None


def flame_table_body(io: XStream, node: dict) -> None:
    f = io.load(FLAME_TABLE_SIZE, node, "raw")
    io.string(f, 424, node, "name")
    materials = node.setdefault("materials", {})
    for off in range(428, 460, 4):
        asset_ref(io, f, off, AssetType.MATERIAL, materials, off)
    strings = node.setdefault("strings", {})
    for off in (460, 464, 468, 472):
        io.string(f, off, strings, off)


def xmodel_array16(io: XStream, chunk: Chunk, off: int, node: dict, key: str) -> None:
    table = array(io, chunk, off, 3, 64, node, key + "_ptrs")
    if table is not None:
        models = node.setdefault(key, {})
        for i in range(16):
            asset_ref(io, table, 4 * i, AssetType.XMODEL, models, i)


def weapon_def_body(io: XStream, d: Chunk, node: dict) -> None:
    strings = node.setdefault("strings", {})
    fx = node.setdefault("fx", {})
    materials = node.setdefault("materials", {})
    models = node.setdefault("models", {})

    def s(off: int) -> None:
        io.string(d, off, strings, off)

    def effect(off: int) -> None:
        asset_ref(io, d, off, AssetType.FX, fx, off)

    def material(off: int) -> None:
        asset_ref(io, d, off, AssetType.MATERIAL, materials, off)

    def model(off: int) -> None:
        asset_ref(io, d, off, AssetType.XMODEL, models, off)

    s(0)  # szOverlayName
    xmodel_array16(io, d, 4, node, "gunXModel")
    model(8)  # handXModel
    s(12)  # szModeName
    array(io, d, 16, 1, 40, node, "notetrackSoundMapKeys")
    array(io, d, 20, 1, 40, node, "notetrackSoundMapValues")
    s(60)  # parentWeaponName
    effect(116)  # viewFlashEffect
    effect(120)  # worldFlashEffect
    for off in range(124, 372, 4):  # 62 sound names
        s(off)
    bounce = array(io, d, 372, 3, 124, node, "bounceSound_ptrs")
    if bounce is not None:
        names = node.setdefault("bounceSound", {})
        for i in range(31):
            io.string(bounce, 4 * i, names, i)
    for off in (376, 380, 384):  # stand / crouch / prone mounted weapdef
        s(off)
    for off in (400, 404, 408, 412):  # shell-eject effects
        effect(off)
    material(416)  # reticleCenter
    material(420)  # reticleSide
    xmodel_array16(io, d, 780, node, "worldModel")
    for off in (784, 788, 792, 796):  # worldClip, rocket, mounted, additionalMelee models
        model(off)
    material(800)  # hudIcon
    material(816)  # ammoCounterIcon
    s(844)  # szSharedAmmoCapName
    for off in (916, 920, 924, 928, 932, 936, 1164):
        s(off)
    material(1400)  # killIcon
    material(808)  # indicatorIcon
    s(1420)
    s(1424)
    model(1512)
    for off in (1520, 1528, 1536, 1544, 1552, 1560):  # projectile effects
        effect(off)
    for off in (1564, 1568, 1572, 1576):  # projectile sounds
        s(off)
    array(io, d, 1616, 3, 124, node, "parallelBounce")
    array(io, d, 1620, 3, 124, node, "perpendicularBounce")
    effect(1624)
    effect(1652)
    s(1656)  # projIgnitionSound
    s(1812)  # aiVsAiAccuracyGraphName
    array(io, d, 1820, 3, 8 * d.s32(1836), node, "aiVsAiAccuracyGraphKnots")
    array(io, d, 1828, 3, 8 * d.s32(1836), node, "originalAiVsAiAccuracyGraphKnots")
    s(1816)  # aiVsPlayerAccuracyGraphName
    array(io, d, 1824, 3, 8 * d.s32(1840), node, "aiVsPlayerAccuracyGraphKnots")
    array(io, d, 1832, 3, 8 * d.s32(1840), node, "originalAiVsPlayerAccuracyGraphKnots")
    for off in (1924, 1928, 1948):  # use / drop hints, script
        s(off)
    array(io, d, 1980, 3, 76, node, "locationDamageMultipliers")
    for off in (1984, 1988, 1992, 2024, 2028):  # rumbles, flame table names
        s(off)
    for key, off in (("flameTableFirstPerson", 2032), ("flameTableThirdPerson", 2036)):
        table = _sub(io, d, off, node, key)
        if table is not None:
            flame_table_body(io, table)
    effect(2040)
    effect(2044)


def weapon_body(io: XStream, h: Chunk, node: dict) -> None:
    io.push(Block.VIRTUAL)
    io.string(h, 0, node, "name")
    weap_def = _sub(io, h, 8, node, "weapDef")
    if weap_def is not None:
        weapon_def_body(io, io.load(WEAPON_DEF_SIZE, weap_def, "raw"), weap_def)
    io.string(h, 12, node, "szDisplayName")
    io.string(h, 20, node, "szAltWeaponName")
    anims = array(io, h, 16, 3, 264, node, "szXAnims_ptrs")
    if anims is not None:
        names = node.setdefault("szXAnims", {})
        for i in range(66):
            io.string(anims, 4 * i, names, i)
    array(io, h, 24, 1, 64, node, "hideTags")
    io.string(h, 64, node, "szAmmoName")
    io.string(h, 72, node, "szClipName")
    materials = node.setdefault("materials", {})
    for off in (140, 144, 148):
        asset_ref(io, h, off, AssetType.MATERIAL, materials, off)
    io.pop()


@register
class WeaponHandler(Handler):
    asset_type = AssetType.WEAPON
    header_size = 228

    def body(self, io: XStream, header: Chunk, node: dict) -> None:
        weapon_body(io, header, node)
