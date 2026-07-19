"""Smoke FreeCADCmd: malha real -> desbaste/acabamento -> G-code."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import FreeCAD
import Mesh

from gcode_writer import build_gcode
from woodcam_3d import (
    FinishingOptions,
    RoughingOptions,
    build_3d_finishing_moves,
    build_3d_roughing_moves,
    height_field_from_mesh,
)
from woodcam_3d.freecad_adapter import mesh_data_from_object


doc = FreeCAD.newDocument("WoodCAM3DCAMSmoke")
obj = doc.addObject("Mesh::Feature", "ReliefSmoke")
obj.Mesh = Mesh.Mesh(
    [
        [FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(20, 0, 0), FreeCAD.Vector(0, 20, 4)],
        [FreeCAD.Vector(20, 0, 0), FreeCAD.Vector(20, 20, 4), FreeCAD.Vector(0, 20, 4)],
    ]
)
doc.recompute()
field = height_field_from_mesh(mesh_data_from_object(obj), sampling_mm=1.0)
rough = build_3d_roughing_moves(
    field,
    RoughingOptions(3.0, stepdown=1.5, allowance=0.4, safe_height=5.0),
)
finish = build_3d_finishing_moves(
    field,
    FinishingOptions(3.0, stepover_percent=12.0, safe_height=5.0),
)
assert rough and finish
assert min(move["z"] for move in finish if move.get("z") is not None) >= -4.000001
lines = build_gcode(18000, 350, 1400, 4000, 5, 12, rough + finish)
assert any(line.startswith("G1 X") and " Z" in line for line in lines)
assert lines[-1] == "M30"
FreeCAD.closeDocument(doc.Name)
print("OK: WoodCAM 3D mesh -> rough -> finish -> G-code")
