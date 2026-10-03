"""Numbers the XFile stream is built from: blocks, pointer markers, asset types.

Evidence for every value is in docs/research/xfile.md (sections 1, 4 and 5).
"""

from __future__ import annotations

from enum import IntEnum


class Block(IntEnum):
    """The seven XFile blocks, numbered as the loader numbers them."""

    TEMP = 0
    RUNTIME = 1
    LARGE_RUNTIME = 2
    PHYSICAL_RUNTIME = 3
    VIRTUAL = 4
    LARGE = 5
    PHYSICAL = 6


BLOCK_COUNT = 7
#: Blocks whose Load_Stream copies bytes from the file at once.
FILE_BLOCKS = frozenset((Block.TEMP, Block.VIRTUAL, Block.LARGE, Block.PHYSICAL))
#: Blocks whose Load_Stream is queued and read after the last asset.
DEFERRED_BLOCKS = frozenset((Block.LARGE_RUNTIME, Block.PHYSICAL_RUNTIME))

#: Pointer field values (u32).
PTR_NULL = 0x00000000
PTR_INLINE = 0xFFFFFFFF  # -1: the data follows in the stream
PTR_INSERT = 0xFFFFFFFE  # -2: as -1, plus an alias slot reserved in VIRTUAL

#: An offset pointer is ((block << 29) | offset) + 1.
OFFSET_BLOCK_SHIFT = 29
OFFSET_MASK = (1 << OFFSET_BLOCK_SHIFT) - 1

HEADER_SIZE = 36
ASSET_LIST_OFFSET = 0x24
ASSET_LIST_SIZE = 16


def encode_offset_pointer(block: int, offset: int) -> int:
    return ((block << OFFSET_BLOCK_SHIFT) | offset) + 1


def decode_offset_pointer(value: int) -> tuple[int, int]:
    """((block << 29) | offset) + 1 -> (block, offset)."""
    value = (value - 1) & 0xFFFFFFFF
    return value >> OFFSET_BLOCK_SHIFT, value & OFFSET_MASK


#: Index = XAssetType (46 entries, from the ELF name table at 0xb5bccc).
ASSET_TYPE_NAMES = (
    "xmodelpieces physpreset physconstraints destructibledef xanim xmodel material "
    "pixelshader vertexshader techset image sound sound_patch col_map_sp col_map_mp com_map "
    "game_map_sp game_map_mp map_ents gfx_map lightdef ui_map font menufile menu localize "
    "weapon weapondef weaponvariant snddriverglobals fx impactfx aitype mptype mpbody mphead "
    "character xmodelalias rawfile stringtable packindex xGlobals ddl glasses texturelist "
    "emblemset"
).split()
ASSET_TYPE_COUNT = len(ASSET_TYPE_NAMES)


class AssetType(IntEnum):
    XMODELPIECES = 0
    PHYSPRESET = 1
    PHYSCONSTRAINTS = 2
    DESTRUCTIBLEDEF = 3
    XANIM = 4
    XMODEL = 5
    MATERIAL = 6
    PIXELSHADER = 7
    VERTEXSHADER = 8
    TECHSET = 9
    IMAGE = 10
    SOUND = 11
    SOUND_PATCH = 12
    COL_MAP_SP = 13
    COL_MAP_MP = 14
    COM_MAP = 15
    GAME_MAP_SP = 16
    GAME_MAP_MP = 17
    MAP_ENTS = 18
    GFX_MAP = 19
    LIGHTDEF = 20
    UI_MAP = 21
    FONT = 22
    MENUFILE = 23
    MENU = 24
    LOCALIZE = 25
    WEAPON = 26
    WEAPONDEF = 27
    WEAPONVARIANT = 28
    SNDDRIVERGLOBALS = 29
    FX = 30
    IMPACTFX = 31
    AITYPE = 32
    MPTYPE = 33
    MPBODY = 34
    MPHEAD = 35
    CHARACTER = 36
    XMODELALIAS = 37
    RAWFILE = 38
    STRINGTABLE = 39
    PACKINDEX = 40
    XGLOBALS = 41
    DDL = 42
    GLASSES = 43
    TEXTURELIST = 44
    EMBLEMSET = 45


def type_name(asset_type: int) -> str:
    if 0 <= asset_type < ASSET_TYPE_COUNT:
        return ASSET_TYPE_NAMES[asset_type]
    return f"type{asset_type}"
