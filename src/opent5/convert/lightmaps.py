"""The PC map's own cod2rad lighting in PS3 form (``--lighting baked``).

cod2rad and the PC linker store, inline under the GfxWorld: the lightmaps (three images per
GfxLightmapArray: ``*lightmapN_primary`` DXT1, ``_secondary`` R5G6B5, ``_secondaryb``
G16R16), the reflection probes (origin, cube image, probe volumes) and the ``$outdoor``
image. Each image becomes a new PS3 GfxImage (``images.convert_image``) whose pixels go in
the map's own zone (deferred, end of the zone); no ``.pak`` is involved. The surfaces keep
their PC lightmap uv and their PC lightmap and probe indices, which then index these arrays
(PC and PS3 mp_nuked agree on both, docs/convert.md 4). Transforms and proof:
docs/research/box-lighting.md 2..5, tests/test_convert_images.py.
"""

from __future__ import annotations

import hashlib
import struct

from opent5.convert.images import convert_image
from opent5.convert.swap import swap_struct
from opent5.formats.texture import format_name
from opent5.xfile.constants import PTR_INLINE

INLINE = struct.pack(">I", PTR_INLINE)


def _image(pc_image, what: str, report: list, new_nodes: list) -> dict:
    from opent5.convert.world import ConvertError

    if not isinstance(pc_image, dict):
        raise ConvertError(
            f"{what}: expected the PC image inline under the GfxWorld (cod2rad output), "
            f"found {type(pc_image).__name__}"
        )
    node = convert_image(pc_image)
    h = node["header"]
    report.append(
        {
            "name": node["name"],
            "what": what,
            "format": format_name(h[0]),
            "width": struct.unpack_from(">H", h, 8)[0],
            "height": struct.unpack_from(">H", h, 10)[0],
            "levels": h[1],
            "bytes": len(node["pixels"]),
            "sha1": hashlib.sha1(node["pixels"]).hexdigest(),
        }
    )
    new_nodes.append(node)
    return node


def convert_lighting(pc: dict, node: dict, new_nodes: list) -> dict:
    """Fill ``node`` (the PS3 GfxWorld being built) with the converted lightmaps, probes and
    outdoor image of ``pc`` (the parsed PC GfxWorld's keys). Returns the report."""
    images: list[dict] = []
    lightmaps = []
    for i, pc_lm in enumerate(pc.get("lightmaps") or ()):
        element = {"_t": "GfxLightmapArray", "raw": INLINE * 3}
        for k in range(3):
            element[k] = _image(pc_lm.get(k), f"lightmap {i} image {k}", images, new_nodes)
        lightmaps.append(element)
        new_nodes.append(element)
    node["lightmaps"] = lightmaps or None

    probes = []
    for i, p in enumerate(pc.get("reflection_probes") or ()):
        raw = bytearray(swap_struct("GfxReflectionProbe", p["raw"]))
        raw[0xC:0x10] = INLINE
        volumes = p.get("probe_volumes")
        if volumes is not None:
            volumes = swap_struct("GfxReflectionProbeVolumeData", volumes)
            raw[0x10:0x14] = INLINE
        else:
            raw[0x10:0x14] = bytes(4)
        element = {
            "_t": "GfxReflectionProbe",
            "raw": bytes(raw),
            "image": _image(p.get("image"), f"reflection probe {i}", images, new_nodes),
            "probe_volumes": volumes,
        }
        probes.append(element)
        new_nodes.append(element)
    node["reflection_probes"] = probes or None

    if pc.get("outdoor_image") is not None:
        node["outdoor_image"] = _image(pc["outdoor_image"], "outdoor", images, new_nodes)
    return {
        "lightmaps": len(lightmaps),
        "reflection_probes": len(probes),
        "outdoor_image": pc.get("outdoor_image") is not None,
        "images": images,
    }
