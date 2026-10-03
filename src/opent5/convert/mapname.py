"""A converted map's own name: the rules a new name must follow, and renaming every asset of
a base map zone that the game finds by the map's name.

Evidence is in docs/research/map-registration.md (ELF addresses are virtual addresses in
t5mp.elf). In short, the game derives from the map name (the ``mapname`` dvar):

- the zone file, ``<name>.ff``, and its image pack ``<name>.pak`` (DB_LoadXAssets via the
  level loader at 0x3a4c00; folder from 0x55bdf8);
- the world: com_map, gfx_map, game_map_mp and col_map_mp named ``maps/mp/<name>.d3dbsp``
  (0x4b5370, ``maps/mp/%s.d3dbsp``);
- the level script ``maps/mp/<name>`` (0x334d9c, ``maps/mp/%s`` with the ``mapname`` dvar)
  and the client script ``clientscripts/mp/<name>``;
- the configstring tables ``mp/configStrings/configStrings_ps3_<name>_<gametype>.csv``
  (0x476708);
- ``exposure/<name>.xpo`` (0x186190; ``exposure/default.xpo`` when missing) and
  ``vision/<name>.vision`` (0x184c98; ``vision/default.vision`` when missing);
- the load screen material ``loadscreen_<name>`` (0x448de0; none shown when missing).

``rename_assets`` renames the base zone's own copies of those (scripts, configstring tables,
sun and exposure files) and rewrites the script paths inside the scripts; the world names are
set by ``mapzone.convert_map``. Every other occurrence of the base name (model, material,
image, effect and sound names, the compass material, the vision the art script sets) names an
asset that keeps its name and is left as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from opent5.convert.world import ConvertError
from opent5.edit.content import rawfile_bytes, rawfile_text
from opent5.xfile import AssetType

#: The UI map table copies a map's name into a 24-byte field (0x416ae4: ``li r5,24`` before
#: the string copy into the entry at 0x1874af8 + 19424 + 112 i), so 23 characters at most.
NAME_MAX = 23
#: Suffixes the zone lookup strips before it looks a name up (0x55be10 ``_load``, 0x55be54
#: ``_patch``) and that the zone opener treats as optional zones (0x260dcc, 0x260de4).
FORBIDDEN_SUFFIXES = ("_load", "_patch")
#: Names the game already knows: the 26 multiplayer maps of the level table at 0xb420e0
#: (and of patch_mp's mp/mapstable.csv), the menu background map the gametype scripts test
#: for, and the four cut maps whose localized names patch_mp still carries.
RESERVED = frozenset(
    (
        "mp_array mp_cairo mp_cosmodrome mp_cracked mp_crisis mp_duga mp_firingrange "
        "mp_hanoi mp_havoc mp_mountain mp_nuked mp_radiation mp_russianbase mp_villa "
        "mp_berlinwall2 mp_discovery mp_kowloon mp_stadium mp_hotel mp_gridlock "
        "mp_outskirts mp_zoo mp_area51 mp_drivein mp_golfcourse mp_silo "
        "mp_background mp_firebase mp_salvage mp_snowmine mp_warmuseum"
    ).split()
)
_NAME = re.compile(r"mp_[a-z0-9_]+")

#: Rawfile names that belong to the map: its scripts, sun and exposure files.
_RAWFILE = (
    r"(maps/mp/(?:createfx/|createart/)?|clientscripts/mp/(?:createfx/)?|sun/|exposure/)"
    r"{base}((?![A-Za-z0-9])[A-Za-z0-9_]*\.(?:gsc|csc|sun|xpo))"
)
#: The configstring tables, one per gametype.
_TABLE = r"(mp/configstrings/configstrings_ps3_){base}(_[a-z0-9]+\.csv)"
#: Script path references inside a script: maps\mp\<base>..., createfx, createart, client.
_SCRIPT_REF = r"((?:maps|clientscripts)\\mp\\(?:createfx\\|createart\\)?){base}(?![A-Za-z0-9])"


def validate(name: str, base: str | None = None) -> str:
    """The name when it is usable for a new map, else ConvertError saying which rule fails."""
    if not isinstance(name, str) or not _NAME.fullmatch(name or ""):
        raise ConvertError(
            f"map name {name!r}: expected lower case letters, digits and '_' after an 'mp_' "
            "prefix (e.g. mp_opent5box)"
        )
    if len(name) > NAME_MAX:
        raise ConvertError(
            f"map name {name!r}: expected at most {NAME_MAX} characters (the game's map table "
            f"keeps 24 bytes with the terminator), found {len(name)}"
        )
    if name.endswith(FORBIDDEN_SUFFIXES):
        raise ConvertError(
            f"map name {name!r}: expected no '_load' or '_patch' ending (the zone loader strips "
            "those)"
        )
    if name in RESERVED or (base is not None and name == base):
        raise ConvertError(
            f"map name {name!r}: expected a name the game does not already use, found a "
            "stock map name"
        )
    return name


def asset_rename(asset_type: int, name: str, base: str, new: str) -> str | None:
    """The new name of a base-zone asset found by the map's name, or None when the asset
    keeps its name."""
    if not name:
        return None
    pattern = _RAWFILE if asset_type == AssetType.RAWFILE else None
    if asset_type == AssetType.STRINGTABLE:
        pattern = _TABLE
    if pattern is None:
        return None
    m = re.fullmatch(pattern.format(base=re.escape(base)), name)
    if m is None:
        return None
    return m.group(1) + new + m.group(2)


def script_text(text: str, base: str, new: str) -> tuple[str, int]:
    """``text`` with every script path that names the base map's scripts renamed, and the
    number of references rewritten."""
    pattern = re.compile(_SCRIPT_REF.format(base=re.escape(base)), re.I)
    return pattern.subn(lambda m: m.group(1) + new, text)


@dataclass
class RenameReport:
    #: asset index -> (old name, new name)
    renamed: dict[int, tuple[str, str]] = field(default_factory=dict)
    #: script name (new) -> script path references rewritten
    references: dict[str, int] = field(default_factory=dict)
    #: assets left as they are although they are named after the base map: their name
    #: field points at another asset's name string, which is renamed (index -> note)
    following: dict[int, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "renamed": {str(i): {"from": o, "to": n} for i, (o, n) in self.renamed.items()},
            "script_references": self.references,
            "following": {str(i): v for i, v in self.following.items()},
        }


def rename_assets(xfile, base: str, new: str, pointer_fields: set[int]) -> RenameReport:
    """Rename the base zone's map-named rawfiles and stringtables in place and rewrite the
    script paths inside every script. ``pointer_fields``: id() of the asset nodes whose name
    field is an offset pointer to a string loaded earlier (their text follows that string;
    they are reported, not renamed)."""
    out = RenameReport()
    taken = {(a.type, a.name) for a in xfile.assets}
    for a in xfile.assets:
        if a.type not in (AssetType.RAWFILE, AssetType.STRINGTABLE) or not isinstance(a.data, dict):
            continue
        name = a.data.get("name") or ""
        if a.type == AssetType.RAWFILE and name.endswith((".gsc", ".csc")):
            text = rawfile_text(a.data)
            changed, count = script_text(text, base, new)
            if count:
                a.data["header"], a.data["buffer"] = rawfile_bytes(a.data, changed)
                out.references[name] = count
        if id(a.data) in pointer_fields:
            if name == base:
                out.following[a.index] = "named by the gfx_map base name, which is renamed"
            continue
        target = asset_rename(a.type, name, base, new)
        if target is None:
            continue
        if (a.type, target) in taken:
            raise ConvertError(
                f"asset {a.index} ({name}): the new name {target!r} is already used in the zone"
            )
        a.data["name"] = target
        out.renamed[a.index] = (name, target)
        if name in out.references:
            out.references[target] = out.references.pop(name)
    return out


def name_pointer_nodes(rewrite) -> set[int]:
    """id() of every node whose ``name`` field is an offset pointer to an earlier string."""
    return {id(node) for node, key in rewrite.trace.string_fields.values() if key == "name"}
