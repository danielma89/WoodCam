"""Smoke da migracao nao destrutiva para uma unica arvore WoodCAM."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import FreeCAD  # noqa: E402
import Part  # noqa: E402

from woodcam_tree import ensure_woodcam_tree  # noqa: E402


document = FreeCAD.newDocument("WoodCAMTreeSmoke")
legacy_drawing = document.addObject(
    "App::DocumentObjectGroup",
    "WoodCAM2D_VectorDrawing",
)
legacy_drawing.Label = "WoodCAM 2D — Desenho"
vector_feature = document.addObject("Part::Feature", "WoodCAM2D_VectorDocument")
vector_feature.Label = "WoodCAM 2D — Documento vetorial"
vector_feature.Shape = Part.makeBox(10.0, 20.0, 3.0)
legacy_drawing.addObject(vector_feature)

legacy_exchange = document.addObject(
    "App::DocumentObjectGroup",
    "WoodCAM2DPanelNestExchange",
)
legacy_exchange.Label = "WoodCAM 2D — PanelNest (layout do Editor preservado)"
legacy_exchange.addProperty("App::PropertyString", "WoodCAMExchangeType", "WoodCAM")
legacy_exchange.WoodCAMExchangeType = "panelnest_exchange_root"
piece = document.addObject("Part::Feature", "LegacyPanelPiece")
piece.Shape = Part.makeBox(30.0, 40.0, 15.0)
legacy_exchange.addObject(piece)

operations = document.addObject("App::DocumentObjectGroup", "WoodCAM2D_Operations")
operations.Label = "WoodCAM 2D — Operações"
operation = document.addObject("App::FeaturePython", "LegacyCut")
operations.addObject(operation)
work_area = document.addObject("App::DocumentObjectGroup", "WoodCAM2D_WorkArea")
work_area.Label = "WoodCAM 2D — Área de trabalho"
area = document.addObject("Part::Feature", "LegacyWorkArea")
area.Shape = Part.makePlane(100.0, 200.0)
work_area.addObject(area)
legacy_simulation = document.addObject(
    "App::DocumentObjectGroup",
    "WoodCAM2D_Simulation",
)
legacy_simulation.Label = "Simulação"
document.recompute()

vector_volume = float(vector_feature.Shape.Volume)
piece_volume = float(piece.Shape.Volume)
tree = ensure_woodcam_tree(document)
document.recompute()

assert tree.root.Name == "WoodCAM"
assert [child.Label for child in tree.root.Group] == [
    "Peças",
    "Operações",
    "Área de trabalho",
]
assert list(tree.parts.Group) == [vector_feature, piece]
assert list(tree.operations.Group) == [operation, legacy_simulation]
assert list(tree.work_area.Group) == [area]
assert document.getObject("WoodCAM2D_VectorDrawing") is None
assert document.getObject("WoodCAM2DPanelNestExchange") is None
assert tree.parts.WoodCAMExchangeType == "panelnest_exchange_root"
assert abs(vector_feature.Shape.Volume - vector_volume) <= 1e-9
assert abs(piece.Shape.Volume - piece_volume) <= 1e-9

FreeCAD.closeDocument(document.Name)
print("WoodCAM tree migration smoke: OK")
