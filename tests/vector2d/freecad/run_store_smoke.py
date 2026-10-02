import os
import sys
import tempfile

import FreeCAD

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.adapters.freecad_store import (
    FreeCADDocumentStore,
    install_woodcam_display_observer,
)
from woodcam_editor.adapters.freecad_commands import FreeCADCommandSession
from woodcam_editor.application.document_store import StoredDocumentCorruptError
from woodcam_editor.domain.document import VectorDocument, WorkArea
from woodcam_editor.domain.entities import CircleEntity, PathEntity
from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.domain.commands import DeleteEntitiesCommand


document = FreeCAD.newDocument("WoodCAMVectorStoreSmoke")
document.UndoMode = 1
vector_document = VectorDocument.create_default(WorkArea(0, 0, 1850, 2750))
layer_id = vector_document.active_layer_id
outer = PathEntity.from_points(
    layer_id,
    (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
    closed=True,
)
hole = CircleEntity(layer_id, Vec2(20, 20), 5)
vector_document.add_entities((outer, hole))

store = FreeCADDocumentStore(document)
info = store.save(vector_document, source_metadata={"test": True})
feature = document.getObject(info.feature_name)
assert feature is not None
assert feature.GeometryJSON
assert feature.Checksum == info.checksum
assert len(feature.Shape.Edges) >= 5
assert all(
    "Hidden" in feature.getEditorMode(name)
    for name in ("GeometryJSON", "SourceMetadataJSON", "Shape")
)
loaded = store.load()
assert set(loaded.entities_by_id) == {outer.id, hole.id}
session = FreeCADCommandSession(loaded, store)
session.execute(DeleteEntitiesCommand((outer.id, hole.id)))
assert not loaded.entities_by_id
assert not store.load().entities_by_id
assert not document.getObject(info.feature_name).Shape.Edges
document.undo()
session.reload()
assert set(loaded.entities_by_id) == {outer.id, hole.id}
assert len(document.getObject(info.feature_name).Shape.Edges) >= 5

# One store save is one host transaction and therefore participates in Undo.
document.undo()
assert document.getObject(info.feature_name) is None
document.redo()
assert document.getObject(info.feature_name) is not None

# Deleting the internal feature in the FreeCAD tree must not cause the Editor
# to restore the vector snapshot it loaded when the session first opened.
document.openTransaction("Delete vector feature in tree")
document.removeObject(info.feature_name)
document.commitTransaction()
session.reload()
assert not loaded.entities_by_id
assert document.getObject(info.feature_name) is None
document.undo()
session.reload()
assert set(loaded.entities_by_id) == {outer.id, hole.id}

feature = document.getObject(info.feature_name)
valid_json = feature.GeometryJSON
feature.GeometryJSON = "{invalid-json"
try:
    store.load()
except StoredDocumentCorruptError:
    pass
else:
    raise AssertionError("corrupt JSON must be rejected")
assert feature.GeometryJSON == "{invalid-json"
feature.GeometryJSON = valid_json

path = tempfile.mktemp(prefix="woodcam-vector-store-", suffix=".FCStd")
document.recompute()
document.saveAs(path)
FreeCAD.closeDocument(document.Name)
reopened = FreeCAD.openDocument(path)
reopened_store = FreeCADDocumentStore(reopened)
reloaded = reopened_store.load()
reopened_feature = reopened.getObject(info.feature_name)
assert all(
    "Hidden" in reopened_feature.getEditorMode(name)
    for name in ("GeometryJSON", "SourceMetadataJSON", "Shape")
)
assert set(reloaded.entities_by_id) == {outer.id, hole.id}
assert reloaded.work_area.width == 1850
assert reloaded.work_area.height == 2750
assert set(reopened_store._entity_shape_cache) == {outer.id, hole.id}
reopened_feature.setEditorMode("GeometryJSON", 0)
operation = reopened.addObject("App::DocumentObjectGroup", "WoodCAMOperationCutSmoke")
operation.addProperty("App::PropertyString", "SettingsJSON")
operation.addProperty("App::PropertyString", "MovesCompressedBase64")
operation.SettingsJSON = '{"operation_mode":"cut"}'
operation.MovesCompressedBase64 = "small-payload"
operation_name = operation.Name
reopened.save()
FreeCAD.closeDocument(reopened.Name)
install_woodcam_display_observer()
protected = FreeCAD.openDocument(path)
assert "Hidden" in protected.getObject(info.feature_name).getEditorMode("GeometryJSON")
protected_operation = protected.getObject(operation_name)
assert "Hidden" in protected_operation.getEditorMode("MovesCompressedBase64")
assert protected_operation.MovesCompressedBase64 == "small-payload"
FreeCAD.closeDocument(protected.Name)
os.unlink(path)
print("FreeCAD document store smoke: OK")
