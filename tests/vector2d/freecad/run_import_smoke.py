import FreeCAD
import Part
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.importers.part_shape import import_freecad_tree, import_part_shape
from woodcam_editor.importers.panelnest_parts import import_panelnest_parts
from woodcam_editor.application.piece_organizer import classify_document_pieces
from geometry_reader import resolve_selection_objects
from ui import WoodCAM2DDialog


points = [
    FreeCAD.Vector(0, 0, 0),
    FreeCAD.Vector(100, 0, 0),
    FreeCAD.Vector(100, 50, 0),
    FreeCAD.Vector(0, 50, 0),
]
wire = Part.makePolygon(points + [points[0]])
circle = Part.makeCircle(5, FreeCAD.Vector(20, 20, 0))
shape = Part.makeCompound([wire, circle])

result = import_part_shape(shape, layer_id="layer")
assert len(result.entities) == 2, result
assert sorted(type(entity).__name__ for entity in result.entities) == [
    "CircleEntity",
    "PathEntity",
]
assert next(entity for entity in result.entities if type(entity).__name__ == "PathEntity").closed

# Um CAM Chapa composto por vários sólidos deve importar uma forma por sólido,
# nunca somente a maior face de todo o compound.
first_box = Part.makeBox(40, 20, 5)
second_box = Part.makeBox(30, 15, 5, FreeCAD.Vector(60, 0, 0))
solid_compound = Part.makeCompound([first_box, second_box])
solid_result = import_part_shape(solid_compound, layer_id="layer")
solid_paths = [
    entity
    for entity in solid_result.entities
    if type(entity).__name__ == "PathEntity"
]
assert len(solid_paths) == 2, solid_result
assert all(entity.closed for entity in solid_paths)

# Um recorte "osso" pode chegar como um único Compound composto por uma
# placa central e quatro discos sobrepostos.  A cópia 2D deve unir as faces
# sobrepostas em um único perfil, sem alterar o Shape de origem e sem fundir
# painéis que apenas se encostam.
bone_plate = Part.makeBox(20, 20, 5)
bone_disks = [
    Part.makeCylinder(5, 5, FreeCAD.Vector(x_value, y_value, 0))
    for x_value, y_value in ((0, 0), (20, 0), (0, 20), (20, 20))
]
bone_compound = Part.makeCompound([bone_plate] + bone_disks)
bone_result = import_part_shape(bone_compound, layer_id="layer")
bone_paths = [
    entity
    for entity in bone_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
]
assert len(bone_paths) == 1, bone_result
assert not [
    entity for entity in bone_result.entities if type(entity).__name__ == "CircleEntity"
], bone_result

# A mesma geometria pode estar em pé no Assembly.  A projeção local deve
# preservar o único perfil usinável: não pode transformar o osso em uma placa
# central mais quatro círculos soltos só porque a normal não é Z.
upright_bone = bone_compound.copy()
upright_bone.rotate(
    FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90.0
)
upright_bone_result = import_part_shape(
    upright_bone,
    layer_id="layer",
    flatten_solids=True,
)
upright_bone_paths = [
    entity
    for entity in upright_bone_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
]
assert len(upright_bone_paths) == 1, upright_bone_result
assert not [
    entity
    for entity in upright_bone_result.entities
    if type(entity).__name__ == "CircleEntity"
], upright_bone_result

# Um painel pode estar em pé em uma montagem.  A importação precisa escolher
# a face larga (100 x 50), projetá-la no plano do Editor e preservar o furo,
# nunca usar a face lateral de 15 mm.
upright_panel = Part.makeBox(100, 50, 15)
upright_panel = upright_panel.cut(
    Part.makeCylinder(5, 15, FreeCAD.Vector(25, 25, 0))
)
upright_panel.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90)
safe_upright_result = import_part_shape(upright_panel, layer_id="layer")
assert not safe_upright_result.entities
assert "peças planas" in safe_upright_result.issues[0].message
upright_result = import_part_shape(
    upright_panel,
    layer_id="layer",
    flatten_solids=True,
    compound_groups=True,
)
upright_paths = [
    entity
    for entity in upright_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
]
assert len(upright_paths) == 1, upright_result
upright_bounds = upright_paths[0].bounds()
upright_dimensions = sorted(
    (round(upright_bounds.width, 6), round(upright_bounds.height, 6))
)
assert upright_dimensions == [50.0, 100.0], upright_dimensions
assert any(type(entity).__name__ == "CircleEntity" for entity in upright_result.entities)
upright_compounds = [
    entity for entity in upright_result.entities if type(entity).__name__ == "GroupEntity"
]
assert len(upright_compounds) == 1, upright_result
assert len(upright_compounds[0].child_ids) == 2, upright_compounds

# A direct assembly import must use that same broad-face flattening without
# calling PanelNest.  Staging is a compact grid, not the former very long
# horizontal strip which made the Editor 2D difficult to inspect/select.
upright_triplet = Part.makeCompound(
    [
        upright_panel,
        upright_panel.copy(),
        upright_panel.copy(),
    ]
)
direct_assembly_result = import_part_shape(
    upright_triplet,
    layer_id="layer",
    flatten_solids=True,
)
direct_assembly_paths = [
    entity
    for entity in direct_assembly_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
]
assert len(direct_assembly_paths) == 3, direct_assembly_result
direct_bounds = [entity.bounds() for entity in direct_assembly_paths]
overall_width = max(bounds.max_x for bounds in direct_bounds) - min(
    bounds.min_x for bounds in direct_bounds
)
overall_height = max(bounds.max_y for bounds in direct_bounds) - min(
    bounds.min_y for bounds in direct_bounds
)
assert overall_width < 260.0, (overall_width, overall_height)
# The shelf can use one or more rows depending on each part's proportions;
# either way its bounding box must stay compact rather than becoming a 350 mm
# strip (three 100 mm panels plus 25 mm gaps).
assert max(overall_width, overall_height) < 260.0, (overall_width, overall_height)

# A seleção de um grupo/Assembly deve percorrer os componentes reais da
# árvore, em vez de importar seu Shape consolidado.  Cada painel continua
# independente e os seus furos ficam no mesmo GroupEntity da peça.
tree_document = FreeCAD.newDocument("WoodCAMTreePartShapeSmoke")
tree_root = tree_document.addObject("App::DocumentObjectGroup", "Furniture")
tree_first = tree_document.addObject("PartDesign::Feature", "TreeFirst")
tree_first.Shape = upright_panel
tree_second = tree_document.addObject("PartDesign::Feature", "TreeSecond")
tree_second_shape = upright_panel.copy()
tree_second_shape.translate(FreeCAD.Vector(400, 0, 0))
tree_second.Shape = tree_second_shape
tree_root.addObject(tree_first)
tree_root.addObject(tree_second)
tree_document.recompute()
tree_result = import_freecad_tree(
    (tree_root,),
    layer_id="layer",
    flatten_solids=True,
    compound_groups=True,
)
tree_paths = [
    entity
    for entity in tree_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
]
tree_groups = [
    entity for entity in tree_result.entities if type(entity).__name__ == "GroupEntity"
]
assert len(tree_paths) == 2, tree_result
assert len(tree_groups) == 2, tree_result
assert all(len(group.child_ids) == 2 for group in tree_groups), tree_groups
assert len([entity for entity in tree_result.entities if type(entity).__name__ == "CircleEntity"]) == 2
tree_bounds = [path.bounds() for path in tree_paths]
assert not (
    tree_bounds[0].max_x > tree_bounds[1].min_x
    and tree_bounds[1].max_x > tree_bounds[0].min_x
    and tree_bounds[0].max_y > tree_bounds[1].min_y
    and tree_bounds[1].max_y > tree_bounds[0].min_y
), tree_bounds


class _TreeImportDocument:
    def __init__(self, entities):
        self.entities_by_id = {entity.id: entity for entity in entities}


tree_classification = classify_document_pieces(_TreeImportDocument(tree_result.entities))
assert len(tree_classification.pieces) == 2, tree_classification
assert all(len(piece.inner_ids) == 1 for piece in tree_classification.pieces), tree_classification
FreeCAD.closeDocument(tree_document.Name)

# O PanelNest pode reportar os arcos circulares de um recorte "osso" como
# quatro furos soltos e omitir o perfil externo por ele ainda ser retangular.
# A ponte do Editor precisa reler o fio interno original, manter o osso como
# um único recorte e não duplicar os quatro arcos como furos.
dogbone_document = FreeCAD.newDocument("WoodCAMPanelNestDogboneSmoke")
plate = Part.makeBox(30, 30, 5)
window = Part.makeBox(12, 12, 5, FreeCAD.Vector(9, 9, 0))
for x_value, y_value in ((9, 9), (21, 9), (9, 21), (21, 21)):
    window = window.fuse(Part.makeCylinder(3, 5, FreeCAD.Vector(x_value, y_value, 0)))
dogbone_source = dogbone_document.addObject("PartDesign::Feature", "DogbonePlate")
dogbone_source.Shape = plate.cut(window)
dogbone_document.recompute()

class _PanelNestDogbonePart:
    object_name = "DogbonePlate"
    part_id = "PN-DOGBONE"
    label = "Dogbone"
    length_mm = 30.0
    width_mm = 30.0
    thickness_mm = 5.0
    quantity = 1
    # PanelNest exposes an explicit rectangle here, but the source face is
    # authoritative because it contains the joined dogbone arcs.
    profile_points = [(0.0, 0.0), (30.0, 0.0), (30.0, 30.0), (0.0, 30.0)]
    holes = [
        {"x_mm": 9.0, "y_mm": 9.0, "diameter_mm": 6.0},
        {"x_mm": 21.0, "y_mm": 9.0, "diameter_mm": 6.0},
        {"x_mm": 9.0, "y_mm": 21.0, "diameter_mm": 6.0},
        {"x_mm": 21.0, "y_mm": 21.0, "diameter_mm": 6.0},
    ]

dogbone_part = _PanelNestDogbonePart()
dogbone_payload = WoodCAM2DDialog._panelnest_source_profiles_from_source((dogbone_part,))
assert len(dogbone_payload["DogbonePlate"]["inner_loops"]) == 1, dogbone_payload
assert dogbone_payload["DogbonePlate"]["holes"] == (), dogbone_payload
dogbone_exact = WoodCAM2DDialog._panelnest_exact_source_entities(
    (dogbone_part,),
    layer_id="layer",
)
assert len(dogbone_exact["DogbonePlate"]) == 2, dogbone_exact
dogbone_result = import_panelnest_parts(
    (dogbone_part,),
    layer_id="layer",
    source_profiles_by_source=dogbone_payload,
    exact_entities_by_source=dogbone_exact,
)
assert [type(entity).__name__ for entity in dogbone_result.entities] == [
    "PathEntity", "PathEntity", "GroupEntity",
], dogbone_result
assert len(dogbone_result.entities[-1].child_ids) == 2, dogbone_result
assert any(
    type(span).__name__ == "ArcSpan"
    for entity in dogbone_result.entities
    for span in getattr(entity, "spans", ())
), dogbone_result
FreeCAD.closeDocument(dogbone_document.Name)

document = FreeCAD.newDocument("WoodCAMTreeImportSmoke")
layout = document.addObject("App::FeaturePython", "PanelNestLayout")
layout.addProperty("App::PropertyString", "PanelNestManagedType")
layout.PanelNestManagedType = "layout_root"
sheet_base = document.addObject("PartDesign::Feature", "PanelNestSheetBase")
sheet_base.Shape = Part.makeBox(300, 200, 10)
cam_sheet = document.addObject("PartDesign::Feature", "PanelNestCAMChapa01_01")
cam_sheet.addProperty("App::PropertyString", "PanelNestManagedType")
cam_sheet.PanelNestManagedType = "layout_cam_compound"
cam_sheet.Shape = Part.makeFace(wire)
document.recompute()
resolved = resolve_selection_objects((layout,))
assert resolved == [cam_sheet]
assert sheet_base not in resolved
FreeCAD.closeDocument(document.Name)
print("part_shape importer smoke: OK")
