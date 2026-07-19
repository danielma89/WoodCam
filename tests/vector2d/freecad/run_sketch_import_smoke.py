import os
import sys
import math

import FreeCAD
import Part
import Sketcher

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
)

from woodcam_editor.domain.entities import CircleEntity, PathEntity
from woodcam_editor.importers.sketch import import_sketch


document = FreeCAD.newDocument("WoodCAMSketchImportSmoke")
sketch = document.addObject("Sketcher::SketchObject", "Sketch")
points = [
    FreeCAD.Vector(0, 0, 0),
    FreeCAD.Vector(100, 0, 0),
    FreeCAD.Vector(100, 50, 0),
    FreeCAD.Vector(0, 50, 0),
]
for index in range(len(points)):
    sketch.addGeometry(Part.LineSegment(points[index], points[(index + 1) % len(points)]), False)
sketch.addGeometry(Part.Circle(FreeCAD.Vector(20, 20, 0), FreeCAD.Vector(0, 0, 1), 5), False)
sketch.addGeometry(
    Part.ArcOfCircle(
        Part.Circle(FreeCAD.Vector(200, 0, 0), FreeCAD.Vector(0, 0, 1), 20),
        0.0,
        math.pi * 1.5,
    ),
    False,
)
sketch.Placement.Base = FreeCAD.Vector(1000, 2000, 0)
document.recompute()
geometry_count = sketch.GeometryCount

result = import_sketch(sketch, layer_id="layer")
assert sketch.GeometryCount == geometry_count
paths = [entity for entity in result.entities if isinstance(entity, PathEntity)]
circles = [entity for entity in result.entities if isinstance(entity, CircleEntity)]
closed_paths = [path for path in paths if path.closed]
open_paths = [path for path in paths if not path.closed]
assert len(closed_paths) == 1
assert closed_paths[0].start.x == 1000 and closed_paths[0].start.y == 2000
assert len(open_paths) == 1
arc = open_paths[0].spans[0]
assert type(arc).__name__ == "ArcSpan"
assert not arc.clockwise
assert arc.sweep_angle > math.pi
assert len(circles) == 1
assert circles[0].center.x == 1020 and circles[0].center.y == 2020
FreeCAD.closeDocument(document.Name)
print("Sketch snapshot importer smoke: OK")
