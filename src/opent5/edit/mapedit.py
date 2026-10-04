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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from opent5.convert import entities as ent
from opent5.convert import propclip as pc
from opent5.convert.scripts import entity_text, parse_entities
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
        #: prop key -> the clip brush indices the editor manages for it (editor-added or moved).
        self._prop_clips: dict[Any, list[int]] = {}
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
    _CLIP_KEYS = ("header", "brushes", "leafs", "leafbrushes", "brush_verts")

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

    def add_prop_clip(self, key, mins, maxs) -> int:
        """Add a solid clip sized to a newly placed prop's footprint (the contiguous BSP
        path, so a trace hits it) and associate it with the prop. Reversible."""
        new = self._commit_clip(
            lambda cm, loc: pc.add_clip_bsp(cm, loc, tuple(mins), tuple(maxs)),
            lambda idx: self._prop_clips.__setitem__(key, [idx]),
            "add prop clip",
        )
        return new

    def move_prop_clip(self, key, delta, footprint=None) -> dict:
        """Move the prop's existing clip cluster by ``delta`` (the whole cluster moves,
        so the old spot is left clear and ``numBrushes`` is unchanged). Finds the managed
        cluster, else the stock cluster under ``footprint``. Returns the moved brush
        indices and any warnings; when no cluster is found it moves nothing and reports
        that the collision stays."""
        cluster = self._cluster_for(key, footprint)
        if not cluster:
            return {"moved": [], "found": False, "warnings": [_COLLISION_STAYS]}
        warnings = [] if key in self._prop_clips else [_baked_shadow_notice("Moving")]
        d = tuple(float(v) for v in delta)
        self._commit_clip(
            lambda cm, loc: pc.move_cluster_bsp(cm, loc, cluster, d),
            lambda _r: self._prop_clips.__setitem__(key, list(cluster)),
            "move prop clip",
        )
        return {"moved": list(cluster), "found": True, "warnings": warnings}

    def remove_prop_clip(self, key, footprint=None) -> dict:
        """Remove (soft-disable) the prop's existing clip cluster. Finds the managed
        cluster, else the stock cluster under ``footprint``. Returns the removed brush
        indices and any warnings; when none is found it removes nothing and reports that
        the collision stays."""
        cluster = self._cluster_for(key, footprint)
        if not cluster:
            return {"removed": [], "found": False, "warnings": [_COLLISION_STAYS]}
        warnings = [] if key in self._prop_clips else [_baked_shadow_notice("Deleting")]
        self._commit_clip(
            lambda cm, loc: pc.remove_cluster(cm, cluster),
            lambda _r: self._prop_clips.pop(key, None),
            "remove prop clip",
        )
        return {"removed": list(cluster), "found": True, "warnings": warnings}

    # -- the clip reversibility machinery ----------------------------------------------------

    def _capture_clip(self, cm) -> dict:
        node = cm.node
        snap = {k: node.get(k) for k in self._CLIP_KEYS}  # bytes: immutable, cheap to hold
        snap["leafbrush_nodes"] = copy.deepcopy(node["leafbrush_nodes"])
        snap["_mine"] = set(cm._mine)
        snap["_padded"] = set(cm._padded)
        snap["_append"] = set(self.doc._rw.append_identities)
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

    def _commit_clip(self, op_fn, assoc_fn, label: str):
        engine = self._clip_engine()
        if engine is None:
            raise EditError(
                f"{self.doc.zone_name}: this zone has no editable clipMap collision"
            )
        cm, loc = engine
        before = self._capture_clip(cm)
        assoc_before = {k: list(v) for k, v in self._prop_clips.items()}
        try:
            result = op_fn(cm, loc)
        except pc.ClipError as exc:
            self._restore_clip(before)
            raise EditError(f"{self.doc.zone_name}: clip edit failed ({exc})") from None
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
        self._redo.append(entry)
        return entry.edit

    def redo(self) -> Edit | None:
        if not self._redo:
            return None
        entry = self._redo.pop()
        entry.edit.apply(self)
        for _ in range(entry.ops):
            self.doc.redo()
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
        return self.doc.save(new_path, verify=verify, progress=progress)
