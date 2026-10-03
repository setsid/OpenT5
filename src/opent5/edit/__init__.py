"""The editing layer shared by the command line and the GUI (docs/edit-api.md).

from opent5.edit import Document
doc = Document.open("mp_nuked.ff")
doc.set_text(doc.find("maps/mp/mp_nuked.gsc").index, new_text)
report = doc.save("out/mp_nuked.ff")       # verified; report.problems is empty
"""

from opent5.edit.document import Document, check_target, game_folders
from opent5.edit.geometry import Mesh
from opent5.edit.types import (
    SIGNATURE_NOTE,
    AssetKey,
    AssetRef,
    Change,
    EditError,
    ImageData,
    SaveReport,
    SearchHit,
)

__all__ = [
    "SIGNATURE_NOTE",
    "AssetKey",
    "AssetRef",
    "Change",
    "Document",
    "EditError",
    "ImageData",
    "Mesh",
    "SaveReport",
    "SearchHit",
    "check_target",
    "game_folders",
]
