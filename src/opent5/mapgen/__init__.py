"""Procedural blocky (voxel) map generation for OpenT5.

A seed makes a cube-grid :class:`~opent5.mapgen.terrain.Terrain` (rolling hills, caves, trees,
water, a village), greedy-meshed into textured brushes (``greedy``) and written as a Radiant
``.map`` (``mapwriter``). The block textures are this project's own pixel art (``textures``).
See docs/mapgen.md; the CLI is tools/blockmap.py.
"""

from . import greedy, mapwriter, noise, preview, terrain, textures

__all__ = ["noise", "terrain", "greedy", "textures", "mapwriter", "preview"]
