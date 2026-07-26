import json
import math
import os
import sys
import tempfile

import FreeCAD

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.adapters.panelnest import (
    PanelNestBridgeError,
    send_document_to_panelnest,
)
from geometry_reader import resolve_selection_objects
from woodcam_editor.domain.document import Piece2D, VectorDocument
from woodcam_editor.domain.entities import CircleEntity, EllipseEntity, PathEntity
from woodcam_editor.domain.primitives import Affine2D, Vec2
from woodcam_editor.domain.spans import ArcSpan, LineSpan


document = FreeCAD.newDocument("WoodCAMPanelNestBridgeSmoke")
document.UndoMode = 1
vector_document = VectorDocument.create_default()
layer_id = vector_document.active_layer_id
outer = PathEntity.from_points(
    layer_id,
    (Vec2(100, 200), Vec2(220, 200), Vec2(220, 280), Vec2(100, 280)),
    closed=True,
)
inner = PathEntity.from_points(
    layer_id,
    (Vec2(120, 220), Vec2(140, 220), Vec2(140, 240), Vec2(120, 240)),
    closed=True,
)
hole = CircleEntity(layer_id, Vec2(180, 240), 5)
polyline_hole_points = tuple(
    Vec2(
        160.0 + 4.0 * math.cos(2.0 * math.pi * index / 16.0),
        220.0 + 4.0 * math.sin(2.0 * math.pi * index / 16.0),
    )
    for index in range(16)
)
polyline_hole = PathEntity.from_points(
    layer_id,
    polyline_hole_points,
    closed=True,
    id="imported-polyline-hole",
)
vector_document.add_entities((outer, inner, hole, polyline_hole))
piece = Piece2D(
    id="piece-bridge",
    name="Lateral com recortes",
    outer_path_id=outer.id,
    inner_path_ids=(inner.id, hole.id, polyline_hole.id),
    quantity=2,
    material="MDF 18",
    thickness=18.0,
    metadata={"cut_method": "CNC", "grain_match_group": "frente-a"},
)
vector_document.add_pieces((piece,))

result = send_document_to_panelnest(
    vector_document,
    freecad_document=document,
)
if result.contract.available:
    assert result.mode == "freecad_exchange+panelnest_metadata"
    assert result.recognized_part_count == 2
    assert not result.contract.supports_inner_profile_loops
    assert not result.contract.has_direct_part_import
    import panelnest

    recognized = panelnest.collect_parts(
        [document.getObject(name) for name in result.object_names],
        include_hidden=True,
    )
    assert len(recognized) == 2
    assert all(len(part.holes) == 2 for part in recognized)
else:
    assert result.mode == "freecad_exchange"
assert len(result.items) == 1
assert len(result.object_names) == 2
assert result.items[0].inner_profile_count == 1
assert result.items[0].circular_hole_count == 2
assert result.items[0].source_offset == (100.0, 200.0)

root = document.getObject(result.group_name)
assert root is not None
woodcam_root = document.getObject("WoodCAM")
assert woodcam_root is not None
assert [child.Label for child in woodcam_root.Group] == [
    "Peças",
    "Operações",
    "Área de trabalho",
]
assert root.Label == "Peças"
assert document.getObject("WoodCAM2DPanelNestExchange") is None
assert root.WoodCAMExchangeType == "panelnest_exchange_root"
assert root.WoodCAMExchangeLayoutMode == "preserve_editor_xy"
assert not root.WoodCAMPanelNestAutoNesting
assert len(root.Group) == 2
assert resolve_selection_objects([root]) == list(root.Group)
manifest = json.loads(root.WoodCAMExchangeManifestJson)
assert manifest["layout_mode"] == "preserve_editor_xy"
assert manifest["automatic_nesting"] is False
assert manifest["placements"]["piece-bridge"] == {
    "base": [100.0, 200.0, 0.0],
    "plane": "XY",
    "rotation_mode": "baked_in_profile",
}

expected_volume = (
    (120.0 * 80.0)
    - (20.0 * 20.0)
    - (math.pi * 5.0 * 5.0)
    - (math.pi * 4.0 * 4.0)
) * 18.0
for object_name in result.object_names:
    feature = document.getObject(object_name)
    assert feature is not None
    assert feature.WoodCAMExchangeType == "panel_part"
    assert "PanelNestManagedType" not in feature.PropertiesList
    assert feature.PanelNestMaterial == "MDF 18"
    assert feature.PanelNestQuantity == 1
    assert feature.PanelNestCutMethod == "CNC"
    assert feature.Placement.Base == FreeCAD.Vector(100.0, 200.0, 0.0)
    assert feature.WoodCAMSourcePlane == "XY"
    assert feature.WoodCAMSourceRotationMode == "baked_in_profile"
    assert feature.WoodCAMExchangeLayoutMode == "preserve_editor_xy"
    assert not feature.WoodCAMPanelNestAutoNesting
    assert abs(feature.Shape.BoundBox.XMin - 100.0) < 1e-8
    assert abs(feature.Shape.BoundBox.YMin - 200.0) < 1e-8
    assert abs(feature.Shape.BoundBox.ZMin) < 1e-8
    assert len(feature.Shape.Solids) == 1
    assert abs(feature.Shape.Volume - expected_volume) < 0.1
    payload = json.loads(feature.WoodCAMPanelPartJson)
    assert len(payload["inner_profile_loops"]) == 1
    assert len(payload["circular_holes"]) == 2
    assert payload["profile_points"][0] == [0.0, 0.0]

# The complete export is one host transaction.
document.undo()
assert document.getObject(result.group_name) is None
for object_name in result.object_names:
    assert document.getObject(object_name) is None
document.redo()
assert document.getObject(result.group_name) is not None
for object_name in result.object_names:
    assert document.getObject(object_name) is not None

path = tempfile.mktemp(prefix="woodcam-panelnest-bridge-", suffix=".FCStd")
document.recompute()
document.saveAs(path)
FreeCAD.closeDocument(document.Name)
reopened = FreeCAD.openDocument(path)
reopened_root = reopened.getObject(result.group_name)
assert reopened_root is not None
assert len(reopened_root.Group) == 2
for object_name in result.object_names:
    feature = reopened.getObject(object_name)
    assert feature is not None
    assert json.loads(feature.WoodCAMPanelPartJson)["id"] == "piece-bridge"
    assert abs(feature.Shape.Volume - expected_volume) < 0.1
    assert feature.Placement.Base == FreeCAD.Vector(100.0, 200.0, 0.0)
FreeCAD.closeDocument(reopened.Name)
os.unlink(path)

# OCC robustness matrix: exact circles/ellipses, ordinary polygons and a path
# with a retraced/touching segment.  Source contours intentionally touch each
# other and one starts on the work-area origin; normalization must keep each
# classified piece independent and create only valid one-solid Shapes.
matrix_document = FreeCAD.newDocument("WoodCAMPanelNestOccMatrixSmoke")


class NoAutomaticNestingPanelNest:
    __name__ = "no_automatic_nesting_panelnest"

    @staticmethod
    def ensure_part_properties(_feature):
        return None

    @staticmethod
    def collect_parts(objects, include_hidden=False):
        return list(objects)

    @staticmethod
    def generate_layout(*_args, **_kwargs):
        raise AssertionError("the WoodCAM exchange must not generate a PanelNest layout")

    @staticmethod
    def nest_parts(*_args, **_kwargs):
        raise AssertionError("the WoodCAM exchange must not run nesting")


matrix_vector_document = VectorDocument.create_default()
matrix_layer_id = matrix_vector_document.active_layer_id
circle_outer = CircleEntity(
    matrix_layer_id,
    Vec2(270.0, 240.0),
    50.0,
    id="matrix-circle",
)
ellipse_outer = EllipseEntity(
    matrix_layer_id,
    Vec2(380.0, 240.0),
    60.0,
    30.0,
    id="matrix-ellipse",
)
ellipse_inner = EllipseEntity(
    matrix_layer_id,
    Vec2(380.0, 240.0),
    18.0,
    8.0,
    rotation=0.2,
    id="matrix-ellipse-inner",
)
border_polygon = PathEntity.from_points(
    matrix_layer_id,
    (Vec2(0.0, 0.0), Vec2(80.0, 0.0), Vec2(60.0, 50.0), Vec2(0.0, 50.0)),
    closed=True,
    id="matrix-border-polygon",
)
touching_path = PathEntity.from_points(
    matrix_layer_id,
    (
        Vec2(500.0, 200.0),
        Vec2(600.0, 200.0),
        Vec2(600.0, 300.0),
        Vec2(500.0, 300.0),
        Vec2(500.0, 250.0),
        Vec2(550.0, 250.0),
        Vec2(550.0, 200.0),
    ),
    closed=True,
    id="matrix-touching-path",
)
curved_path = PathEntity(
    matrix_layer_id,
    (
        LineSpan(Vec2(600.0, 200.0), Vec2(650.0, 200.0)),
        ArcSpan(Vec2(650.0, 200.0), Vec2(650.0, 260.0), Vec2(650.0, 230.0)),
        LineSpan(Vec2(650.0, 260.0), Vec2(600.0, 260.0)),
        ArcSpan(Vec2(600.0, 260.0), Vec2(600.0, 200.0), Vec2(600.0, 230.0)),
    ),
    closed=True,
    id="matrix-curved-path",
)
placed_rectangle = PathEntity.from_points(
    matrix_layer_id,
    (Vec2(0.0, 0.0), Vec2(80.0, 0.0), Vec2(80.0, 40.0), Vec2(0.0, 40.0)),
    closed=True,
    id="matrix-placed-rectangle",
)
matrix_vector_document.add_entities(
    (
        circle_outer,
        ellipse_outer,
        ellipse_inner,
        border_polygon,
        touching_path,
        curved_path,
        placed_rectangle,
    )
)
matrix_vector_document.add_pieces(
    (
        Piece2D(
            "Círculo tangente",
            circle_outer.id,
            id="piece-circle",
            thickness=18.0,
        ),
        Piece2D(
            "Elipse com interno",
            ellipse_outer.id,
            (ellipse_inner.id,),
            id="piece-ellipse",
            thickness=18.0,
        ),
        Piece2D(
            "Polígono na borda",
            border_polygon.id,
            id="piece-polygon-border",
            thickness=18.0,
        ),
        Piece2D(
            "Path normalizado",
            touching_path.id,
            id="piece-touching-path",
            thickness=18.0,
        ),
        Piece2D(
            "Path com arcos",
            curved_path.id,
            id="piece-curved-path",
            thickness=18.0,
        ),
        Piece2D(
            "Retângulo rotacionado",
            placed_rectangle.id,
            id="piece-placed-rotated",
            thickness=18.0,
            placement=(
                Affine2D.translation(Vec2(720.0, 180.0))
                @ Affine2D.rotation(math.radians(30.0))
            ),
        ),
    )
)
matrix_result = send_document_to_panelnest(
    matrix_vector_document,
    freecad_document=matrix_document,
    panelnest_module=NoAutomaticNestingPanelNest,
)
assert len(matrix_result.items) == 6
assert len(matrix_result.object_names) == 6
assert matrix_result.recognized_part_count == 6
matrix_root = matrix_document.getObject(matrix_result.group_name)
matrix_manifest = json.loads(matrix_root.WoodCAMExchangeManifestJson)
assert {part["id"] for part in matrix_manifest["parts"]} == {
    "piece-circle",
    "piece-ellipse",
    "piece-polygon-border",
    "piece-touching-path",
    "piece-curved-path",
    "piece-placed-rotated",
}
ellipse_payload = next(
    part for part in matrix_manifest["parts"] if part["id"] == "piece-ellipse"
)
assert len(ellipse_payload["inner_profile_loops"]) == 1
assert len(ellipse_payload["inner_profile_loops"][0]) > 12
for object_name in matrix_result.object_names:
    feature = matrix_document.getObject(object_name)
    assert feature.Shape.isValid()
    assert len(feature.Shape.Solids) == 1
    assert feature.Shape.Volume > 0.0
    payload = json.loads(feature.WoodCAMPanelPartJson)
    item = next(item for item in matrix_result.items if item.part_id == payload["id"])
    assert feature.Placement.Base == FreeCAD.Vector(
        item.source_offset[0], item.source_offset[1], 0.0
    )
    assert abs(feature.Shape.BoundBox.XMin - item.source_offset[0]) < 1e-7
    assert abs(feature.Shape.BoundBox.YMin - item.source_offset[1]) < 1e-7
    assert abs(feature.Shape.BoundBox.ZMin) < 1e-8
    assert payload == next(
        part for part in matrix_manifest["parts"] if part["id"] == payload["id"]
    )

rotated_feature = next(
    matrix_document.getObject(name)
    for name in matrix_result.object_names
    if matrix_document.getObject(name).WoodCAMSourcePieceId == "piece-placed-rotated"
)
rotated_payload = json.loads(rotated_feature.WoodCAMPanelPartJson)
first, second = rotated_payload["profile_points"][:2]
preserved_angle = math.degrees(
    math.atan2(second[1] - first[1], second[0] - first[0])
)
assert abs(preserved_angle - 30.0) < 1e-7
assert rotated_feature.WoodCAMSourceRotationMode == "baked_in_profile"

circle_feature = next(
    matrix_document.getObject(name)
    for name in matrix_result.object_names
    if matrix_document.getObject(name).WoodCAMSourcePieceId == "piece-circle"
)
ellipse_feature = next(
    matrix_document.getObject(name)
    for name in matrix_result.object_names
    if matrix_document.getObject(name).WoodCAMSourcePieceId == "piece-ellipse"
)
assert abs(circle_feature.Shape.BoundBox.XMax - ellipse_feature.Shape.BoundBox.XMin) < 0.02, (
    circle_feature.Shape.BoundBox.XMax,
    ellipse_feature.Shape.BoundBox.XMin,
)

# A genuinely ambiguous self-crossing contour must not leak an invalid Shape.
# Replacing an existing exchange is transactional: the previous complete
# manifest and every valid feature survive the rejected send.
manifest_before_rejected_send = matrix_root.WoodCAMExchangeManifestJson
names_before_rejected_send = tuple(matrix_result.object_names)
invalid_vector_document = VectorDocument.create_default()
invalid_path = PathEntity.from_points(
    invalid_vector_document.active_layer_id,
    (Vec2(0, 0), Vec2(100, 0), Vec2(20, 100), Vec2(80, 100), Vec2(0, 20)),
    closed=True,
    id="matrix-self-cross",
)
invalid_vector_document.add_entities((invalid_path,))
invalid_vector_document.add_pieces(
    (
        Piece2D(
            "Autocruzada",
            invalid_path.id,
            id="piece-self-cross",
            thickness=18.0,
        ),
    )
)
try:
    send_document_to_panelnest(
        invalid_vector_document,
        freecad_document=matrix_document,
        panelnest_module=None,
    )
except PanelNestBridgeError as error:
    message = str(error)
    assert "piece-self-cross" in message
    assert "2 regiões" in message
else:
    raise AssertionError("self-crossing PanelPart unexpectedly produced a Shape")
restored_matrix_root = matrix_document.getObject(matrix_result.group_name)
assert restored_matrix_root is not None
assert restored_matrix_root.WoodCAMExchangeManifestJson == manifest_before_rejected_send
for object_name in names_before_rejected_send:
    restored_feature = matrix_document.getObject(object_name)
    assert restored_feature is not None
    assert restored_feature.Shape.isValid()
    assert len(restored_feature.Shape.Solids) == 1

FreeCAD.closeDocument(matrix_document.Name)
print("FreeCAD PanelNest bridge smoke: OK")
