"""weapon (26): WeaponVariantDef (228) -> WeaponDef (2056). docs/research/structs-content.md
section 13. Sizes and pointer offsets are those of PC T5, so the PC field names
apply (OpenAssetTools, src/Common/Game/T5/T5_Assets.h); fields the document
does not name individually are keyed by their offset.

Loaders: Ptr 0x253e30, WeaponVariantDef 0x253790, WeaponDef 0x252308,
FlameTable 0x249810.
"""

from __future__ import annotations

from opent5.xfile.constants import AssetType, Block
from opent5.xfile.handlers.base import Handler, asset_ref, register
from opent5.xfile.stream import Chunk, XStream

WEAPON_DEF_SIZE = 2056
FLAME_TABLE_SIZE = 476


def read_flame_table(st: XStream) -> dict:
    f = st.load(FLAME_TABLE_SIZE)
    out: dict = {"name": st.string(f, 424)}
    out["materials"] = [asset_ref(st, f, off, AssetType.MATERIAL) for off in range(428, 460, 4)]
    out["strings"] = [st.string(f, off) for off in (460, 464, 468, 472)]
    out["raw"] = f.bytes()
    return out


def read_xmodel_array16(st: XStream, chunk: Chunk, off: int):
    if st.follows(chunk, off):
        st.alloc(3)
        table = st.load(64)
        return [asset_ref(st, table, 4 * i, AssetType.XMODEL) for i in range(16)]
    return None


def read_fixed(st: XStream, chunk: Chunk, off: int, mask: int, size: int) -> bytes | None:
    if st.follows(chunk, off):
        st.alloc(mask)
        return st.load(size).bytes()
    return None


def read_weapon_def(st: XStream, d: Chunk) -> dict:
    s = st.string
    out: dict = {}
    strings: dict[int, str | None] = {}
    fx: dict[int, object] = {}
    materials: dict[int, object] = {}
    models: dict[int, object] = {}
    out["szOverlayName"] = s(d, 0)
    out["gunXModel"] = read_xmodel_array16(st, d, 4)
    models[8] = asset_ref(st, d, 8, AssetType.XMODEL)  # handXModel
    out["szModeName"] = s(d, 12)
    out["notetrackSoundMapKeys"] = read_fixed(st, d, 16, 1, 40)
    out["notetrackSoundMapValues"] = read_fixed(st, d, 20, 1, 40)
    out["parentWeaponName"] = s(d, 60)
    fx[116] = asset_ref(st, d, 116, AssetType.FX)  # viewFlashEffect
    fx[120] = asset_ref(st, d, 120, AssetType.FX)  # worldFlashEffect
    for off in range(124, 372, 4):  # 62 sound names
        strings[off] = s(d, off)
    out["bounceSound"] = None
    if st.follows(d, 372):
        st.alloc(3)
        table = st.load(124)
        out["bounceSound"] = [s(table, 4 * i) for i in range(31)]
    for off in (376, 380, 384):  # stand / crouch / prone mounted weapdef
        strings[off] = s(d, off)
    for off in (400, 404, 408, 412):  # shell-eject effects
        fx[off] = asset_ref(st, d, off, AssetType.FX)
    materials[416] = asset_ref(st, d, 416, AssetType.MATERIAL)  # reticleCenter
    materials[420] = asset_ref(st, d, 420, AssetType.MATERIAL)  # reticleSide
    out["worldModel"] = read_xmodel_array16(st, d, 780)
    for off in (784, 788, 792, 796):  # worldClip, rocket, mounted, additionalMelee models
        models[off] = asset_ref(st, d, off, AssetType.XMODEL)
    materials[800] = asset_ref(st, d, 800, AssetType.MATERIAL)  # hudIcon
    materials[816] = asset_ref(st, d, 816, AssetType.MATERIAL)  # ammoCounterIcon
    strings[844] = s(d, 844)  # szSharedAmmoCapName
    for off in (916, 920, 924, 928, 932, 936, 1164):
        strings[off] = s(d, off)
    materials[1400] = asset_ref(st, d, 1400, AssetType.MATERIAL)  # killIcon
    materials[808] = asset_ref(st, d, 808, AssetType.MATERIAL)  # indicatorIcon
    strings[1420] = s(d, 1420)
    strings[1424] = s(d, 1424)
    models[1512] = asset_ref(st, d, 1512, AssetType.XMODEL)
    for off in (1520, 1528, 1536, 1544, 1552, 1560):  # projectile effects
        fx[off] = asset_ref(st, d, off, AssetType.FX)
    for off in (1564, 1568, 1572, 1576):  # projectile sounds
        strings[off] = s(d, off)
    out["parallelBounce"] = read_fixed(st, d, 1616, 3, 124)
    out["perpendicularBounce"] = read_fixed(st, d, 1620, 3, 124)
    fx[1624] = asset_ref(st, d, 1624, AssetType.FX)
    fx[1652] = asset_ref(st, d, 1652, AssetType.FX)
    strings[1656] = s(d, 1656)  # projIgnitionSound
    out["aiVsAiAccuracyGraphName"] = s(d, 1812)
    out["aiVsAiAccuracyGraphKnots"] = read_fixed(st, d, 1820, 3, 8 * d.s32(1836))
    out["originalAiVsAiAccuracyGraphKnots"] = read_fixed(st, d, 1828, 3, 8 * d.s32(1836))
    out["aiVsPlayerAccuracyGraphName"] = s(d, 1816)
    out["aiVsPlayerAccuracyGraphKnots"] = read_fixed(st, d, 1824, 3, 8 * d.s32(1840))
    out["originalAiVsPlayerAccuracyGraphKnots"] = read_fixed(st, d, 1832, 3, 8 * d.s32(1840))
    for off in (1924, 1928, 1948):  # use / drop hints, script
        strings[off] = s(d, off)
    out["locationDamageMultipliers"] = read_fixed(st, d, 1980, 3, 76)
    for off in (1984, 1988, 1992, 2024, 2028):  # rumbles, flame table names
        strings[off] = s(d, off)
    for key, off in (("flameTableFirstPerson", 2032), ("flameTableThirdPerson", 2036)):
        out[key] = None
        if st.follows(d, off):
            st.alloc(3)
            out[key] = read_flame_table(st)
    fx[2040] = asset_ref(st, d, 2040, AssetType.FX)
    fx[2044] = asset_ref(st, d, 2044, AssetType.FX)
    out["strings"] = strings
    out["fx"] = fx
    out["materials"] = materials
    out["models"] = models
    out["raw"] = d.bytes()
    return out


def read_weapon(st: XStream, h: Chunk) -> dict:
    st.push(Block.VIRTUAL)
    out: dict = {"name": st.string(h, 0)}
    out["weapDef"] = None
    if st.follows(h, 8):
        st.alloc(3)
        out["weapDef"] = read_weapon_def(st, st.load(WEAPON_DEF_SIZE))
    out["szDisplayName"] = st.string(h, 12)
    out["szAltWeaponName"] = st.string(h, 20)
    out["szXAnims"] = None
    if st.follows(h, 16):
        st.alloc(3)
        table = st.load(264)
        out["szXAnims"] = [st.string(table, 4 * i) for i in range(66)]
    out["hideTags"] = None
    if st.follows(h, 24):
        st.alloc(1)
        tags = st.load(64)
        out["hideTags"] = [tags.u16(2 * i) for i in range(32)]
    out["szAmmoName"] = st.string(h, 64)
    out["szClipName"] = st.string(h, 72)
    out["materials"] = [asset_ref(st, h, off, AssetType.MATERIAL) for off in (140, 144, 148)]
    out["header"] = h.bytes()
    st.pop()
    return out


@register
class WeaponHandler(Handler):
    asset_type = AssetType.WEAPON
    header_size = 228

    def read(self, st: XStream, header: Chunk) -> dict:
        return read_weapon(st, header)
