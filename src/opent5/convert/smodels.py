"""Static models (misc_model props): PC GfxWorld static model arrays -> PS3, and the
clipMap's static model list re-pointed at the base zone's XModels.

Layouts (checked on PC and PS3 mp_nuked, 4209 static models, ``compare_static_models``;
docs/convert.md section 12):

- GfxStaticModelInst (0x28: mins, maxs, lightingOrigin, groundLighting): the same on both
  platforms, swapped. groundLighting is identical in all 4209; the floats differ in their
  low bits where the PS3 linker recomputed them from its own model bounds.
- GfxStaticModelDrawInst: PC 0x4c, PS3 0x2c. PS3 = cullDist, origin (PC +0x0 .. +0x10),
  the three axis rows as CMP 11:11:10 words (PC 3 x 3 f32 at +0x10, normalised and
  rounded as ``world.cmp_pack``: 12627 of 12627 words identical), scale (PC +0x34), model
  (an XModel alias), flags (PC +0x3c), lightingHandle u16, reflectionProbeIndex,
  primaryLightIndex (PC +0x48 .. +0x4c). PC smodelCacheIndex[4] (+0x40) is dropped.
- the GfxWorld header counts (dpvs.smodelCount, smodelVisDataCount, ...) are PC words
  moved like the rest of the header (``world.gfx_header``); the cells' aabb trees and the
  shadow geometry carry static model indexes and are swapped by ``world.convert_gfx``.
- clipMap staticModelList (cStaticModel_s 0x50): swapped by ``world.convert_clip``; its +0x4
  XModel alias is replaced here by the base's.

The models themselves are not converted for a map built from stock props: every model
must already be an XModel of the base zone (its alias word is reused, as materials are,
docs/convert.md 3.4). A model the base lacks stops the conversion with its name.

Lighting the models get (INFERRED from the fields, not read in the renderer): cod2rad
writes each instance's lightingOrigin and groundLighting colour (GfxStaticModelInst) and
its lightingHandle, reflection probe and primary light (draw instance); they are carried
from the PC map unchanged.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from opent5.convert.swap import swap_struct
from opent5.convert.world import ConvertError, cmp_pack
from opent5.convert.xmodel import asset_words

PC_DRAW_INST = 0x4C
PS3_DRAW_INST = 0x2C
STATIC_MODEL = 0x50
INST = 0x28
#: Splice sources (``splice.BASE``).
BASE = "base"


def ps3_draw_inst(pc_raw: bytes, model_word: bytes) -> bytes:
    """One PS3 GfxStaticModelDrawInst (0x2c) from a PC one (0x4c, little-endian) and the
    base zone's alias word for its model."""
    if len(pc_raw) != PC_DRAW_INST:
        raise ConvertError(f"PC draw inst: expected {PC_DRAW_INST} bytes, found {len(pc_raw)}")
    head = struct.unpack_from("<4f", pc_raw, 0)
    axes = np.array(struct.unpack_from("<9f", pc_raw, 0x10), np.float64).reshape(3, 3)
    scale, flags = (
        struct.unpack_from("<f", pc_raw, 0x34)[0],
        struct.unpack_from("<i", pc_raw, 0x3C)[0],
    )
    handle = struct.unpack_from("<H", pc_raw, 0x48)[0]
    out = struct.pack(">4f", *head)
    out += cmp_pack(axes).tobytes()
    out += struct.pack(">f", scale) + model_word + struct.pack(">iH", flags, handle)
    out += pc_raw[0x4A:0x4C]
    return out


def model_words(base_xfile) -> dict[str, tuple[bytes, Any]]:
    """XModel name -> (alias word, node) for every XModel the base zone holds behind an
    alias slot (the top-level xmodel assets and models loaded with -2)."""
    return asset_words(base_xfile, "XModel")


def _model_name(link: Any) -> str | None:
    if isinstance(link, dict):
        return link.get("name")
    return getattr(link, "name", None)


@dataclass
class Taken:
    """The PC GfxWorld's static model arrays, removed so that ``world.convert_gfx`` (which
    refuses them) converts the rest."""

    insts: bytes | None
    draw_insts: list[dict] | None


def take(pc_gfx: dict) -> Taken:
    """Remove the static model arrays from a parsed PC GfxWorld node (before
    ``world.convert_gfx``)."""
    taken = Taken(pc_gfx.get("smodel_insts"), pc_gfx.get("smodel_draw_insts"))
    pc_gfx["smodel_insts"] = None
    pc_gfx["smodel_draw_insts"] = None
    return taken


@dataclass
class StaticModelResult:
    #: Element nodes for GfxWorld "smodel_draw_insts" (their +0x20 words are base values).
    draw_insts: list[dict] | None
    #: Bytes for GfxWorld "smodel_insts".
    insts: bytes | None
    #: Model name per instance, and the count of each.
    names: list[str] = field(default_factory=list)
    report: dict = field(default_factory=dict)


def convert_static_models(
    taken: Taken, words: dict[str, tuple[bytes, Any]], pc_xfile=None
) -> StaticModelResult:
    """PS3 static model arrays from the PC ones; ``words`` from ``model_words(base)``."""
    draws = taken.draw_insts or []
    names: list[str] = []
    out: list[dict] = []
    missing: set[str] = set()
    for i, element in enumerate(draws):
        name = _model_name(element.get("model"))
        if name is None and pc_xfile is not None:
            raw = struct.unpack_from("<I", bytes(element["raw"]), 0x38)[0]
            target = pc_xfile.resolve(raw)
            name = _model_name(target.asset if target is not None else None)
        if name is None:
            raise ConvertError(f"PC static model {i}: its XModel has no name")
        names.append(name)
        if name not in words:
            missing.add(name)
            continue
        word, node = words[name]
        out.append(
            {
                "_t": "GfxStaticModelDrawInst",
                "raw": ps3_draw_inst(bytes(element["raw"]), word),
                "model": node,
            }
        )
    if missing:
        raise ConvertError(
            f"static models {sorted(missing)}: not XModels of the base zone; the converter "
            "places the base's models by name (docs/convert.md 12)"
        )
    insts = swap_struct("GfxStaticModelInst", taken.insts) if taken.insts else None
    if (insts is not None and len(insts) // INST != len(out)) or (insts is None and out):
        raise ConvertError(
            f"static models: {len(out)} draw instances, "
            f"{0 if insts is None else len(insts) // INST} instances"
        )
    counts: dict[str, int] = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    return StaticModelResult(
        out or None, insts, names, {"count": len(names), "models": dict(sorted(counts.items()))}
    )


def repoint_clip(clip: dict, words: dict[str, tuple[bytes, Any]], pc_xfile) -> list[str]:
    """Replace the +0x4 XModel alias of every clipMap static model (after
    ``world.convert_clip`` swapped the list) with the base's word; returns the names."""
    data = clip.get("static_model_list")
    if not data:
        return []
    data = bytearray(data)
    names = []
    for k in range(len(data) // STATIC_MODEL):
        at = STATIC_MODEL * k + 4
        raw = struct.unpack_from(">I", data, at)[0]
        target = pc_xfile.resolve(raw)
        name = _model_name(target.asset if target is not None else None)
        if name is None or name not in words:
            raise ConvertError(
                f"clipMap static model {k}: XModel {name!r} is not an XModel of the base zone"
            )
        data[at : at + 4] = words[name][0]
        names.append(name)
    clip["static_model_list"] = bytes(data)
    return names


def place(gfx: dict, clip: dict, result: StaticModelResult, splice) -> None:
    """Put the converted arrays into the converted GfxWorld node and register the nodes
    whose pointer words are base values with the ``Splice``."""
    gfx["smodel_insts"] = result.insts
    gfx["smodel_draw_insts"] = result.draw_insts
    for element in result.draw_insts or ():
        splice.origin(element, BASE)
    data = clip.get("static_model_list")
    if data:
        splice.origin_ranges(clip, "static_model_list", [(0, len(data))], BASE)


# -- comparison with a map that exists on both platforms --------------------------------------


def compare_static_models(pc_gfx: dict, ps3_gfx: dict, words: dict, pc_xfile=None) -> dict:
    """Convert PC mp_nuked's static models with the base's model words and compare every
    field with PS3 mp_nuked's."""
    taken = Taken(pc_gfx.get("smodel_insts"), pc_gfx.get("smodel_draw_insts"))
    res = convert_static_models(taken, words, pc_xfile)
    mine = np.frombuffer(b"".join(e["raw"] for e in res.draw_insts or ()), np.uint8)
    theirs = np.frombuffer(
        b"".join(bytes(e["raw"]) for e in ps3_gfx.get("smodel_draw_insts") or ()), np.uint8
    )
    n = len(mine) // PS3_DRAW_INST
    report: dict = {"draw_insts": n, "ps3_draw_insts": len(theirs) // PS3_DRAW_INST}
    if len(mine) == len(theirs):
        a, b = mine.reshape(n, PS3_DRAW_INST), theirs.reshape(n, PS3_DRAW_INST)
        fields = {
            "cullDist": (0x0, 0x4),
            "origin": (0x4, 0x10),
            "axes (CMP)": (0x10, 0x1C),
            "scale": (0x1C, 0x20),
            "model alias": (0x20, 0x24),
            "flags": (0x24, 0x28),
            "lightingHandle, probe, primary light": (0x28, 0x2C),
        }
        report["identical"] = {
            k: int((a[:, lo:hi] == b[:, lo:hi]).all(axis=1).sum()) for k, (lo, hi) in fields.items()
        }
        ca = a[:, 0:4].copy().view(">f4")[:, 0]
        cb = b[:, 0:4].copy().view(">f4")[:, 0]
        ratio = np.abs(ca / np.where(cb == 0, 1, cb) - 1)
        report["cullDist_max_relative_difference"] = float(ratio.max())
    ia = np.frombuffer(res.insts or b"", ">f4").reshape(-1, 10)
    ib = np.frombuffer(bytes(ps3_gfx.get("smodel_insts") or b""), ">f4").reshape(-1, 10)
    if ia.shape == ib.shape and len(ia):
        report["insts"] = {
            "count": len(ia),
            "groundLighting identical": int((ia[:, 9].view(">u4") == ib[:, 9].view(">u4")).sum()),
            "mins/maxs identical": int((ia[:, :6] == ib[:, :6]).all(axis=1).sum()),
            "mins/maxs max abs difference": float(np.abs(ia[:, :6] - ib[:, :6]).max()),
            "lightingOrigin identical": int((ia[:, 6:9] == ib[:, 6:9]).all(axis=1).sum()),
            "lightingOrigin max abs difference": float(np.abs(ia[:, 6:9] - ib[:, 6:9]).max()),
        }
    return report
