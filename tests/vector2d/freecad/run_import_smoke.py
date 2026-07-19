import FreeCAD
import Part
import os
import sys

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.importers.part_shape import import_part_shape
from geometry_reader import resolve_selection_objects


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
