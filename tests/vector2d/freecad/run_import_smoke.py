import FreeCAD
import Part
import os
import sys
from types import SimpleNamespace

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.importers.part_shape import import_freecad_tree, import_part_shape
from woodcam_editor.importers.panelnest_parts import import_panelnest_parts
from woodcam_editor.application.piece_organizer import classify_document_pieces
from woodcam_editor.domain import VectorDocument, validate_document
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

# Um rebaixo cego tem uma face de fundo real. A importação deve preservar essa
# região como hachura/seleção CAM independente, inclusive quando o rebaixo abre
# na borda e compartilha um trecho com o contorno externo da chapa.
for pocket_name, cutter in (
    (
        "closed",
        Part.makeBox(60, 40, 5, FreeCAD.Vector(50, 30, 13)),
    ),
    (
        "edge-open",
        Part.makeBox(70, 40, 5, FreeCAD.Vector(-1, 30, 13)),
    ),
):
    pocket_source = Part.makeBox(200, 100, 18).cut(cutter)
    source_volume = round(float(pocket_source.Volume), 9)
    source_bounds = (
        round(float(pocket_source.BoundBox.XMin), 9),
        round(float(pocket_source.BoundBox.YMin), 9),
        round(float(pocket_source.BoundBox.ZMin), 9),
        round(float(pocket_source.BoundBox.XMax), 9),
        round(float(pocket_source.BoundBox.YMax), 9),
        round(float(pocket_source.BoundBox.ZMax), 9),
    )
    pocket_document = VectorDocument.create_default()
    pocket_result = import_part_shape(
        pocket_source,
        layer_id=pocket_document.active_layer_id,
    )
    pocket_document.add_entities(pocket_result.entities, bump_revision=False)
    pocket_regions = [
        entity
        for entity in pocket_result.entities
        if str(entity.metadata.get("import_role", "")) == "pocket_region"
    ]
    assert len(pocket_regions) == 1, (pocket_name, pocket_result)
    assert round(float(pocket_regions[0].metadata["pocket_depth_mm"]), 9) == 5.0
    external_contours = [
        entity
        for entity in pocket_result.entities
        if str(entity.metadata.get("import_role", "")) == "cut_external"
    ]
    assert len(external_contours) == 1, (pocket_name, pocket_result)
    assert abs(abs(float(external_contours[0].signed_area(0.05))) - 20000.0) < 0.1

    classification = classify_document_pieces(pocket_document)
    assert len(classification.pieces) == 1, (pocket_name, classification)
    assert classification.pieces[0].inner_ids == (), (pocket_name, classification)
    assert classification.pieces[0].feature_ids == (
        pocket_regions[0].id,
    ), (pocket_name, classification)
    if pocket_name == "edge-open":
        report = validate_document(pocket_document)
        assert not report.by_code("CONTOUR_INTERSECTION"), report
        assert not report.by_code("BRANCH_NODE"), report

    assert round(float(pocket_source.Volume), 9) == source_volume
    assert (
        round(float(pocket_source.BoundBox.XMin), 9),
        round(float(pocket_source.BoundBox.YMin), 9),
        round(float(pocket_source.BoundBox.ZMin), 9),
        round(float(pocket_source.BoundBox.XMax), 9),
        round(float(pocket_source.BoundBox.YMax), 9),
        round(float(pocket_source.BoundBox.ZMax), 9),
    ) == source_bounds

# A mesma chapa pode estar em pé no móvel. O fundo do rebaixo precisa usar a
# exata projeção escolhida para a face larga; projetá-lo outra vez em XY global
# o reduz a uma linha sem área (e desloca rebaixos circulares para fora).
upright_pocket_source = Part.makeBox(200, 100, 18).cut(
    Part.makeBox(60, 40, 5, FreeCAD.Vector(50, 30, 13))
)
upright_pocket_source.rotate(
    FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90.0
)
upright_pocket_result = import_part_shape(
    upright_pocket_source,
    layer_id="layer",
    flatten_solids=True,
)
upright_pocket_regions = [
    entity
    for entity in upright_pocket_result.entities
    if str(entity.metadata.get("import_role", "")) == "pocket_region"
]
assert len(upright_pocket_regions) == 1, upright_pocket_result
upright_pocket_bounds = upright_pocket_regions[0].bounds()
assert sorted(
    (round(upright_pocket_bounds.width, 6), round(upright_pocket_bounds.height, 6))
) == [40.0, 60.0], upright_pocket_bounds
assert abs(float(upright_pocket_regions[0].signed_area(0.05))) > 2399.0

upright_edge_pocket = Part.makeBox(200, 100, 18).cut(
    Part.makeBox(70, 40, 5, FreeCAD.Vector(-1, 30, 13))
)
upright_edge_pocket.rotate(
    FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90.0
)
upright_edge_result = import_part_shape(
    upright_edge_pocket,
    layer_id="layer",
    flatten_solids=True,
)
upright_edge_external = next(
    entity
    for entity in upright_edge_result.entities
    if str(entity.metadata.get("import_role", "")) == "cut_external"
)
upright_edge_regions = [
    entity
    for entity in upright_edge_result.entities
    if str(entity.metadata.get("import_role", "")) == "pocket_region"
]
assert len(upright_edge_regions) == 1, upright_edge_result
assert abs(abs(float(upright_edge_external.signed_area(0.05))) - 20000.0) < 0.1
assert abs(float(upright_edge_regions[0].signed_area(0.05))) > 2759.0

# Rebaixos circulares cegos usam o mesmo plano compartilhado. Eles não podem
# aparecer como anéis soltos no canto da grade nem permanecer como corte
# interno coincidente.
upright_circular_pockets = Part.makeBox(200, 100, 18)
for center_x in (45.0, 155.0):
    upright_circular_pockets = upright_circular_pockets.cut(
        Part.makeCylinder(17.5, 5, FreeCAD.Vector(center_x, 25.0, 13.0))
    )
upright_circular_pockets.rotate(
    FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 0), 90.0
)
upright_circular_result = import_part_shape(
    upright_circular_pockets,
    layer_id="layer",
    flatten_solids=True,
)
circular_regions = [
    entity
    for entity in upright_circular_result.entities
    if str(entity.metadata.get("import_role", "")) == "pocket_region"
]
assert len(circular_regions) == 2, upright_circular_result
assert all(type(entity).__name__ == "CircleEntity" for entity in circular_regions)
assert not [
    entity
    for entity in upright_circular_result.entities
    if str(entity.metadata.get("import_role", "")) == "cut_internal"
], upright_circular_result
upright_circular_outer = next(
    entity
    for entity in upright_circular_result.entities
    if str(entity.metadata.get("import_role", "")) == "cut_external"
)
outer_bounds = upright_circular_outer.bounds()
assert all(
    outer_bounds.contains_bbox(entity.bounds(), 1.0e-7)
    for entity in circular_regions
)

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

# Painéis opostos com furos passantes devem ser projetados a partir das faces
# externas. Um rebaixo gerado depois pelo CAM precisa cair na face correta de
# cada peça; usar +Y para ambos faz duas cópias visuais do mesmo lado.
mirrored_document = FreeCAD.newDocument("WoodCAMMirroredFacesSmoke")
mirrored_root = mirrored_document.addObject("App::DocumentObjectGroup", "MirroredPair")
for suffix, y_start in (("Left", -35.0), ("Right", 20.0)):
    leaf = mirrored_document.addObject("PartDesign::Feature", "Panel" + suffix)
    blank = Part.makeBox(100, 15, 200, FreeCAD.Vector(0, y_start, 0))
    through_hole = Part.makeCylinder(
        2, 15, FreeCAD.Vector(20, y_start, 50), FreeCAD.Vector(0, 1, 0)
    )
    leaf.Shape = blank.cut(through_hole)
    mirrored_root.addObject(leaf)
mirrored_document.recompute()
volumes_before = tuple(round(float(obj.Shape.Volume), 8) for obj in mirrored_root.Group)
mirrored_result = import_freecad_tree(
    (mirrored_root,), layer_id="layer", flatten_solids=True
)
mirrored_holes = {
    entity.metadata["source_tree_instance_id"].split(":")[1]: entity
    for entity in mirrored_result.entities
    if type(entity).__name__ == "CircleEntity"
}
mirrored_outlines = {
    entity.metadata["source_tree_instance_id"].split(":")[1]: entity
    for entity in mirrored_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
}
assert set(mirrored_holes) == {"PanelLeft", "PanelRight"}, mirrored_result
assert set(mirrored_outlines) == set(mirrored_holes), mirrored_result
left_hole_x = mirrored_holes["PanelLeft"].center.x - mirrored_outlines["PanelLeft"].bounds().min_x
right_hole_x = mirrored_holes["PanelRight"].center.x - mirrored_outlines["PanelRight"].bounds().min_x
assert sorted((round(left_hole_x, 5), round(right_hole_x, 5))) == [50.0, 150.0], (
    left_hole_x, right_hole_x
)
assert tuple(round(float(obj.Shape.Volume), 8) for obj in mirrored_root.Group) == volumes_before
panelnest_exact = WoodCAM2DDialog._panelnest_exact_source_entities(
    (SimpleNamespace(object_name="PanelLeft"), SimpleNamespace(object_name="PanelRight")),
    layer_id="layer",
)
assert set(panelnest_exact) == {"PanelLeft", "PanelRight"}, panelnest_exact
panelnest_hole_offsets = []
for name in ("PanelLeft", "PanelRight"):
    entities = panelnest_exact[name]
    hole = next(entity for entity in entities if type(entity).__name__ == "CircleEntity")
    outline = next(
        entity for entity in entities
        if type(entity).__name__ == "PathEntity" and entity.closed
    )
    panelnest_hole_offsets.append(round(hole.center.x - outline.bounds().min_x, 5))
assert sorted(panelnest_hole_offsets) == [50.0, 150.0], panelnest_hole_offsets
FreeCAD.closeDocument(mirrored_document.Name)

# O mesmo contrato vale para painéis horizontais: a face inferior precisa de
# projeção espelhada, embora o sólido já esteja paralelo ao XY global.
horizontal_document = FreeCAD.newDocument("WoodCAMHorizontalFacesSmoke")
horizontal_root = horizontal_document.addObject("App::DocumentObjectGroup", "HorizontalPair")
for suffix, z_start in (("Lower", -35.0), ("Upper", 20.0)):
    leaf = horizontal_document.addObject("PartDesign::Feature", "Board" + suffix)
    blank = Part.makeBox(100, 200, 15, FreeCAD.Vector(0, 0, z_start))
    through_hole = Part.makeCylinder(
        2, 15, FreeCAD.Vector(20, 50, z_start), FreeCAD.Vector(0, 0, 1)
    )
    leaf.Shape = blank.cut(through_hole)
    horizontal_root.addObject(leaf)
horizontal_document.recompute()
horizontal_result = import_freecad_tree(
    (horizontal_root,), layer_id="layer", flatten_solids=True
)
horizontal_holes = {
    entity.metadata["source_tree_instance_id"].split(":")[1]: entity
    for entity in horizontal_result.entities
    if type(entity).__name__ == "CircleEntity"
}
horizontal_outlines = {
    entity.metadata["source_tree_instance_id"].split(":")[1]: entity
    for entity in horizontal_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
}
assert set(horizontal_holes) == {"BoardLower", "BoardUpper"}, horizontal_result
relative_holes = [
    (
        round(hole.center.x - horizontal_outlines[name].bounds().min_x, 5),
        round(hole.center.y - horizontal_outlines[name].bounds().min_y, 5),
    )
    for name, hole in horizontal_holes.items()
]
assert sorted(relative_holes) == [(20.0, 50.0), (20.0, 150.0)], relative_holes
assert all(
    round(outline.bounds().width, 5) == 100.0
    and round(outline.bounds().height, 5) == 200.0
    for outline in horizontal_outlines.values()
), horizontal_outlines
FreeCAD.closeDocument(horizontal_document.Name)

# Um único nó gerado pode conter várias chapas físicas.  Se duas delas têm o
# mesmo XY mas estão em alturas Z distintas, a importação precisa estacioná-las
# separadamente e classificá-las como duas peças, não como chapa + recorte.
stacked_document = FreeCAD.newDocument("WoodCAMStackedTreePartShapeSmoke")
stacked_root = stacked_document.addObject("App::DocumentObjectGroup", "StackedFurniture")
stacked_leaf = stacked_document.addObject("PartDesign::Feature", "StackedLeaf")
lower_board = Part.makeBox(100, 80, 15)
upper_board = Part.makeBox(40, 20, 15, FreeCAD.Vector(0, 0, 30))
stacked_source_shape = Part.makeCompound([lower_board, upper_board])
stacked_leaf.Shape = stacked_source_shape
stacked_root.addObject(stacked_leaf)
stacked_document.recompute()
source_volume_before = round(float(stacked_leaf.Shape.Volume), 9)
source_bounds_before = (
    round(float(stacked_leaf.Shape.BoundBox.XMin), 9),
    round(float(stacked_leaf.Shape.BoundBox.YMin), 9),
    round(float(stacked_leaf.Shape.BoundBox.ZMin), 9),
    round(float(stacked_leaf.Shape.BoundBox.XMax), 9),
    round(float(stacked_leaf.Shape.BoundBox.YMax), 9),
    round(float(stacked_leaf.Shape.BoundBox.ZMax), 9),
)
stacked_result = import_freecad_tree(
    (stacked_root,),
    layer_id="layer",
    flatten_solids=True,
    compound_groups=True,
)
stacked_paths = [
    entity
    for entity in stacked_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
]
assert len(stacked_paths) == 2, stacked_result
stacked_instances = {
    str(entity.metadata.get("source_tree_instance_id", ""))
    for entity in stacked_paths
}
assert len(stacked_instances) == 2, stacked_instances
assert all("component-" in instance for instance in stacked_instances), stacked_instances
stacked_bounds = [entity.bounds() for entity in stacked_paths]
assert not (
    stacked_bounds[0].max_x > stacked_bounds[1].min_x
    and stacked_bounds[1].max_x > stacked_bounds[0].min_x
    and stacked_bounds[0].max_y > stacked_bounds[1].min_y
    and stacked_bounds[1].max_y > stacked_bounds[0].min_y
), stacked_bounds
stacked_classification = classify_document_pieces(
    _TreeImportDocument(stacked_result.entities)
)
assert len(stacked_classification.pieces) == 2, stacked_classification
assert all(not piece.inner_ids for piece in stacked_classification.pieces), stacked_classification
assert round(float(stacked_leaf.Shape.Volume), 9) == source_volume_before
assert (
    round(float(stacked_leaf.Shape.BoundBox.XMin), 9),
    round(float(stacked_leaf.Shape.BoundBox.YMin), 9),
    round(float(stacked_leaf.Shape.BoundBox.ZMin), 9),
    round(float(stacked_leaf.Shape.BoundBox.XMax), 9),
    round(float(stacked_leaf.Shape.BoundBox.YMax), 9),
    round(float(stacked_leaf.Shape.BoundBox.ZMax), 9),
) == source_bounds_before
FreeCAD.closeDocument(stacked_document.Name)

# A identidade por componente é aplicada somente depois da fusão coplanar.
# Assim, o osso continua sendo uma única peça física também pelo caminho da
# árvore e não regride para uma placa mais quatro círculos soltos.
tree_bone_document = FreeCAD.newDocument("WoodCAMTreeDogboneSmoke")
tree_bone_root = tree_bone_document.addObject("App::DocumentObjectGroup", "BoneFurniture")
tree_bone_leaf = tree_bone_document.addObject("PartDesign::Feature", "BoneLeaf")
tree_bone_leaf.Shape = bone_compound
tree_bone_root.addObject(tree_bone_leaf)
tree_bone_document.recompute()
tree_bone_result = import_freecad_tree(
    (tree_bone_root,),
    layer_id="layer",
    flatten_solids=True,
    compound_groups=True,
)
tree_bone_paths = [
    entity
    for entity in tree_bone_result.entities
    if type(entity).__name__ == "PathEntity" and entity.closed
]
assert len(tree_bone_paths) == 1, tree_bone_result
assert not [
    entity
    for entity in tree_bone_result.entities
    if type(entity).__name__ == "CircleEntity"
], tree_bone_result
assert len(
    {
        str(entity.metadata.get("source_tree_instance_id", ""))
        for entity in tree_bone_result.entities
    }
) == 1, tree_bone_result
FreeCAD.closeDocument(tree_bone_document.Name)

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
