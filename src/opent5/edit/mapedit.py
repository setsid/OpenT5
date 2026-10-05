"""``EditSession``: in-place editing of a map zone's placed objects (v0.3.0 phase 1).

The session wraps one open ``opent5.edit.Document`` and holds the zone's placed objects as an
in-memory model: the entities of the map_ents entity string (spawns, objective entities and
placed lights), plus the sun and the fog. Every change is a small reversible ``Edit`` on that
model; undo and redo are a cursor over an ordered list of them, and a drag coalesces its many
moves into one entry.

Edits never touch a file. Applying one mutates the model and then reconciles it into the
document's parsed nodes through the existing edit layer (``Document.set_text`` for the entity
string, ``Document.set_field`` for the GfxWorld sun light), so ``Document.build`` and
``Document.save`` produce and verify the bytes unchanged. Saving writes a new file only
(``<name>.edited.ff`` by default), after the document's verify (reparse-exact and the emulated
loader checks) and the gametype entity rules (``opent5.convert.entities``); a save that would
break a gametype that worked before, or that fails verify, writes nothing and reports why.

The entity string stays byte-identical while no entity is edited; the first entity edit
rewrites it in the canonical ``"key" "value"`` form, which still reparses exactly.
"""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from opent5.convert import entities as ent
from opent5.convert import propclip as pc
from opent5.convert.scripts import entity_text, parse_entities
from opent5.edit import pathregen
from opent5.edit.document import Document
from opent5.edit.types import AssetKey, EditError, SaveReport
from opent5.xfile.constants import AssetType as T
from opent5.xfile.schema import view

#: Default world axes to offset a duplicate along, and the default nudge.
DUPLICATE_OFFSET = (64.0, 0.0, 0.0)

#: Classname fragments used to label a marker in the 3D view.
_SPAWN = "_spawn"
_LIGHT_CLASSES = frozenset({"light", "corona", "light_environment"})

#: Shown when a prop's footprint holds no clip cluster to move or remove.
_COLLISION_STAYS = (
    "No clip collision was found at this prop's footprint, so its collision is left as "
    "it is. Nothing was moved or removed."
)


def _baked_shadow_notice(action: str) -> str:
    """The baked-lightmap warning for moving or deleting a stock prop. The editor cannot
    relight the map, so a prop's baked shadow stays where it was baked."""
    return (
        f"{action} this prop leaves its baked lightmap shadow at the old position. The "
        "editor does not relight the map, so the shadow moves only with a full relight, "
        "which is not available here."
    )


def _non_axial_clip_notice(degrees: float) -> str:
    """Said when a prop is rotated by a non-quarter-turn: an axis-aligned clip cannot slant,
    so the box collision becomes the axis-aligned bounding box of the rotated footprint."""
    return (
        f"Rotated {degrees:g} degrees, which is not a quarter turn. An axis-aligned clip "
        "cannot slant, so the prop's box collision is kept as the axis-aligned footprint of "
        "the rotated bounds (a little wider than the model). A quarter turn (90, 180, 270) is "
        "represented exactly."
    )


def _render_absent_notice(model: str) -> str:
    return (
        f"Added the prop {model!r} as a solid clip and a static-model record, but the zone "
        "carries no GfxWorld draw instance for it to clone, so it may not draw in game. Its "
        "collision is in place."
    )


def _render_moved_collision_stays() -> str:
    """Said when a prop with no clip cluster is moved: its model follows the gizmo, but its
    baked collision cannot be relocated here."""
    return (
        "Moved the prop's model, but no clip collision was found at its footprint, so its "
        "collision stays at the old position. The model draws at the new spot; baked collision "
        "moves only with a full recompile, which is not available here."
    )


def _render_moved_fresh_clip_notice() -> str:
    """Said when a prop with no clip cluster is moved: the model moved and a fresh solid clip
    was added at the new footprint, so it is solid where it now sits. The old baked-triangle
    collision cannot be removed here, so it is left behind and we say so."""
    return (
        "Moved the prop's model and added a solid clip at the new footprint, so it blocks "
        "where it now sits. Any baked-triangle collision at the old position stays, as baked "
        "collision clears only with a full recompile, which is not available here."
    )


def _render_moved_no_clip_added_notice() -> str:
    """Said when a clip-less prop is moved but the new spot is outside the collision BSP, so a
    clip could not be added there."""
    return (
        "Moved the prop's model, but the new spot is outside the collision BSP, so no clip "
        "could be added there and it is not solid. Any baked collision at the old position "
        "stays (baked collision clears only with a full recompile)."
    )


def _render_hidden_collision_stays() -> str:
    """Said when a prop with no clip cluster is deleted: the model is hidden, but any baked
    collision remains."""
    return (
        "Hid the prop's model, but it has no clip cluster to remove, so any baked-triangle "
        "collision stays. Baked collision clears only with a full recompile, not here."
    )


# -- static-model render struct offsets (see src/opent5/xfile/layouts_pc.py cStaticModel_s
# -- and src/opent5/convert/smodels.py for the GfxWorld draw inst / inst layouts).
_SM_SIZE = 0x50  # cStaticModel_s
_SM_ORIGIN = 0x08
_SM_INVAXIS = 0x14  # invScaledAxis, mat3 (9 f32, row-major)
_SM_ABSMIN = 0x38
_SM_ABSMAX = 0x44
_DRAW_SIZE = 0x2C  # GfxStaticModelDrawInst
_DRAW_ORIGIN = 0x04
_DRAW_AXIS = 0x10  # three CMP axis words
_DRAW_SCALE = 0x1C  # placement.scale (f32); 0 is a degenerate placement that draws nothing
_INST_SIZE = 0x28  # GfxStaticModelInst
_INST_MINS = 0x00
_INST_MAXS = 0x0C
#: clipMap_t header numStaticModels; GfxWorld header dpvs.smodelCount (sizes both the smodel
#: inst and draw-inst arrays on PS3, confirmed by round-trip on mp_nuked).
_CLIP_NUM_SMODELS = 0x10
_GFX_SMODEL_COUNT = 0x35C
#: Tolerance (world units) for matching a clipMap static-model prop to its GfxWorld draw
#: instance by origin, once the model name already constrains the candidates. The two tables
#: store the same placement, so a correct match sits within a unit; the small window only
#: rejects a same-model instance that is a different placement.
_LINK_TOL = 2.0


def _vec_str(vec) -> str:
    return " ".join(f"{float(v):g}" for v in vec)


def _parse_vec(text: str | None, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    try:
        x, y, z = (float(v) for v in (text or "").split()[:3])
    except ValueError:
        return tuple(float(v) for v in default)  # type: ignore[return-value]
    return (x, y, z)


@dataclass
class StaticModelProp:
    """A static-model prop read from the clipMap's ``staticModelList``: its index, the
    model name, world origin and the baked collision footprint (absmin, absmax). The
    editor finds a prop's clip cluster under this footprint."""

    index: int
    model: str | None
    origin: tuple[float, float, float]
    absmin: tuple[float, float, float]
    absmax: tuple[float, float, float]

    @property
    def footprint(self) -> tuple[tuple, tuple]:
        return (self.absmin, self.absmax)


@dataclass
class MapObject:
    """One placed object the editor shows: a stable synthetic ``id`` and its ordered
    key/value pairs (the entity's own keys from the entity string)."""

    id: int
    keys: dict[str, str]

    @property
    def classname(self) -> str:
        return self.keys.get("classname", "")

    @property
    def origin(self) -> tuple[float, float, float]:
        return _parse_vec(self.keys.get("origin"))

    @property
    def angles(self) -> tuple[float, float, float]:
        return _parse_vec(self.keys.get("angles"))

    @property
    def kind(self) -> str:
        cls = self.classname
        if cls == "worldspawn":
            return "worldspawn"
        if _SPAWN in cls:
            return "spawn"
        if cls in _LIGHT_CLASSES or cls.startswith("light"):
            return "light"
        if cls.startswith("trigger_") or cls.startswith("script_"):
            return "objective"
        return "entity"

    @property
    def label(self) -> str:
        return self.keys.get("targetname") or self.classname or f"object {self.id}"


# -- edits -----------------------------------------------------------------------------------


class Edit:
    """A reversible change to the model. ``apply`` and ``revert`` mutate the session's model
    only; the session reconciles the result into the document. ``merge_key`` (not None) marks
    edits that coalesce into the previous one, e.g. the steps of one drag."""

    label = "edit"
    merge_key: tuple | None = None

    def apply(self, session: EditSession) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def revert(self, session: EditSession) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass
class _KeyEdit(Edit):
    """Set or delete one key of one object (None deletes)."""

    obj_id: int
    key: str
    before: str | None
    after: str | None
    label: str = "set property"
    merge_key: tuple | None = None

    def _set(self, session: EditSession, value: str | None) -> None:
        keys = session._object(self.obj_id).keys
        if value is None:
            keys.pop(self.key, None)
        else:
            keys[self.key] = value

    def apply(self, session: EditSession) -> None:
        self._set(session, self.after)

    def revert(self, session: EditSession) -> None:
        self._set(session, self.before)


@dataclass
class MoveObject(_KeyEdit):
    label: str = "move"


@dataclass
class RotateObject(_KeyEdit):
    label: str = "rotate"


@dataclass
class SetProperty(_KeyEdit):
    label: str = "set property"


@dataclass
class AddObject(Edit):
    keys: dict[str, str]
    obj_id: int
    index: int | None = None  # position in the list; None appends
    label: str = "add"
    merge_key: tuple | None = None

    def apply(self, session: EditSession) -> None:
        obj = MapObject(self.obj_id, dict(self.keys))
        if self.index is None:
            session._objects.append(obj)
        else:
            session._objects.insert(self.index, obj)

    def revert(self, session: EditSession) -> None:
        session._remove(self.obj_id)


@dataclass
class DuplicateObject(AddObject):
    source_id: int = -1
    label: str = "duplicate"


@dataclass
class DeleteObject(Edit):
    obj_id: int
    index: int
    keys: dict[str, str]
    label: str = "delete"
    merge_key: tuple | None = None

    def apply(self, session: EditSession) -> None:
        session._remove(self.obj_id)

    def revert(self, session: EditSession) -> None:
        session._objects.insert(self.index, MapObject(self.obj_id, dict(self.keys)))


@dataclass
class _SunFog(Edit):
    """Worldspawn key changes (before/after per key) plus, for the sun, the GfxWorld sun
    light colour and direction the session should hold."""

    worldspawn: dict[str, tuple[str | None, str | None]]
    before_color: tuple | None = None
    after_color: tuple | None = None
    before_dir: tuple | None = None
    after_dir: tuple | None = None
    label: str = "set sun"
    merge_key: tuple | None = None

    def _apply_keys(self, session: EditSession, which: int) -> None:
        ws = session._worldspawn()
        if ws is None:
            raise EditError(f"{session.doc.zone_name}: the map has no worldspawn entity")
        for key, pair in self.worldspawn.items():
            value = pair[which]
            if value is None:
                ws.keys.pop(key, None)
            else:
                ws.keys[key] = value

    def apply(self, session: EditSession) -> None:
        self._apply_keys(session, 1)
        if self.after_color is not None or self.before_color is not None:
            session._sun_color = self.after_color
        if self.after_dir is not None or self.before_dir is not None:
            session._sun_dir = self.after_dir

    def revert(self, session: EditSession) -> None:
        self._apply_keys(session, 0)
        if self.after_color is not None or self.before_color is not None:
            session._sun_color = self.before_color
        if self.after_dir is not None or self.before_dir is not None:
            session._sun_dir = self.before_dir


@dataclass
class SetSun(_SunFog):
    label: str = "set sun"


@dataclass
class SetFog(_SunFog):
    label: str = "set fog"


@dataclass
class ClipEdit(Edit):
    """A reversible clip-collision edit. The clipMap node is mutated in place by
    ``opent5.convert.propclip`` through the document's Rewrite, so the edit holds a
    full snapshot of the clip node (and the engine's append bookkeeping) before and
    after, plus the prop->clip association either side. ``apply`` and ``revert``
    assign the snapshots back, restoring the clipMap exactly."""

    before: dict
    after: dict
    assoc_before: dict
    assoc_after: dict
    label: str = "clip"
    merge_key: tuple | None = None

    def apply(self, session: EditSession) -> None:
        session._restore_clip(self.after)
        session._prop_clips = {k: list(v) for k, v in self.assoc_after.items()}

    def revert(self, session: EditSession) -> None:
        session._restore_clip(self.before)
        session._prop_clips = {k: list(v) for k, v in self.assoc_before.items()}


# -- the session -----------------------------------------------------------------------------


@dataclass
class _Entry:
    edit: Edit
    #: number of document operations this edit pushed (to undo/redo together).
    ops: int


class EditSession:
    """Edit one open ``Document``'s placed objects, sun and fog."""

    def __init__(self, doc: Document):
        self.doc = doc
        self._undo: list[_Entry] = []
        self._redo: list[_Entry] = []
        self._next_id = 0
        self._locate()
        self._load_model()

    @classmethod
    def open(cls, source, name: str | None = None, progress=None) -> EditSession:
        return cls(Document.open(source, name=name, progress=progress))

    # -- locating the sources ----------------------------------------------------------------

    def _locate(self) -> None:
        self._ent_key: AssetKey | None = None
        self._clip_node: dict | None = None
        self._clip_index: AssetKey | None = None
        self._gfx_index: int | None = None
        self._gfx_node: dict | None = None
        self._game_index: AssetKey | None = None
        self._game_node: dict | None = None
        for a in self.doc.assets:
            if a.type in (T.COL_MAP_MP, T.COL_MAP_SP):
                node = self.doc.xfile.assets[a.index].data
                if isinstance(node, dict) and self._clip_node is None:
                    # the clipMap carries the collision (brushes, leaves, BSP) the clip
                    # editor edits, and often the entity string too.
                    self._clip_node = node
                    self._clip_index = a.index
                    if self._ent_key is None and isinstance(node.get("map_ents"), dict):
                        self._ent_key = a.index
            elif a.type == T.MAP_ENTS and self._ent_key is None:
                self._ent_key = a.index
            elif a.type == T.GFX_MAP and self._gfx_index is None:
                self._gfx_index = a.index
                self._gfx_node = self.doc.xfile.assets[a.index].data
            elif a.type in (T.GAME_MAP_MP, T.GAME_MAP_SP) and self._game_index is None:
                # the GameWorld* asset holds the path nodes (PathData) the save-time
                # line-of-sight regen re-tests against the edited collision.
                node = self.doc.xfile.assets[a.index].data
                if isinstance(node, dict):
                    self._game_index = a.index
                    self._game_node = node
        if self._ent_key is None:
            raise EditError(
                f"{self.doc.zone_name}: expected a map_ents entity string (in a col_map or on "
                "its own), found none"
            )
        text = self.doc.text(self._ent_key)
        self._newline = "\r\n" if "\r\n" in text else "\n"
        self._orig_text = text

    def _load_model(self) -> None:
        #: (ClipMap, BspLocator) once built, (None, None) when the zone has none; None at first.
        self._clip: tuple | None = None
        #: stable prop-index -> GfxWorld draw-instance-index link, built once by model AND
        #: origin (``_prop_draw_map``). None until built; dropped when the prop/draw-inst set
        #: changes (an add, or an undo/redo), never on a move (indices stay put).
        self._prop_draw: dict[int, int] | None = None
        #: prop key -> the clip brush indices the editor manages for it (editor-added or moved).
        self._prop_clips: dict[Any, list[int]] = {}
        #: AABBs of the clip volumes clip edits have touched (old and new footprints), so the
        #: save-time regen re-tests only the path links crossing an edit. Monotonic: a region
        #: that was ever edited stays worth re-testing, and re-testing a clear link is a no-op.
        self._edited_boxes: list[tuple[tuple, tuple]] = []
        self._objects: list[MapObject] = []
        for keys in parse_entities(self._orig_text):
            self._objects.append(MapObject(self._next_id, dict(keys)))
            self._next_id += 1
        self._orig_keys = [dict(o.keys) for o in self._objects]
        self._sun_color = self._gfx_sun_vec("color")
        self._sun_dir = self._gfx_sun_vec("dir")
        self._orig_sun_color = self._sun_color
        self._orig_sun_dir = self._sun_dir
        # the gametypes that were ready before any edit: a save must not break them.
        self._ready_before = {
            g for g, r in self._gametype_report(self._orig_keys).items() if r["ready"]
        }

    # -- the model ---------------------------------------------------------------------------

    @property
    def objects(self) -> list[MapObject]:
        return list(self._objects)

    def _object(self, obj_id: int) -> MapObject:
        for o in self._objects:
            if o.id == obj_id:
                return o
        raise EditError(f"object {obj_id}: no such object in the session")

    def object(self, obj_id: int) -> MapObject:
        return self._object(obj_id)

    def _index_of(self, obj_id: int) -> int:
        for i, o in enumerate(self._objects):
            if o.id == obj_id:
                return i
        raise EditError(f"object {obj_id}: no such object in the session")

    def _remove(self, obj_id: int) -> None:
        self._objects.pop(self._index_of(obj_id))

    def _worldspawn(self) -> MapObject | None:
        for o in self._objects:
            if o.classname == "worldspawn":
                return o
        return None

    def markers(self) -> list[MapObject]:
        """Objects the 3D view shows a marker for: every entity that carries an origin."""
        return [o for o in self._objects if "origin" in o.keys]

    # -- the sun -----------------------------------------------------------------------------

    def _gfx_sun_vec(self, field_name: str):
        if self._gfx_index is None:
            return None
        try:
            value = self.doc.field_info(self._gfx_index, f"sun_light/{field_name}")["value"]
        except EditError:
            return None
        return tuple(float(v) for v in value) if value is not None else None

    def sun_state(self) -> dict:
        ws = self._worldspawn()
        keys = dict(ws.keys) if ws is not None else {}
        return {
            "sunlight": keys.get("sunlight"),
            "suncolor": keys.get("suncolor"),
            "sundirection": keys.get("sundirection"),
            "gfx_color": self._sun_color,
            "gfx_dir": self._sun_dir,
            "has_gfx_sun": self._gfx_index is not None and self._sun_color is not None,
        }

    def fog_state(self) -> dict:
        ws = self._worldspawn()
        keys = dict(ws.keys) if ws is not None else {}
        return {k: v for k, v in keys.items() if "fog" in k.lower()}

    # -- editing: entities -------------------------------------------------------------------

    def move_object(self, obj_id: int, origin, coalesce: bool = False, group=None) -> None:
        obj = self._object(obj_id)
        after = _vec_str(origin)
        if after == obj.keys.get("origin"):
            return
        merge = ("move", obj_id, group) if coalesce else None
        self._apply(
            MoveObject(obj_id, "origin", obj.keys.get("origin"), after, merge_key=merge)
        )

    def rotate_object(self, obj_id: int, angles, coalesce: bool = False, group=None) -> None:
        obj = self._object(obj_id)
        after = _vec_str(angles)
        if after == obj.keys.get("angles"):
            return
        merge = ("rotate", obj_id, group) if coalesce else None
        self._apply(
            RotateObject(obj_id, "angles", obj.keys.get("angles"), after, merge_key=merge)
        )

    def set_property(self, obj_id: int, key: str, value: str | None) -> None:
        if key == "classname" and not value:
            raise EditError("classname: an entity must keep a classname")
        obj = self._object(obj_id)
        before = obj.keys.get(key)
        if before == value:
            return
        self._apply(SetProperty(obj_id, key, before, value))

    def add_object(self, keys: dict[str, str], index: int | None = None) -> int:
        if not keys.get("classname"):
            raise EditError("add: an entity needs a classname")
        obj_id = self._next_id
        self._next_id += 1
        self._apply(AddObject(dict(keys), obj_id, index))
        return obj_id

    def duplicate_object(self, obj_id: int, offset=DUPLICATE_OFFSET) -> int:
        src = self._object(obj_id)
        keys = dict(src.keys)
        if "origin" in keys:
            keys["origin"] = _vec_str(np.asarray(src.origin) + np.asarray(offset, float))
        new_id = self._next_id
        self._next_id += 1
        self._apply(
            DuplicateObject(keys, new_id, self._index_of(obj_id) + 1, source_id=obj_id)
        )
        return new_id

    def delete_object(self, obj_id: int) -> None:
        obj = self._object(obj_id)
        self._apply(DeleteObject(obj_id, self._index_of(obj_id), dict(obj.keys)))

    # -- editing: sun and fog ----------------------------------------------------------------

    def set_sun(self, direction=None, color=None, intensity=None) -> None:
        """Sun direction, colour and intensity. ``direction`` and ``color`` are 3-tuples;
        they update the worldspawn keys and, where the zone carries one, the GfxWorld sun
        light. ``intensity`` is the worldspawn ``sunlight`` value."""
        ws = self._worldspawn()
        if ws is None:
            raise EditError(f"{self.doc.zone_name}: the map has no worldspawn entity to hold sun")
        changes: dict[str, tuple[str | None, str | None]] = {}
        if direction is not None:
            changes["sundirection"] = (ws.keys.get("sundirection"), _vec_str(direction))
        if color is not None:
            changes["suncolor"] = (ws.keys.get("suncolor"), _vec_str(color))
        if intensity is not None:
            changes["sunlight"] = (ws.keys.get("sunlight"), f"{float(intensity):g}")
        if not changes and color is None and direction is None:
            return
        edit = SetSun(
            changes,
            before_color=self._sun_color if color is not None else None,
            after_color=tuple(float(v) for v in color) if color is not None else None,
            before_dir=self._sun_dir if direction is not None else None,
            after_dir=tuple(float(v) for v in direction) if direction is not None else None,
        )
        self._apply(edit)

    def set_fog(self, **keys: str) -> None:
        """Fog worldspawn keys, by name, e.g. ``set_fog(fogstart="0", fogcolor="0.3 0.3 0.4")``.
        The fog source is per map (worldspawn key, dvar or a fog asset); these write the
        worldspawn-key representation."""
        ws = self._worldspawn()
        if ws is None:
            raise EditError(f"{self.doc.zone_name}: the map has no worldspawn entity to hold fog")
        changes = {k: (ws.keys.get(k), str(v)) for k, v in keys.items()}
        if not changes:
            return
        self._apply(SetFog(changes))

    # -- editing: static-model prop collision (clip cbrushes) --------------------------------

    #: node keys the clip operations rewrite; snapshotted for exact undo.
    #: clipMap node keys the clip and prop-render operations rewrite; snapshotted for exact
    #: undo. ``static_model_list`` carries the per-prop cStaticModel render record (origin,
    #: invScaledAxis, bounds), which a prop move / rotate / add changes in step with the clip.
    _CLIP_KEYS = ("header", "brushes", "leafs", "leafbrushes", "brush_verts", "static_model_list")

    def _clip_engine(self) -> tuple | None:
        """``(ClipMap, BspLocator)`` for this zone's clipMap, or ``None`` when the zone
        carries no clipMap with a cNode BSP (a synthetic or entity-only zone). Built
        once and cached."""
        if self._clip is not None:
            return self._clip if self._clip[0] is not None else None
        if self._clip_node is None:
            self._clip = (None, None)
            return None
        try:
            cm = pc.ClipMap(self._clip_node, rewrite=self.doc._rw)
            loc = pc.BspLocator.from_xfile(self.doc.xfile, self._clip_node)
        except pc.ClipError:
            self._clip = (None, None)
            return None
        self._clip = (cm, loc)
        return self._clip

    @property
    def can_edit_clips(self) -> bool:
        return self._clip_engine() is not None

    def static_models(self) -> list[StaticModelProp]:
        """The clipMap's static-model props, each with its collision footprint. Empty
        when the zone has no clipMap static models."""
        if self._clip_node is None or self._clip_node.get("static_model_list") is None:
            return []
        out: list[StaticModelProp] = []
        v = view(self._clip_node)
        for i, s in enumerate(v.array("static_model_list")):
            target = self.doc.xfile.resolve(int(s["xmodel"]))
            name = target.asset.get("name") if target and target.asset else None
            out.append(
                StaticModelProp(
                    i,
                    name,
                    tuple(float(x) for x in s["origin"]),
                    tuple(float(x) for x in s["absmin"]),
                    tuple(float(x) for x in s["absmax"]),
                )
            )
        return out

    def clip_cluster_in_footprint(self, mins, maxs) -> list[int]:
        """The clip brushes under a footprint (within it), as the editor shows and moves
        for a prop. Empty when the zone has no clipMap or no clip sits there."""
        engine = self._clip_engine()
        if engine is None:
            return []
        cm, _ = engine
        return pc.clips_in_footprint(cm, tuple(mins), tuple(maxs), within=True)

    def cluster_boxes(self, brush_indices) -> list[tuple[tuple, tuple]]:
        """The (mins, maxs) box of each clip brush in a cluster, for highlighting the
        collision a move or delete will affect."""
        engine = self._clip_engine()
        if engine is None:
            return []
        cm, _ = engine
        out = []
        for i in brush_indices:
            if 0 <= i < cm.num_brushes:
                b = cm.brush(i)
                out.append((b.mins, b.maxs))
        return out

    def managed_cluster(self, key) -> list[int]:
        return list(self._prop_clips.get(key, []))

    def _cluster_for(self, key, footprint) -> list[int]:
        """The clip cluster for a prop: the one the editor manages for it, else the stock
        cluster under its footprint."""
        if key in self._prop_clips:
            return list(self._prop_clips[key])
        if footprint is None:
            return []
        return self.clip_cluster_in_footprint(footprint[0], footprint[1])

    def _note_edited_box(self, mins, maxs) -> None:
        """Record a clip volume a clip edit touched (old or new footprint) for the save-time
        path-link regen. The box is an ``(mins, maxs)`` AABB."""
        self._edited_boxes.append((tuple(float(v) for v in mins), tuple(float(v) for v in maxs)))

    def add_prop_clip(self, key, mins, maxs) -> int:
        """Add a solid clip sized to a newly placed prop's footprint (the contiguous BSP
        path, so a trace hits it) and associate it with the prop. Reversible. This adds
        collision only; ``add_prop`` adds a crate's render static model with it."""
        new = self._commit_clip(
            lambda cm, loc: pc.add_clip_bsp(cm, loc, tuple(mins), tuple(maxs)),
            lambda idx: self._prop_clips.__setitem__(key, [idx]),
            "add prop clip",
        )
        self._note_edited_box(mins, maxs)
        return new

    def add_prop(self, model: str, mins, maxs) -> dict:
        """Place a solid static-model prop (e.g. a crate) in one reversible edit: a clip
        brush sized to its footprint (``add_clip_bsp``, so a trace hits it) AND a render
        static model (a clipMap ``cStaticModel`` plus a GfxWorld draw instance and smodel
        inst, cloned from an existing prop of the same ``model``), the two kept in step the
        way a stock prop carries both. Returns ``{"prop": index, "brush": i, "warnings": [...]}``
        with the new static-model index for the editor to select. Raises ``EditError`` when
        the zone carries no static model named ``model`` or has no editable clipMap."""
        mins = tuple(float(v) for v in mins)
        maxs = tuple(float(v) for v in maxs)
        centre = tuple((mins[k] + maxs[k]) / 2 for k in range(3))
        sm_tmpl = self._clip_smodel_by_model(model)
        gfx_tmpl = self._draw_inst_by_model(model)
        if sm_tmpl is None:
            raise EditError(
                f"add prop: the zone carries no static model named {model!r} to place"
            )
        warnings: list = [] if gfx_tmpl is not None else [_render_absent_notice(model)]

        def op(cm, loc):
            brush = pc.add_clip_bsp(cm, loc, mins, maxs)
            index = self._append_render_smodel(sm_tmpl, gfx_tmpl, centre, mins, maxs)
            return (brush, index)

        brush, index = self._commit_clip(
            op,
            lambda res: self._prop_clips.__setitem__(res[1], [res[0]]),
            "add prop",
            touch_gfx=True,
        )
        self._note_edited_box(mins, maxs)
        return {"prop": index, "brush": brush, "warnings": warnings}

    def move_prop_clip(self, key, delta, footprint=None) -> dict:
        """Move a static-model prop by ``delta``. The render always moves (the clipMap
        ``cStaticModel`` and the matching GfxWorld draw instance + smodel inst), so the model
        follows the gizmo for any prop. When the prop has a clip cluster (the managed one, else
        the stock cluster under ``footprint``) the whole cluster moves with it, so the old spot
        is left clear and ``numBrushes`` is unchanged; when it has none, only the render moves
        and a warning says the baked collision stays. Returns the moved brush indices,
        ``render_moved`` for the clip-less case, and any warnings."""
        cluster = self._cluster_for(key, footprint)
        d = tuple(float(v) for v in delta)
        if not cluster:
            # No clip cluster under the prop (most stock props have none). Move the render so
            # the model follows the gizmo AND add a fresh solid clip at the new footprint, so a
            # prop with no clip of its own becomes solid where it now sits (the bundle move's
            # "solid at the new spot" guarantee). The old baked-triangle collision cannot be
            # relocated or removed here, so it is left behind and we warn. A prop with no render
            # and no footprint has nothing to act on.
            is_prop = isinstance(key, int) and 0 <= key < self._sml_count()
            if not is_prop and footprint is None:
                return {"moved": [], "found": False, "warnings": [_COLLISION_STAYS]}
            added: dict = {}

            def op(cm, loc):
                if is_prop:
                    self._render_shift(key, d)
                brush = None
                if footprint is not None:
                    new_min = tuple(footprint[0][k] + d[k] for k in range(3))
                    new_max = tuple(footprint[1][k] + d[k] for k in range(3))
                    try:
                        brush = pc.add_clip_bsp(cm, loc, new_min, new_max)
                    except pc.ClipError:
                        brush = None  # new spot outside the BSP: move the model, add no clip
                added["brush"] = brush
                return brush

            self._commit_clip(
                op,
                lambda brush: self._prop_clips.__setitem__(key, [brush])
                if brush is not None
                else None,
                "move prop",
                touch_gfx=True,
            )
            brush = added.get("brush")
            if brush is not None and footprint is not None:
                self._note_edited_box(footprint[0], footprint[1])
                new_min = tuple(footprint[0][k] + d[k] for k in range(3))
                new_max = tuple(footprint[1][k] + d[k] for k in range(3))
                self._note_edited_box(new_min, new_max)
            if brush is not None:
                warn = _render_moved_fresh_clip_notice()
            elif footprint is not None:
                warn = _render_moved_no_clip_added_notice()
            else:
                warn = _render_moved_collision_stays()
            return {
                "moved": [brush] if brush is not None else [],
                "found": False,
                "render_moved": is_prop,
                "added_clip": brush,
                "warnings": [warn],
            }
        warnings = [] if key in self._prop_clips else [_baked_shadow_notice("Moving")]
        cm, _ = self._clip_engine()
        old_bounds = pc.cluster_bounds(cm, cluster)

        def op(cm, loc):
            pc.move_cluster_bsp(cm, loc, cluster, d)
            self._render_shift(key, d)
            return None

        self._commit_clip(
            op,
            lambda _r: self._prop_clips.__setitem__(key, list(cluster)),
            "move prop clip",
            touch_gfx=True,
        )
        # the old footprint (now vacated) and the new one both bound links to re-test.
        if old_bounds is not None:
            self._note_edited_box(*old_bounds)
            new_min = tuple(old_bounds[0][k] + d[k] for k in range(3))
            new_max = tuple(old_bounds[1][k] + d[k] for k in range(3))
            self._note_edited_box(new_min, new_max)
        return {"moved": list(cluster), "found": True, "warnings": warnings}

    def rotate_prop_clip(self, key, degrees, footprint=None) -> dict:
        """Rotate the prop about world Z by ``degrees``: its render (the ``cStaticModel``
        ``invScaledAxis`` and the GfxWorld draw-inst axis rows) turns, and each clip brush
        in its cluster becomes the axis-aligned bounding box of its rotated self about the
        cluster centre. A quarter turn (90, 180, 270) swaps the axial extents exactly; any
        other angle keeps the clip as the rotated bounding box and adds a notice, because an
        axial clip cannot represent a slanted box. ``numBrushes`` is unchanged. Returns the
        rotated brush indices and any warnings."""
        cluster = self._cluster_for(key, footprint)
        if not cluster:
            return {"rotated": [], "found": False, "warnings": [_COLLISION_STAYS]}
        from opent5.edit import gizmo as gz

        cm, _ = self._clip_engine()
        bounds = pc.cluster_bounds(cm, cluster)
        centre = tuple((bounds[0][k] + bounds[1][k]) / 2 for k in range(3))
        new_boxes = {
            i: gz.rotated_box_aabb_z(cm.brush(i).mins, cm.brush(i).maxs, centre, degrees)
            for i in cluster
        }
        new_min = tuple(min(b[0][k] for b in new_boxes.values()) for k in range(3))
        new_max = tuple(max(b[1][k] for b in new_boxes.values()) for k in range(3))
        warnings = [] if key in self._prop_clips else [_baked_shadow_notice("Rotating")]
        if not gz.is_axial_turn(degrees):
            warnings.append(_non_axial_clip_notice(degrees))

        def op(cm_, loc):
            for i in cluster:
                mn, mx = new_boxes[i]
                pc.move_clip_bsp(cm_, loc, i, mn, mx)
            self._render_rotate(key, degrees, new_min, new_max)
            return None

        self._commit_clip(
            op,
            lambda _r: self._prop_clips.__setitem__(key, list(cluster)),
            "rotate prop clip",
            touch_gfx=True,
        )
        # the footprint before the turn and the rotated footprint after it.
        self._note_edited_box(bounds[0], bounds[1])
        self._note_edited_box(new_min, new_max)
        return {"rotated": list(cluster), "found": True, "warnings": warnings}

    def remove_prop_clip(self, key, footprint=None) -> dict:
        """Delete a prop: hide its render (a degenerate GfxWorld draw instance that draws
        nothing, reversibly) AND remove its clip cluster. Finds the managed cluster, else the
        stock cluster under ``footprint``. Returns the removed brush indices, whether a render
        was hidden and any warnings; a stock prop warns that its baked shadow stays, and a
        prop with no clip cluster warns that any baked-triangle collision remains."""
        cluster = self._cluster_for(key, footprint)
        is_prop = isinstance(key, int) and 0 <= key < self._sml_count()
        if not cluster and not is_prop:
            return {"removed": [], "found": False, "warnings": [_COLLISION_STAYS]}
        warnings: list = []
        if cluster and key not in self._prop_clips:
            warnings.append(_baked_shadow_notice("Deleting"))
        cm = self._clip_engine()[0] if cluster else None
        old_bounds = pc.cluster_bounds(cm, cluster) if cluster else None

        def op(cm_, loc):
            if cluster:
                pc.remove_cluster(cm_, cluster)
            if is_prop:
                self._render_hide(key)
            return None

        self._commit_clip(
            op,
            lambda _r: self._prop_clips.pop(key, None),
            "remove prop clip",
            touch_gfx=True,
        )
        # the footprint the collision used to fill: links that ran through it may now be clear.
        if old_bounds is not None:
            self._note_edited_box(*old_bounds)
        if not cluster:
            warnings.append(_render_hidden_collision_stays())
        return {
            "removed": list(cluster),
            "found": bool(cluster),
            "render_hidden": is_prop,
            "warnings": warnings,
        }

    # -- individual bundle pieces (the right-click menu drives these) ------------------------

    def move_prop_render(self, key, delta) -> dict:
        """Move ONLY a prop's render by ``delta`` (the model, not its collision). One
        reversible edit. For the menu's "model only" move."""
        d = tuple(float(v) for v in delta)
        if not (isinstance(key, int) and 0 <= key < self._sml_count()):
            return {"render_moved": False, "warnings": [_COLLISION_STAYS]}
        self._commit_clip(
            lambda cm, loc: self._render_shift(key, d), None, "move prop model", touch_gfx=True
        )
        return {"render_moved": True, "warnings": [_baked_shadow_notice("Moving")]}

    def move_prop_cluster(self, key, delta, footprint=None, brushes=None) -> dict:
        """Move ONLY a prop's clip cluster by ``delta`` (the collision, not the render). Uses
        ``brushes`` if given, else the prop's managed/stock cluster. For the menu's "collision
        only" move. Reversible; ``numBrushes`` unchanged."""
        cluster = list(brushes) if brushes is not None else self._cluster_for(key, footprint)
        d = tuple(float(v) for v in delta)
        if not cluster:
            return {"moved": [], "found": False, "warnings": [_COLLISION_STAYS]}
        cm, _ = self._clip_engine()
        old_bounds = pc.cluster_bounds(cm, cluster)
        def _assoc(_r):
            if key is not None:
                self._prop_clips[key] = list(cluster)

        self._commit_clip(
            lambda cm_, loc: (pc.move_cluster_bsp(cm_, loc, cluster, d), None)[1],
            _assoc,
            "move clip",
        )
        if old_bounds is not None:
            self._note_edited_box(*old_bounds)
            self._note_edited_box(
                tuple(old_bounds[0][k] + d[k] for k in range(3)),
                tuple(old_bounds[1][k] + d[k] for k in range(3)),
            )
        return {"moved": list(cluster), "found": True, "warnings": []}

    def remove_prop_render(self, key) -> dict:
        """Hide ONLY a prop's render (the model), leaving its collision. One reversible edit.
        For the menu's "model only" delete."""
        if not (isinstance(key, int) and 0 <= key < self._sml_count()):
            return {"render_hidden": False, "warnings": []}
        self._commit_clip(
            lambda cm, loc: self._render_hide(key), None, "hide prop model", touch_gfx=True
        )
        return {"render_hidden": True, "warnings": [_baked_shadow_notice("Deleting")]}

    def remove_clips(self, indices, key=None) -> dict:
        """Remove a given set of clip brushes (disable them in place, drop their leaf
        references), reversibly. For the menu's "collision only" delete, for deleting the
        individual clip brushes picked in the collision view, and for removing a leftover
        invisible wall that belongs to no prop. ``key`` (when the brushes are a prop's cluster)
        is dropped from the managed associations."""
        engine = self._clip_engine()
        if engine is None:
            return {"removed": [], "warnings": [self.doc.zone_name + ": no editable clipMap"]}
        cm, _ = engine
        indices = [int(i) for i in indices if 0 <= int(i) < cm.num_brushes]
        if not indices:
            return {"removed": [], "warnings": []}
        old_bounds = pc.cluster_bounds(cm, indices)
        self._commit_clip(
            lambda cm_, loc: (pc.remove_cluster(cm_, indices), None)[1],
            (lambda _r: self._prop_clips.pop(key, None)) if key is not None else None,
            "remove clip",
        )
        if old_bounds is not None:
            self._note_edited_box(*old_bounds)
        return {"removed": list(indices), "warnings": []}

    # -- collision brushes for the collision view --------------------------------------------

    def collision_brushes(self, families=("clip",)) -> list:
        """The clipMap's clip brushes for the collision view to show, pick and delete: a list
        of ``(index, mins, maxs, contents, family)`` for each brush whose contents family
        (``opent5.convert.propclip.contents_family``: 'clip', 'solid' or 'other') is in
        ``families``. A disabled brush (contents 0, already removed) is skipped. Empty when the
        zone has no editable clipMap. These are the invisible walls the user sees and can
        remove with ``remove_clips``; the baked-triangle collision is separate and is marked
        un-removable in the UI."""
        engine = self._clip_engine()
        if engine is None:
            return []
        cm, _ = engine
        want = set(families) if families else None
        out = []
        for i in range(cm.num_brushes):
            b = cm.brush(i)
            if b.contents == 0:
                continue
            fam = pc.contents_family(b.contents)
            if want is not None and fam not in want:
                continue
            out.append((i, b.mins, b.maxs, b.contents, fam))
        return out

    # -- static-model render (cStaticModel + GfxWorld draw inst + smodel inst) ----------------

    def _sml_count(self) -> int:
        data = self._clip_node.get("static_model_list") if self._clip_node else None
        return 0 if not data else len(data) // _SM_SIZE

    def _clip_smodel_by_model(self, model: str) -> int | None:
        for p in self.static_models():
            if p.model == model:
                return p.index
        return None

    def _draw_insts(self) -> list | None:
        if self._gfx_node is None:
            return None
        return self._gfx_node.get("smodel_draw_insts")

    def _draw_raw(self, j: int) -> bytes:
        e = self._draw_insts()[j]
        return bytes(e["raw"] if isinstance(e, dict) else e)

    def _draw_inst_by_model(self, model: str) -> int | None:
        di = self._draw_insts()
        if not di:
            return None
        for j, e in enumerate(di):
            node = e.get("model") if isinstance(e, dict) else None
            name = node.get("name") if isinstance(node, dict) else getattr(node, "name", None)
            if name == model:
                return j
        return None

    def has_draw_inst(self, model: str) -> bool:
        """Whether the zone carries a GfxWorld draw instance for ``model`` to clone, so an
        added copy will draw. A model without one can be given collision but will not render,
        so the Add picker offers only models that have one."""
        return self._draw_inst_by_model(model) is not None

    def _draw_inst_at_origin(self, origin, tol: float = 0.5) -> int | None:
        di = self._draw_insts()
        if not di:
            return None
        for j in range(len(di)):
            org = struct.unpack_from(">3f", self._draw_raw(j), _DRAW_ORIGIN)
            if all(abs(org[k] - origin[k]) < tol for k in range(3)):
                return j
        return None

    def _draw_inst_name(self, j: int) -> str | None:
        e = self._draw_insts()[j]
        node = e.get("model") if isinstance(e, dict) else None
        return node.get("name") if isinstance(node, dict) else getattr(node, "name", None)

    def _draw_inst_origin(self, j: int):
        return struct.unpack_from(">3f", self._draw_raw(j), _DRAW_ORIGIN)

    def _prop_draw_map(self) -> dict[int, int]:
        """A stable mapping from a clipMap static-model prop index to its GfxWorld draw
        instance, matched by model name AND origin so a prop that shares a spot with a
        different-model neighbour links to its OWN render, not the neighbour's. Origin-only
        matching (the old ``_draw_inst_at_origin`` within 0.5 units) silently linked such
        props to the wrong draw instance, which is why moving some props moved nothing or the
        wrong model. Each draw instance is claimed by at most one prop. Built once and cached;
        a prop with no matching draw instance (e.g. a scripted vehicle) is absent, so its
        render simply does not move. The indices stay valid across moves and rotations (they
        rewrite records in place), so the map is dropped only when a prop or draw instance is
        added or an undo/redo changes the set."""
        if self._prop_draw is not None:
            return self._prop_draw
        mapping: dict[int, int] = {}
        di = self._draw_insts()
        if not di:
            self._prop_draw = mapping
            return mapping
        by_model: dict[Any, list[int]] = {}
        for j in range(len(di)):
            by_model.setdefault(self._draw_inst_name(j), []).append(j)
        claimed: set[int] = set()
        tol2 = _LINK_TOL * _LINK_TOL
        for p in self.static_models():
            best, best_d = None, tol2
            for j in by_model.get(p.model, ()):
                if j in claimed:
                    continue
                o = self._draw_inst_origin(j)
                d = sum((o[k] - p.origin[k]) ** 2 for k in range(3))
                if d <= best_d:
                    best, best_d = j, d
            if best is not None:
                claimed.add(best)
                mapping[p.index] = best
        self._prop_draw = mapping
        return mapping

    def _draw_inst_for(self, prop_index) -> int | None:
        """The GfxWorld draw instance a prop's render lives in, through the stable link."""
        if not isinstance(prop_index, int):
            return None
        return self._prop_draw_map().get(prop_index)

    def _set_draw_inst(self, j: int, raw: bytes) -> None:
        """Replace draw-inst ``j`` with a fresh element (never mutate in place, so the clip
        snapshot's shallow list copy restores exactly)."""
        di = self._draw_insts()
        old = di[j]
        if isinstance(old, dict):
            di[j] = {"_t": old.get("_t", "GfxStaticModelDrawInst"), "raw": raw,
                     "model": old.get("model")}  # fmt: skip
        else:
            di[j] = raw

    def _shift_inst_bounds(self, j: int, d) -> None:
        insts = self._gfx_node.get("smodel_insts")
        if not insts or (j + 1) * _INST_SIZE > len(insts):
            return
        ins = bytearray(insts)
        base = j * _INST_SIZE
        for off in (_INST_MINS, _INST_MAXS):
            v = struct.unpack_from(">3f", ins, base + off)
            struct.pack_into(">3f", ins, base + off, *(v[k] + d[k] for k in range(3)))
        self._gfx_node["smodel_insts"] = bytes(ins)

    def _render_shift(self, prop_index, d) -> bool:
        """Shift a static-model prop's render by ``d``: the clipMap ``cStaticModel`` origin
        and bounds, plus the GfxWorld draw instance (through the stable prop->draw-inst link)
        and the parallel smodel inst bounds. Returns True when a render record was found. Node
        edits only; the commit snapshots and marks the assets."""
        if not isinstance(prop_index, int) or not (0 <= prop_index < self._sml_count()):
            return False
        d = tuple(float(v) for v in d)
        # Resolve the link BEFORE moving the cStaticModel origin: the link map is built lazily
        # by matching prop origins against draw-inst origins, and a first lookup after the
        # origin had already shifted would match nothing (the draw insts are still at the old
        # spots). Building it here keeps the two tables' origins consistent at map-build time.
        j = self._draw_inst_for(prop_index)
        sml = bytearray(self._clip_node["static_model_list"])
        o = prop_index * _SM_SIZE
        for off in (_SM_ORIGIN, _SM_ABSMIN, _SM_ABSMAX):
            v = struct.unpack_from(">3f", sml, o + off)
            struct.pack_into(">3f", sml, o + off, *(v[k] + d[k] for k in range(3)))
        self._clip_node["static_model_list"] = bytes(sml)
        if j is not None:
            raw = bytearray(self._draw_raw(j))
            org = struct.unpack_from(">3f", raw, _DRAW_ORIGIN)
            struct.pack_into(">3f", raw, _DRAW_ORIGIN, *(org[k] + d[k] for k in range(3)))
            self._set_draw_inst(j, bytes(raw))
            self._shift_inst_bounds(j, d)
            self._gfx_touched = True
        return True

    def _render_hide(self, prop_index) -> bool:
        """Hide a prop's render by zeroing its GfxWorld draw-inst scale: a degenerate
        placement that draws nothing (the viewer skips scale-0 placements). Reversible through
        the clip snapshot. Returns True when a draw instance was found and hidden. The clipMap
        ``cStaticModel`` is left as it is, so the clip removal is the collision side."""
        if not isinstance(prop_index, int) or not (0 <= prop_index < self._sml_count()):
            return False
        j = self._draw_inst_for(prop_index)
        if j is None:
            return False
        raw = bytearray(self._draw_raw(j))
        struct.pack_into(">f", raw, _DRAW_SCALE, 0.0)
        self._set_draw_inst(j, bytes(raw))
        self._gfx_touched = True
        return True

    def _render_rotate(self, prop_index, degrees, new_mins, new_maxs) -> bool:
        """Turn a prop's render about world Z by ``degrees`` (the ``cStaticModel``
        ``invScaledAxis`` and the GfxWorld draw-inst CMP axis rows) and reset its render
        bounds to ``new_mins``/``new_maxs`` (the rotated cluster footprint). The exact
        in-engine orientation is a device check; the collision is the clip."""
        if not isinstance(prop_index, int) or not (0 <= prop_index < self._sml_count()):
            return False
        from opent5.edit import gizmo as gz

        yaw = gz.yaw_matrix(degrees)
        sml = bytearray(self._clip_node["static_model_list"])
        o = prop_index * _SM_SIZE
        inv = np.array(struct.unpack_from(">9f", sml, o + _SM_INVAXIS), np.float64).reshape(3, 3)
        # invScaledAxis is the inverse of the model->world axis, so it turns by -degrees.
        inv_rot = inv @ gz.yaw_matrix(-degrees)
        struct.pack_into(">9f", sml, o + _SM_INVAXIS, *(float(v) for v in inv_rot.reshape(-1)))
        struct.pack_into(">3f", sml, o + _SM_ABSMIN, *new_mins)
        struct.pack_into(">3f", sml, o + _SM_ABSMAX, *new_maxs)
        self._clip_node["static_model_list"] = bytes(sml)
        j = self._draw_inst_for(prop_index)
        if j is not None:
            from opent5.convert.world import cmp_pack
            from opent5.xfile.schema import unpack_cmp

            raw = bytearray(self._draw_raw(j))
            words = [struct.unpack_from(">I", raw, _DRAW_AXIS + 4 * k)[0] for k in range(3)]
            axes = np.array([unpack_cmp(w) for w in words], np.float64)
            packed = cmp_pack(axes @ yaw)
            for k in range(3):
                struct.pack_into(">I", raw, _DRAW_AXIS + 4 * k, int(packed[k]))
            self._set_draw_inst(j, bytes(raw))
            # reset the inst bounds to the rotated footprint (absolute, not a shift).
            insts = self._gfx_node.get("smodel_insts")
            if insts and (j + 1) * _INST_SIZE <= len(insts):
                ins = bytearray(insts)
                struct.pack_into(">3f", ins, j * _INST_SIZE + _INST_MINS, *new_mins)
                struct.pack_into(">3f", ins, j * _INST_SIZE + _INST_MAXS, *new_maxs)
                self._gfx_node["smodel_insts"] = bytes(ins)
            self._gfx_touched = True
        return True

    def _append_render_smodel(self, sm_tmpl, gfx_tmpl, centre, mins, maxs) -> int:
        """Append a render static model cloned from the templates: the clipMap
        ``cStaticModel`` (so it is a listed, selectable prop with a footprint) and, when a
        GfxWorld draw-inst template is given, the GfxWorld draw instance + smodel inst so it
        draws. Returns the new static-model index."""
        data = bytearray(self._clip_node.get("static_model_list") or b"")
        elem = bytearray(self._sml_elem(sm_tmpl))
        struct.pack_into(">3f", elem, _SM_ORIGIN, *centre)
        struct.pack_into(">3f", elem, _SM_ABSMIN, *mins)
        struct.pack_into(">3f", elem, _SM_ABSMAX, *maxs)
        data += elem
        self._clip_node["static_model_list"] = bytes(data)
        count = len(data) // _SM_SIZE
        h = bytearray(self._clip_node["header"])
        struct.pack_into(">I", h, _CLIP_NUM_SMODELS, count)
        self._clip_node["header"] = bytes(h)
        new_index = count - 1
        if gfx_tmpl is not None:
            di = self._draw_insts()
            raw = bytearray(self._draw_raw(gfx_tmpl))
            struct.pack_into(">3f", raw, _DRAW_ORIGIN, *centre)
            tmpl = di[gfx_tmpl]
            di.append({"_t": "GfxStaticModelDrawInst", "raw": bytes(raw),
                       "model": tmpl.get("model") if isinstance(tmpl, dict) else None})  # fmt: skip
            insts = self._gfx_node.get("smodel_insts")
            if insts and (gfx_tmpl + 1) * _INST_SIZE <= len(insts):
                ins = bytearray(insts)
                new_inst = bytearray(ins[gfx_tmpl * _INST_SIZE : (gfx_tmpl + 1) * _INST_SIZE])
                struct.pack_into(">3f", new_inst, _INST_MINS, *mins)
                struct.pack_into(">3f", new_inst, _INST_MAXS, *maxs)
                ins += new_inst
                self._gfx_node["smodel_insts"] = bytes(ins)
            gh = bytearray(self._gfx_node["header"])
            struct.pack_into(
                ">I", gh, _GFX_SMODEL_COUNT, len(self._gfx_node["smodel_insts"]) // _INST_SIZE
            )
            self._gfx_node["header"] = bytes(gh)
            self._gfx_touched = True
        # a prop and its draw instance were appended: drop the link map so it rebuilds over
        # the new set (the appended draw instance sits at the centre, so it re-links cleanly).
        self._prop_draw = None
        return new_index

    def _sml_elem(self, i: int) -> bytes:
        data = self._clip_node["static_model_list"]
        return bytes(data[i * _SM_SIZE : (i + 1) * _SM_SIZE])

    # -- the clip reversibility machinery ----------------------------------------------------

    def _capture_clip(self, cm) -> dict:
        node = cm.node
        snap = {k: node.get(k) for k in self._CLIP_KEYS}  # bytes: immutable, cheap to hold
        snap["leafbrush_nodes"] = copy.deepcopy(node["leafbrush_nodes"])
        snap["_mine"] = set(cm._mine)
        snap["_padded"] = set(cm._padded)
        snap["_append"] = set(self.doc._rw.append_identities)
        # the GfxWorld render side (origin, axes, bounds) a prop move / rotate / add changes
        # alongside the clip. The draw-inst list is never mutated in place (elements are
        # replaced), so a shallow copy restores it exactly; the inst bytes and header are
        # immutable.
        if self._gfx_node is not None:
            snap["_gfx_header"] = self._gfx_node.get("header")
            snap["_gfx_insts"] = self._gfx_node.get("smodel_insts")
            draws = self._gfx_node.get("smodel_draw_insts")
            snap["_gfx_draws"] = list(draws) if draws is not None else None
        return snap

    def _restore_clip(self, snap: dict) -> None:
        engine = self._clip_engine()
        if engine is None:  # pragma: no cover - only reached with a clip edit in history
            raise EditError("clip edit cannot be undone: the clipMap is no longer available")
        cm, _ = engine
        node = cm.node
        for k in self._CLIP_KEYS:
            node[k] = snap[k]
        node["leafbrush_nodes"] = copy.deepcopy(snap["leafbrush_nodes"])
        cm._mine = set(snap["_mine"])
        cm._padded = set(snap["_padded"])
        self.doc._rw.append_identities = set(snap["_append"])
        if self._gfx_node is not None and "_gfx_header" in snap:
            self._gfx_node["header"] = snap["_gfx_header"]
            self._gfx_node["smodel_insts"] = snap["_gfx_insts"]
            draws = snap["_gfx_draws"]
            self._gfx_node["smodel_draw_insts"] = list(draws) if draws is not None else None

    def _commit_clip(self, op_fn, assoc_fn, label: str, touch_gfx: bool = False):
        engine = self._clip_engine()
        if engine is None:
            raise EditError(
                f"{self.doc.zone_name}: this zone has no editable clipMap collision"
            )
        cm, loc = engine
        before = self._capture_clip(cm)
        assoc_before = {k: list(v) for k, v in self._prop_clips.items()}
        self._gfx_touched = False
        try:
            result = op_fn(cm, loc)
        except pc.ClipError as exc:
            self._restore_clip(before)
            raise EditError(f"{self.doc.zone_name}: clip edit failed ({exc})") from None
        if assoc_fn is not None:
            assoc_fn(result)
        after = self._capture_clip(cm)
        edit = ClipEdit(
            before,
            after,
            assoc_before,
            {k: list(v) for k, v in self._prop_clips.items()},
            label=label,
        )
        before_ops = len(self.doc._undo)
        self.doc.touch_asset(self._clip_index)
        # the render edit lives in the GfxWorld asset, a separate node, so mark it too when a
        # prop's render moved; its bytes are in the same ClipEdit snapshot.
        if touch_gfx and self._gfx_touched and self._gfx_index is not None:
            self.doc.touch_asset(self._gfx_index)
        ops = len(self.doc._undo) - before_ops
        self._undo.append(_Entry(edit, ops))
        self._redo.clear()
        return result

    # -- applying, reconciling, history ------------------------------------------------------

    def _apply(self, edit: Edit) -> None:
        mergeable = (
            edit.merge_key is not None
            and self._undo
            and self._undo[-1].edit.merge_key == edit.merge_key
        )
        if mergeable:
            top = self._undo.pop()
            for _ in range(top.ops):
                self.doc.undo()
            top.edit.revert(self)
            # carry the drag's original "before" so one undo restores the start
            if isinstance(edit, _KeyEdit) and isinstance(top.edit, _KeyEdit):
                edit.before = top.edit.before
        edit.apply(self)
        ops = self._reconcile()
        self._undo.append(_Entry(edit, ops))
        self._redo.clear()

    def _reconcile(self) -> int:
        """Bring the document's nodes in step with the model; return how many document
        operations it took (so undo/redo can match them)."""
        before = len(self.doc._undo)
        self._reconcile_entities()
        self._reconcile_sun()
        return len(self.doc._undo) - before

    def _render_entities(self) -> str:
        current = [o.keys for o in self._objects]
        if current == self._orig_keys:
            return self._orig_text
        return entity_text(current, self._newline)

    def _reconcile_entities(self) -> None:
        want = self._render_entities()
        if want != self.doc.text(self._ent_key):
            self.doc.set_text(self._ent_key, want)

    def _reconcile_sun(self) -> None:
        if self._gfx_index is None:
            return
        for field_name, value in (("color", self._sun_color), ("dir", self._sun_dir)):
            if value is None:
                continue
            path = f"sun_light/{field_name}"
            current = self.doc.field_info(self._gfx_index, path)["value"]
            if current is None:
                continue
            want = tuple(float(v) for v in value)
            if tuple(float(v) for v in current) == want:
                continue
            self.doc.set_field(self._gfx_index, path, list(want))

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def dirty(self) -> bool:
        return bool(self._undo)

    def undo(self) -> Edit | None:
        if not self._undo:
            return None
        entry = self._undo.pop()
        for _ in range(entry.ops):
            self.doc.undo()
        entry.edit.revert(self)
        self._prop_draw = None  # an add/delete may be undone; rebuild the link over the set
        self._redo.append(entry)
        return entry.edit

    def redo(self) -> Edit | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        entry.edit.apply(self)
        for _ in range(entry.ops):
            self.doc.redo()
        self._prop_draw = None
        self._undo.append(entry)
        return entry.edit

    def history(self) -> list[str]:
        return [e.edit.label for e in self._undo]

    # -- gametype rules ----------------------------------------------------------------------

    def _cmodels(self):
        if self._clip_node is None:
            return None
        return ent.cmodel_bounds(self._clip_node)

    def _mapname(self) -> str:
        if self._gfx_node is not None:
            return self._gfx_node.get("base_name") or ""
        return ""

    def _gametype_report(self, key_dicts: list[dict]) -> dict:
        xmodels = {
            a.name for a in self.doc.assets if a.type == T.XMODEL and a.name
        }
        return ent.report(key_dicts, ent.GAMETYPES, self._cmodels(), xmodels, self._mapname())

    def gametype_report(self) -> dict:
        """``opent5.convert.entities.report`` for the current entities."""
        return self._gametype_report([o.keys for o in self._objects])

    def gametype_problems(self, require: tuple[str, ...] = ()) -> list[str]:
        """Why a save would be refused on the gametype rules: any gametype that worked before
        and would not now, plus any explicitly required one that is not ready."""
        report = self.gametype_report()
        must = set(self._ready_before) | set(require)
        problems: list[str] = []
        for g in sorted(must):
            r = report.get(g, {"ready": False, "missing": [f"{g}: unknown gametype"]})
            if not r["ready"]:
                for m in r.get("missing", []):
                    problems.append(f"gametype {g}: {m}")
        return problems

    # -- building and saving -----------------------------------------------------------------

    def _regen_paths(self) -> dict | None:
        """Edit-relative path-link regeneration, run at save once clip edits exist. Keeps the
        map's existing links, re-tests only the links crossing an edited clip volume against
        the current collision, and drops those now blocked. Raises ``EditError`` (writing
        nothing) when the edit itself has walled off a region that was connected before;
        pre-existing fragmentation under our stricter-than-engine model is returned as a
        warning, not a failure. Returns the regen report, or ``None`` when there is nothing to
        do (no path nodes, no clipMap, or no clip edit). The game node's link bytes are a
        reversible edit: snapshotted here so a refused save restores them exactly, and the
        asset is marked touched only when a link was actually dropped."""
        if self._game_node is None or not self._edited_boxes:
            return None
        engine = self._clip_engine()
        if engine is None:
            return None
        cm, loc = engine
        # snapshot every node's (raw, links) so a refused save leaves the graph untouched.
        nodes = self._game_node.get("nodes") or []
        snapshot = [(el["raw"], el.get("links")) for el in nodes]
        try:
            report = pathregen.regenerate_local_or_raise(
                self._game_node, cm, loc, self._edited_boxes
            )
        except pathregen.PathRegenError as exc:
            for el, (raw, links) in zip(nodes, snapshot, strict=True):
                el["raw"] = raw
                el["links"] = links
            raise EditError(
                f"refusing to write: {exc} Nothing was written; move or remove the prop so a "
                "clear path link still bridges the two sides, or add a path node."
            ) from None
        if report["removed"] and self._game_index is not None:
            # the regen changed link bytes in place, so the game asset must be re-laid out
            # and verified on this save (the clip edits touch their own assets separately).
            self.doc.touch_asset(self._game_index)
        return report

    def build(self, progress=None) -> bytes:
        return self.doc.build(progress=progress)

    def default_save_path(self) -> Path | None:
        if self.doc.path is None:
            return None
        return self.doc.path.with_suffix("").with_suffix(".edited.ff")

    def save(
        self,
        new_path: str | Path | None = None,
        verify: bool = True,
        require: tuple[str, ...] = (),
        progress=None,
    ) -> SaveReport:
        """Write a new file. Refuses (writing nothing) when a gametype that worked before
        would break, or any explicitly ``require``-d one is not ready; then runs the
        document's own verify. ``new_path`` defaults to ``<name>.edited.ff``."""
        if new_path is None:
            new_path = self.default_save_path()
            if new_path is None:
                raise EditError("save: no path given and the zone was opened from bytes")
        problems = self.gametype_problems(require)
        if problems:
            raise EditError(
                "refusing to write: the edited entities break a gametype that worked before "
                "(nothing written): " + "; ".join(problems)
            )
        # edit-relative path-link regen: a new split refuses the save, pre-existing
        # fragmentation is a warning only. Runs before the build so the written file carries
        # the dropped links.
        regen = self._regen_paths()
        report = self.doc.save(new_path, verify=verify, progress=progress)
        if regen is not None:
            # a warning lives in details, never in report.problems (which signals a verify
            # failure and drives report.verified), so a benign warning never looks like one.
            warnings: list[str] = []
            if regen["preexisting_fragmentation"]:
                warnings.append(
                    "the map already read as "
                    f"{regen['before_components']} disconnected path region(s) before this "
                    "edit (our line-of-sight model is stricter than the engine's, which loads "
                    "it anyway). The edit did not make it worse, so the save was allowed."
                )
            report.details["path_regen"] = {**regen, "warnings": warnings}
        return report
