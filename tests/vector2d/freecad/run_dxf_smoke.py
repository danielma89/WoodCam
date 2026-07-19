import os
import sys
import tempfile

import FreeCAD

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.domain.document import VectorDocument
from woodcam_editor.domain.entities import CircleEntity, PathEntity
from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.exporters.dxf import export_dxf
from woodcam_editor.importers.dxf import import_dxf


document = VectorDocument.create_default()
layer = document.active_layer_id
document.add_entities(
    (
        PathEntity.from_points(
            layer,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
            closed=True,
        ),
        CircleEntity(layer, Vec2(20, 20), 5),
    )
)
handle = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
handle.close()
documents_before = set(FreeCAD.listDocuments())
try:
    export_dxf(document, handle.name)
    result = import_dxf(handle.name, layer_id=layer, backend="freecad")
finally:
    os.unlink(handle.name)

assert result.entities
assert set(FreeCAD.listDocuments()) == documents_before
assert result.source_metadata["backend"] == "freecad_temp_document"
print("DXF FreeCAD temporary-document smoke: OK")

