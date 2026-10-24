"""Real abrupt process exit: no Qt shutdown, document close or lock cleanup."""
import os
from pathlib import Path
import sys

root = Path(sys.argv[1])
library = Path(sys.prefix) / "Library/bin"
dll_handle = os.add_dll_directory(str(library)) if sys.platform == "win32" else None
from PySide6.QtWidgets import QApplication
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from cadastrophe.document import Document, SceneObject
from cadastrophe.core.documents import DocumentManager
from cadastrophe.core.object_types import ObjectTypeRegistry
from cadastrophe.core.persistence import save_document
from cadastrophe.core.recovery import RecoveryService

app = QApplication([])
documents = DocumentManager()
document = documents.add(Document("Projet crash"))
body = SceneObject("Avant crash", BRepPrimAPI_MakeBox(12, 23, 34).Shape(), (.2, .4, .6))
document.add(body)
save_document(document, root / "original.cada")
document.rename(body.id, "Après crash éçà")
document.set_transparencies({body.id: .5})
service = RecoveryService(documents, ObjectTypeRegistry(), root / "recovery")
service.configure(True, 5)
assert service.snapshot_now() == 1
os._exit(73)
