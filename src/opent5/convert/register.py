"""Registering a new map with the game's menus: a row in patch_mp's ``mp/mapstable.csv`` and
the localized name, written into a copy of the update's patch_mp.ff.

How the game uses the table (docs/research/map-registration.md; ELF addresses are virtual
addresses in t5mp.elf):

- 0x4166f0 builds the UI map list: ``maxnum_map`` (column 1 of the row whose column 0 is
  ``maxnum_map``) entries; entry i is the row whose column 5 is ``str(i)``. From that row it
  keeps column 0 (the map name, 24-byte field), column 3 (the localize key of the name, and
  ``<key>_CAPS``), column 10 (``YES``: offered in splitscreen) and column 11 (the map pack:
  0 the base game, 2..5 a DLC pack whose content must be mounted). Entries are 112 bytes in
  an array of 128 (memset of 0x3800 bytes at 0x18796b8).
- 0x40dd80 lists, when ``ui_showDLCMaps`` is off, the entries of pack 0, so a pack-0 row is
  offered with the base maps in a private match or splitscreen game.
- patch_mp's ``dvar_defaults.cfg`` clears ``ui_mapname`` when column 5 of the map's row is
  empty, and common_mp's ``maps/mp/gametypes/_teams.gsc`` reads the team sets from
  columns 1 and 2 (lines 299, 309).
- Column 7 (compass overlay) is read by index for the combat record heat map (0x439748);
  columns 12..15 (team short names, faction) by index at 0x6610f0..0x6611c8.

The name shown in the menus is the value of the column-3 key. A zone cannot gain a
localize asset through the editing layer, so the new row takes one of the localized names
patch_mp carries for four cut maps (no table row and no menu uses them) and sets its text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from opent5.convert.mapname import NAME_MAX, validate
from opent5.convert.world import ConvertError

TABLE = "mp/mapstable.csv"
COLUMNS = 16
#: The UI map array holds 128 entries (0x3800 bytes / 112, cleared at 0x41677c).
MAX_MAPS = 128
#: Localized names in patch_mp that no map table row and no menu uses (the cut maps
#: Firebase, Salvage, Snow Mine, War Museum): (name key, its _CAPS key, description key).
SLOTS = {
    "warmuseum": ("MPUI_WARMUSEUM", "MPUI_WARMUSEUM_CAPS", "MPUI_DESC_MAP_WARMUSEUM"),
    "snowmine": ("MPUI_SNOWMINE", "MPUI_SNOWMINE_CAPS", "MPUI_DESC_MAP_SNOWMINE"),
    "salvage": ("MPUI_SALVAGE", "MPUI_SALVAGE_CAPS", "MPUI_DESC_MAP_SALVAGE"),
    "firebase": ("MPUI_FIREBASE", "MPUI_FIREBASE_CAPS", "MPUI_DESC_MAP_FIREBASE"),
}


@dataclass
class MapEntry:
    """One entry of the UI map list as 0x4166f0 builds it."""

    index: int
    name: str
    key: str
    splitscreen: bool
    pack: int


def entries(rows: list[list[str]]) -> list[MapEntry]:
    """The UI map list the game builds from the table's rows (0x4166f0)."""
    count = 0
    for row in rows:
        if row and row[0] == "maxnum_map":
            count = _int(row[1] if len(row) > 1 else "")
    out = []
    by_index = {}
    for row in rows:
        if len(row) > 5 and row[5] not in by_index:
            by_index[row[5]] = row
    for i in range(count):
        row = by_index.get(str(i))
        if row is None:
            out.append(MapEntry(i, "", "", False, 0))
            continue
        out.append(
            MapEntry(
                i,
                row[0][:NAME_MAX],
                row[3],
                len(row) > 10 and row[10] == "YES",
                _int(row[11] if len(row) > 11 else ""),
            )
        )
    return out


def offered(rows: list[list[str]], show_dlc: bool = False) -> list[str]:
    """Map names the map list offers (0x40dd80): pack 0 with ``ui_showDLCMaps`` off."""
    return [e.name for e in entries(rows) if (e.pack != 0) == show_dlc and e.name]


def _int(text: str) -> int:
    try:
        return int(text.strip(), 10)
    except ValueError:
        return 0


@dataclass
class RowPlan:
    #: the row as it will be stored
    values: list[str]
    #: row number in the table (existing row) or None (a new row at the end)
    row: int | None
    #: new maxnum_map value, or None when it stays
    maxnum: int | None
    maxnum_row: int
    #: the keys (name, caps, description)
    keys: tuple[str, str, str]
    notes: list[str] = field(default_factory=list)


def plan_row(rows: list[list[str]], name: str, base: str, slot: str = "warmuseum") -> RowPlan:
    """The map table row for ``name``: a copy of ``base``'s row (team sets, size,
    splitscreen, team names, compass overlay, map select image) with its own name, index,
    localize keys and pack 0. An existing row of ``name`` is updated in place."""
    validate(name)
    if slot not in SLOTS:
        raise ConvertError(f"ui slot {slot!r}: expected one of {', '.join(SLOTS)}")
    keys = SLOTS[slot]
    if not rows or len(rows[0]) != COLUMNS:
        found = len(rows[0]) if rows else 0
        raise ConvertError(f"{TABLE}: expected {COLUMNS} columns, found {found}")
    maxnum_row = next((i for i, r in enumerate(rows) if r[0] == "maxnum_map"), None)
    if maxnum_row is None:
        raise ConvertError(f"{TABLE}: expected a 'maxnum_map' row, found none")
    count = _int(rows[maxnum_row][1])
    base_row = next((r for r in rows if r[0] == base), None)
    if base_row is None:
        raise ConvertError(f"{TABLE}: expected a row for the base map {base!r}, found none")
    for r in rows:
        if r[0] != name and r[3] in keys[:1]:
            raise ConvertError(
                f"{TABLE}: the name key {keys[0]} is already used by {r[0]}; choose another slot"
            )
    existing = next((i for i, r in enumerate(rows) if r[0] == name), None)
    if existing is not None:
        index = rows[existing][5]
        maxnum = None
        notes = [f"{name} already has row {existing} (index {index}); updated in place"]
    else:
        used = {r[5] for r in rows if len(r) > 5}
        if str(count) in used:
            raise ConvertError(
                f"{TABLE}: index {count} (maxnum_map) is already used by another row"
            )
        if count + 1 > MAX_MAPS:
            raise ConvertError(
                f"{TABLE}: the game keeps {MAX_MAPS} maps at most, the table has {count}"
            )
        index = str(count)
        maxnum = count + 1
        notes = []
    values = list(base_row)
    values[0] = name
    values[3] = keys[0]
    values[5] = index
    values[6] = keys[2]
    values[11] = "0"
    return RowPlan(values, existing, maxnum, maxnum_row, keys, notes)


def default_title(name: str) -> str:
    """'mp_opent5box' -> 'Opent5box'."""
    stem = name[3:] if name.startswith("mp_") else name
    return stem.replace("_", " ").strip().capitalize() or name


def register_map(
    patch_mp: str | Path,
    out_path: str | Path,
    name: str,
    base: str,
    title: str | None = None,
    description: str | None = None,
    slot: str = "warmuseum",
) -> dict:
    """Write a copy of ``patch_mp`` (read only) to ``out_path`` with ``name`` registered:
    its map table row, ``maxnum_map`` and the localized name, caps name and description.
    Returns the report (rows, keys, the saved file's checks and the map list read back)."""
    from opent5.edit import Document, EditError

    title = title or default_title(name)
    description = description or f"{title}: a map converted with OpenT5."
    try:
        doc = Document.open(patch_mp)
        ref = doc.find(TABLE, "stringtable")
        if ref is None:
            raise ConvertError(f"{patch_mp}: expected a stringtable {TABLE}, found none")
        rows = doc.table(ref.index)
        plan = plan_row(rows, name, base, slot)
        if plan.row is None:
            row = doc.add_row(ref.index, plan.values)
            doc.set_cell(ref.index, plan.maxnum_row, 1, str(plan.maxnum))
        else:
            row = plan.row
            for col, value in enumerate(plan.values):
                doc.set_cell(ref.index, row, col, value)
        texts = (title, title.upper(), description)
        for key, text in zip(plan.keys, texts, strict=True):
            loc = doc.find(key, "localize")
            if loc is None:
                raise ConvertError(f"{patch_mp}: expected a localize entry {key}, found none")
            doc.set_localize(loc.index, text)
        saved = doc.save(out_path, verify=True)
        check = Document.open(out_path)
        back = check.table(check.find(TABLE, "stringtable").index)
        names = {key: check.localize(check.find(key, "localize").index)[1] for key in plan.keys}
    except EditError as exc:
        raise ConvertError(str(exc)) from None
    listed = offered(back)
    if name not in listed:
        raise ConvertError(f"{out_path}: read back, the map list does not offer {name}")
    if names[plan.keys[0]] != title:
        raise ConvertError(f"{out_path}: read back, {plan.keys[0]} is {names[plan.keys[0]]!r}")
    entry = next(e for e in entries(back) if e.name == name)
    return {
        "source": str(patch_mp),
        "output": str(out_path),
        "sha1": saved.sha1,
        "bytes": saved.bytes,
        "verified": saved.verified,
        "problems": list(saved.problems),
        "assets_changed": saved.assets_changed,
        "row": row,
        "values": plan.values,
        "maxnum_map": plan.maxnum if plan.maxnum is not None else len(entries(back)),
        "keys": dict(zip(("name", "caps", "description"), plan.keys, strict=True)),
        "texts": names,
        "entry": {
            "index": entry.index,
            "name": entry.name,
            "key": entry.key,
            "splitscreen": entry.splitscreen,
            "pack": entry.pack,
        },
        "offered_without_dlc": listed,
        "notes": plan.notes,
        "signature": saved.signature_note,
    }
