"""Smoke test do WoodCAM usando um projeto real do PanelNest."""

import json
import os
import sys
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WOODCAM_ROOT = Path(
    os.environ.get(
        "WOODCAM2D_PATH",
        str(Path.home() / "Projetos" / "CNC Marcenaria"),
    )
).expanduser().resolve()
for path in (REPOSITORY_ROOT, WOODCAM_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import panelnest
from panelnest.constants import INTERNAL_PROPERTY_NAME
from geometry_reader import get_selected_geometry
from operations import build_contour_cut_stage, build_machining_stages


def main():
    project_path = os.environ.get("PANELNEST_TEST_PROJECT", "").strip()
    if not project_path:
        raise RuntimeError("Defina PANELNEST_TEST_PROJECT com o arquivo FCStd a validar.")

    document = App.openDocument(str(Path(project_path).expanduser().resolve()))
    try:
        parts = panelnest.collect_parts(include_hidden=True)
        settings = panelnest.get_sheet_settings()
        layout_sheets, _root = panelnest.create_layout_model(parts, settings=settings)
        cam_objects = [
            obj
            for obj in document.Objects
            if str(getattr(obj, INTERNAL_PROPERTY_NAME, "") or "") == "layout_cam_compound"
        ]

        expected_holes = sum(
            len(getattr(placement.part, "holes", None) or [])
            for sheet in layout_sheets
            for placement in sheet.placements
        )
        detected_holes = 0
        drill_moves = 0
        helical_moves = 0
        cut_moves = 0
        ordered = True

        for sheet_index, cam_object in enumerate(cam_objects):
            material_thickness = float(layout_sheets[sheet_index].thickness_mm)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(cam_object)
            geometry = get_selected_geometry()
            detected_holes += len(geometry["holes"])
            hole_stages = build_machining_stages(
                [],
                geometry["holes"],
                final_depth=material_thickness + 0.5,
                stepdown=3.0,
                ramp_length=30.0,
                safe_height=8.0,
                tool_diameter=3.0,
                cut_side="outside",
                material_thickness=material_thickness,
                drill_holes=True,
                cut_enabled=False,
            )
            hole_moves = hole_stages.get("holes", [])
            current_drills = [
                move for move in hole_moves if move["type"] == "feed_drill"
            ]
            current_helices = [
                move for move in hole_moves if move["type"] == "feed_helix"
            ]
            contour_moves = build_contour_cut_stage(
                geometry["contours"],
                [],
                final_depth=material_thickness + 0.5,
                stepdown=3.0,
                ramp_length=30.0,
                safe_height=8.0,
                tool_diameter=3.0,
                outer_cut_side="outside",
                material_thickness=material_thickness,
                inner_holes=geometry["holes"],
            )
            current_cuts = [
                move for move in contour_moves if move["type"] == "feed_cut"
            ]
            drill_moves += len(current_drills)
            helical_moves += len(current_helices)
            cut_moves += len(current_cuts)
            ordered = (
                ordered
                and list(hole_stages) == ["holes"]
                and not any(move["type"] == "feed_helix" for move in contour_moves)
            )

        result = {
            "cam_sheets": len(cam_objects),
            "expected_holes": expected_holes,
            "detected_holes": detected_holes,
            "drill_moves": drill_moves,
            "helical_moves": helical_moves,
            "cut_moves": cut_moves,
            "drilling_precedes_cutting": ordered,
        }
        print("PANELNEST_WOODCAM_PROJECT_CHECK=" + json.dumps(result))
        if detected_holes != expected_holes or not ordered or cut_moves <= 0:
            raise AssertionError(result)
    finally:
        Gui.Selection.clearSelection()
        App.closeDocument(document.Name)
        try:
            from PySide import QtCore
        except ImportError:
            from PySide6 import QtCore
        QtCore.QTimer.singleShot(100, Gui.getMainWindow().close)


main()
