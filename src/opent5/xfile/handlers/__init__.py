"""Per-type asset handlers. Importing this package registers every handler.

``REGISTRY`` maps an XAssetType value to its ``Handler``. Types the game has
no loader for (ui_map, weapondef, weaponvariant, aitype .. xmodelalias) have
no handler: the game's Load_XAssetHeader switch ignores them.
"""

from opent5.xfile.handlers import (  # noqa: F401  (imported to register)
    clipmap,
    comworld,
    fx,
    gameworld,
    gfxworld,
    image,
    lightdef,
    localize,
    mapents,
    material,
    menu,
    misc,
    physics,
    rawfile,
    shaders,
    sound,
    stringtable,
    techset,
    weapon,
    xanim,
    xmodel,
    xmodelpieces,
)
from opent5.xfile.handlers.base import REGISTRY, Handler, handler_for, register

__all__ = ["REGISTRY", "Handler", "handler_for", "register"]
