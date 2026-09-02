"""Smoke test da integração PanelNest + WoodCAM 2D em uma sessão GUI."""

import json
import os
import tempfile
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Sketcher
try:
    from PySide import QtCore
except ImportError:
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore


def _mark_panelnest_object(obj, managed_type):
    obj.addProperty("App::PropertyString", "PanelNestManagedType", "Panel Nest")
    obj.PanelNestManagedType = managed_type


def main():
    workbenches = dict(Gui.listWorkbenches())
    Gui.activateWorkbench("PanelNestWorkbench")
    commands = set(Gui.listCommands())
    document = App.newDocument("PanelNestWoodCAMSmokeTest")

    layout_root = document.addObject("App::Part", "LayoutPanelNestTeste")
    _mark_panelnest_object(layout_root, "layout_root")
    sheet_base = document.addObject("Part::Box", "PanelNestBaseChapaTeste")
    sheet_base.Length = 2750
    sheet_base.Width = 1850
    sheet_base.Height = 15
    _mark_panelnest_object(sheet_base, "layout_sheet")
    layout_root.addObject(sheet_base)

    cam_shape_a = Part.makeBox(100, 50, 15)
    through_hole = Part.makeCylinder(
        4,
        15,
        App.Vector(20, 20, 15),
        App.Vector(0, 0, -1),
    )
    blind_hole = Part.makeCylinder(
        5,
        5,
        App.Vector(70, 20, 15),
        App.Vector(0, 0, -1),
    )
    cam_shape_a = cam_shape_a.cut(through_hole).cut(blind_hole)
    cam_shape_b = Part.makeBox(120, 40, 15, App.Vector(150, 0, 0))
    cam_feature = document.addObject("Part::Feature", "PanelNestCAMChapaTeste")
    cam_feature.Shape = Part.makeCompound([cam_shape_a, cam_shape_b])
    _mark_panelnest_object(cam_feature, "layout_cam_compound")
    document.recompute()

    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(layout_root)
    Gui.runCommand("PanelNest_OpenWoodCAM2D")
    import ui as woodcam_ui
    import geometry_reader as woodcam_geometry

    selected_geometry = woodcam_geometry.get_selected_geometry()
    contours = selected_geometry["contours"]
    holes = selected_geometry["holes"]
    contour_widths = [
        max(point[0] for point in contour) - min(point[0] for point in contour)
        for contour in contours
    ]

    woodcam_dialog_opened = bool(
        woodcam_ui._DIALOG_INSTANCE is not None
        and woodcam_ui._DIALOG_INSTANCE.isVisible()
    )
    dialog = woodcam_ui._DIALOG_INSTANCE
    operation_types = [
        dialog.operation_combo.itemText(index)
        for index in range(dialog.operation_combo.count())
    ]
    operation_tabs = [
        dialog.operation_tabs.tabText(index)
        for index in range(dialog.operation_tabs.count())
    ]
    tab_indexes = {
        dialog.operation_tabs.tabText(index): index
        for index in range(dialog.operation_tabs.count())
    }
    operation_tab_icons = all(
        not dialog.operation_tabs.tabIcon(tab_indexes[tab_name]).isNull()
        for tab_name in (
            "Trabalho",
            "Material",
            "Fresas",
            "Corte",
            "Furo",
            "Preenchimento",
        )
    )
    dialog.job_type_buttons["single_sided"].setChecked(True)
    dialog.job_z_zero_surface.setChecked(True)
    dialog.job_origin_buttons["bottom_left"].setChecked(True)
    dialog.job_use_selection_bounds_origin.setChecked(False)
    dialog.z_zero_surface.setChecked(True)
    dialog.origin_buttons["bottom_left"].setChecked(True)
    dialog.use_selection_bounds_origin.setChecked(False)
    origin_diagram = next(
        (
            diagram
            for diagram in getattr(dialog, "setup_diagrams", [])
            if getattr(diagram, "kind", "") == "origin"
        ),
        None,
    )
    dialog.job_origin_buttons["top_right"].setChecked(True)
    dialog.origin_buttons["top_right"].setChecked(True)
    Gui.updateGui()
    top_right_origin_pixmap = (
        origin_diagram._load_asset_pixmap(
            woodcam_ui.ORIGIN_ANCHOR_ASSET_FILENAMES["top_right"]
        )
        if origin_diagram is not None
        else None
    )
    origin_dynamic_images = (
        dialog._job_origin_anchor() == "top_right"
        and dialog._origin_anchor() == "top_right"
        and top_right_origin_pixmap is not None
        and not top_right_origin_pixmap.isNull()
    )
    dialog.job_origin_buttons["bottom_left"].setChecked(True)
    dialog.origin_buttons["bottom_left"].setChecked(True)
    job_setup_controls = (
        sorted(dialog.job_type_buttons) == [
            "double_sided",
            "single_sided",
        ]
        and not dialog.job_type_buttons["double_sided"].isEnabled()
        and dialog.job_z_zero_surface.text() == "Superfície do material"
        and dialog.job_z_zero_bed.text() == "Mesa da máquina"
        and sorted(dialog.job_origin_buttons) == [
            "bottom_center",
            "bottom_left",
            "bottom_right",
            "center",
            "middle_left",
            "middle_right",
            "top_center",
            "top_left",
            "top_right",
        ]
        and "job_width" in dialog.fields
        and "job_height" in dialog.fields
        and "job_depth" in dialog.fields
        and "job_origin_x" in dialog.fields
        and "job_origin_y" in dialog.fields
    )
    material_setup_controls = (
        "material_thickness" in dialog.fields
        and "retract_height" in dialog.fields
        and "safe_height" in dialog.fields
        and "model_gap_above" in dialog.fields
        and "model_gap_below" in dialog.fields
        and dialog.z_zero_surface.text() == "Superfície do material"
        and dialog.z_zero_bed.text() == "Mesa da máquina"
        and sorted(dialog.origin_buttons) == [
            "bottom_center",
            "bottom_left",
            "bottom_right",
            "center",
            "middle_left",
            "middle_right",
            "top_center",
            "top_left",
            "top_right",
        ]
        and "start_x" in dialog.fields
        and "start_y" in dialog.fields
        and hasattr(dialog, "model_position_slider")
        and hasattr(dialog, "home_z_display")
    )
    tab_specific_controls = {
        "cut": {
            "start_depth",
            "cut_depth",
            "tool_diameter",
            "stepdown",
            "feed_xy",
            "feed_z",
            "rapid_feed",
            "rpm",
            "ramp_length",
            "corner_angle_threshold",
            "corner_feed_percent",
            "corner_slowdown_distance",
        }.issubset(dialog.operation_fields["cut"]),
        "holes": {
            "start_depth",
            "cut_depth",
            "tool_diameter",
            "stepdown",
            "feed_xy",
            "feed_z",
            "rapid_feed",
            "rpm",
            "helix_pitch",
            "helix_stepover_percent",
            "peck_step",
            "peck_retract_clearance",
            "dwell_seconds",
        }.issubset(dialog.operation_fields["holes"]),
        "pocket": {
            "start_depth",
            "cut_depth",
            "tool_diameter",
            "stepdown",
            "feed_xy",
            "feed_z",
            "rapid_feed",
            "rpm",
            "ramp_length",
            "pocket_stepover_percent",
            "pocket_allowance",
            "pocket_raster_angle",
        }.issubset(dialog.operation_fields["pocket"]),
    }
    dialog.operation_tabs.setCurrentIndex(tab_indexes["Corte"])
    cut_side_extents = {}
    inner_cut_precedes_outer = True
    for index, operation_name in enumerate(operation_types):
        dialog.operation_combo.setCurrentIndex(index)
        operation_settings = dialog._collect_settings()
        operation_moves = dialog._build_moves_from_selection(operation_settings)
        xs = [
            float(move["x"])
            for move in operation_moves
            if move.get("x") is not None and move["type"] == "feed_cut"
        ]
        cut_side_extents[operation_name] = [
            round(min(xs), 3),
            round(max(xs), 3),
        ]
        hole_cut_indexes = [
            move_index
            for move_index, move in enumerate(operation_moves)
            if move["type"] == "feed_cut"
            and (
                ((move["x"] - 20.0) ** 2 + (move["y"] - 20.0) ** 2) ** 0.5 <= 1.1
                or ((move["x"] - 70.0) ** 2 + (move["y"] - 20.0) ** 2) ** 0.5 <= 2.1
            )
        ]
        outer_cut_indexes = [
            move_index
            for move_index, move in enumerate(operation_moves)
            if move["type"] == "feed_cut"
            and move_index not in hole_cut_indexes
        ]
        inner_cut_precedes_outer = (
            inner_cut_precedes_outer
            and bool(hole_cut_indexes)
            and bool(outer_cut_indexes)
            and max(hole_cut_indexes) < min(outer_cut_indexes)
        )
    corner_slowdown_generated = any(
        move.get("corner_slowdown")
        and abs(float(move.get("feed_scale", 0.0)) - 0.4) < 1e-9
        for move in operation_moves
    )
    cut_settings_for_apply = dict(operation_settings)
    cut_moves_for_apply = list(operation_moves)
    dialog.z_zero_bed.setChecked(True)
    machine_bed_settings = dialog._collect_settings()
    machine_bed_moves = dialog._build_moves_from_selection(machine_bed_settings)
    machine_bed_z_values = [
        float(move["z"])
        for move in machine_bed_moves
        if move.get("z") is not None
    ]
    machine_bed_z_zero_transform = (
        round(max(machine_bed_z_values), 3) == 23.0
        and round(min(machine_bed_z_values), 3) == -0.5
        and machine_bed_settings["z_zero_mode"] == "machine_bed"
    )
    dialog.z_zero_surface.setChecked(True)

    dialog.operation_tabs.setCurrentIndex(tab_indexes["Furo"])
    dialog.use_helical_drilling.setChecked(True)
    dialog.fields["start_x"].setText("12")
    dialog.fields["start_y"].setText("34")
    hole_settings = dialog._collect_settings()
    hole_stages = dialog._build_stage_moves_from_selection(hole_settings)
    configured_start = {"type": "rapid", "x": 12.0, "y": 34.0, "z": 8.0}
    hole_uses_start = all(
        moves[0] == configured_start and moves[-1] == configured_start
        for moves in hole_stages.values()
    )
    helical_hole_generated = any(
        move["type"] == "feed_helix"
        for move in hole_stages["holes"]
    )
    dialog.operation_fields["holes"]["tool_diameter"].setText("12")
    dialog.peck_enabled.setChecked(True)
    dialog.operation_fields["holes"]["peck_step"].setText("3")
    dialog.operation_fields["holes"]["peck_retract_clearance"].setText("0.5")
    dialog.dwell_enabled.setChecked(True)
    dialog.operation_fields["holes"]["dwell_seconds"].setText("0.2")
    peck_settings = dialog._collect_settings()
    peck_moves = dialog._build_moves_from_selection(peck_settings)
    peck_dwell_generated = (
        sum(move["type"] == "feed_drill" for move in peck_moves) > len(holes)
        and sum(move["type"] == "dwell" for move in peck_moves)
        == sum(move["type"] == "feed_drill" for move in peck_moves)
    )

    dialog.operation_tabs.setCurrentIndex(tab_indexes["Preenchimento"])
    dialog.pocket_strategy_combo.setCurrentIndex(0)
    dialog.pocket_direction_combo.setCurrentIndex(0)
    pocket_direction_first = dialog.operation_diagrams["pocket"]._state()
    dialog.pocket_direction_combo.setCurrentIndex(1)
    pocket_direction_second = dialog.operation_diagrams["pocket"]._state()
    pocket_diagram_direction_changes = (
        pocket_direction_first.get("climb") is True
        and pocket_direction_second.get("climb") is False
    )
    dialog.pocket_strategy_combo.setCurrentIndex(1)
    dialog.operation_fields["pocket"]["pocket_stepover_percent"].setText("40")
    dialog.operation_fields["pocket"]["pocket_allowance"].setText("0")
    dialog.operation_fields["pocket"]["pocket_raster_angle"].setText("0")
    dialog.operation_fields["pocket"]["cut_depth"].setText("4")
    dialog.operation_fields["pocket"]["stepdown"].setText("2")
    pocket_settings = dialog._collect_settings()
    pocket_stages = dialog._build_stage_moves_from_selection(pocket_settings)
    pocket_uses_start = all(
        moves[0] == configured_start and moves[-1] == configured_start
        for moves in pocket_stages.values()
    )
    pocket_xs = [
        move["x"]
        for move in pocket_stages["pocket"]
        if move.get("x") is not None and move["type"] == "feed_cut"
    ]
    pocket_continuous_links = any(
        move.get("pocket_link")
        for move in pocket_stages["pocket"]
    )
    pocket_area_sequence = []
    for move in pocket_stages["pocket"][1:-1]:
        if move["type"] != "rapid" or move.get("x") is None:
            continue
        area_name = "left" if move["x"] < 130.0 else "right"
        if not pocket_area_sequence or pocket_area_sequence[-1] != area_name:
            pocket_area_sequence.append(area_name)
    pocket_finishes_area_before_next = pocket_area_sequence == ["left", "right"]

    applied_hole_1 = dialog._create_applied_operation(
        hole_settings,
        hole_stages["holes"],
    )
    applied_cut_1 = dialog._create_applied_operation(
        cut_settings_for_apply,
        cut_moves_for_apply,
    )
    applied_hole_2 = dialog._create_applied_operation(
        hole_settings,
        hole_stages["holes"],
    )
    operations_root = document.getObject("WoodCAM2D_Operations")
    applied_operation_labels = [
        operation.Label
        for operation in operations_root.Group
    ]
    applied_operations_persist = (
        operations_root.Label == "WoodCAM 2D — Operações"
        and applied_operation_labels == [
            "Furo 01",
            "Corte 01",
            "Furo 02",
        ]
        and applied_hole_1.WoodCAMSequence == 1
        and applied_cut_1.WoodCAMSequence == 1
        and applied_hole_2.WoodCAMSequence == 2
        and applied_hole_1.MoveCount == len(hole_stages["holes"])
        and json.loads(applied_hole_1.SettingsJSON)["operation_mode"]
        == "holes"
        and len(json.loads(applied_cut_1.MovesJSON))
        == len(cut_moves_for_apply)
        and bool(applied_hole_1.Group)
        and bool(applied_cut_1.Group)
        and all(
            child.WoodCAMAppliedPath
            for operation in (
                applied_hole_1,
                applied_cut_1,
                applied_hole_2,
            )
            for child in operation.Group
        )
    )
    work_area_preview_group = document.addObject(
        "App::DocumentObjectGroup",
        "WoodCAM2D_Preview",
    )
    work_area_settings = dict(cut_settings_for_apply)
    work_area_settings["job_width"] = 320.0
    work_area_settings["job_height"] = 180.0
    work_area_settings["origin_anchor"] = "center"
    work_area_object = dialog._add_work_area_preview(
        document,
        work_area_preview_group,
        work_area_settings,
    )
    work_area_preview_created = (
        work_area_object is not None
        and work_area_object.Label == "Área de trabalho"
        and work_area_object in work_area_preview_group.Group
    )
    dialog._clear_existing_preview(document)
    temporary_preview = document.addObject(
        "App::DocumentObjectGroup",
        "WoodCAM2D_Preview",
    )
    temporary_preview_child = document.addObject(
        "Part::Feature",
        "PreviewPersistenceCheck",
    )
    temporary_preview.addObject(temporary_preview_child)
    dialog._clear_existing_preview(document)
    preview_cleanup_preserves_operations = (
        document.getObject("WoodCAM2D_Preview") is None
        and document.getObject("WoodCAM2D_Operations") is operations_root
        and applied_hole_1 in operations_root.Group
        and applied_cut_1 in operations_root.Group
        and applied_hole_2 in operations_root.Group
    )

    with tempfile.TemporaryDirectory() as temporary_directory:
        base_path = Path(temporary_directory) / "trabalho.nc"
        first_hole_path = dialog._stage_output_path(base_path, "holes")
        first_hole_path.write_text("ocupado", encoding="utf-8")
        next_hole_path = dialog._available_output_path(first_hole_path)
        non_overwrite_name = next_hole_path.name

    outer_wire = Part.makePolygon(
        [
            App.Vector(0, 0, 0),
            App.Vector(100, 0, 0),
            App.Vector(100, 60, 0),
            App.Vector(0, 60, 0),
            App.Vector(0, 0, 0),
        ]
    )
    selected_wire = Part.makePolygon(
        [
            App.Vector(20, 20, 0),
            App.Vector(45, 20, 0),
            App.Vector(45, 35, 0),
            App.Vector(20, 35, 0),
            App.Vector(20, 20, 0),
        ]
    )
    partial_selection_feature = document.addObject(
        "Part::Feature",
        "WoodCAMPartialEdgeSelection",
    )
    partial_selection_feature.Shape = Part.makeCompound([outer_wire, selected_wire])
    document.recompute()
    selected_edge_names = []
    for edge_index, edge in enumerate(partial_selection_feature.Shape.Edges, start=1):
        xs = [vertex.Point.x for vertex in edge.Vertexes]
        ys = [vertex.Point.y for vertex in edge.Vertexes]
        if min(xs) >= 19.999 and max(xs) <= 45.001 and min(ys) >= 19.999:
            selected_edge_names.append(f"Edge{edge_index}")

    Gui.Selection.clearSelection()
    for edge_name in selected_edge_names:
        Gui.Selection.addSelection(partial_selection_feature, edge_name)
    partial_geometry = woodcam_geometry.get_selected_geometry()
    partial_contour = partial_geometry["contours"][0]
    partial_selection_only = (
        len(partial_geometry["contours"]) == 1
        and round(max(point[0] for point in partial_contour) - min(point[0] for point in partial_contour), 3)
        == 25.0
        and round(max(point[1] for point in partial_contour) - min(point[1] for point in partial_contour), 3)
        == 15.0
    )

    Gui.Selection.clearSelection()
    for edge_name in selected_edge_names[:-1]:
        Gui.Selection.addSelection(partial_selection_feature, edge_name)
    partial_open_selection_blocked = False
    try:
        woodcam_geometry.get_selected_geometry()
    except ValueError as error:
        partial_open_selection_blocked = "não formam um contorno fechado" in str(error)

    circle_feature = document.addObject(
        "Part::Feature",
        "WoodCAMSketchCircleSelection",
    )
    circle_wires = [
        Part.Wire(
            [
                Part.makeCircle(
                    radius,
                    App.Vector(center_x, center_y, 0),
                )
            ]
        )
        for center_x, center_y, radius in (
            (10.0, 10.0, 2.0),
            (30.0, 10.0, 3.0),
            (10.0, 30.0, 4.0),
            (30.0, 30.0, 5.0),
        )
    ]
    circle_feature.Shape = Part.makeCompound(circle_wires)
    document.recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(circle_feature)
    circle_object_geometry = woodcam_geometry.get_selected_geometry()
    sketch_circles_become_holes = (
        not circle_object_geometry["contours"]
        and sorted(
            round(hole["diameter_mm"], 3)
            for hole in circle_object_geometry["holes"]
        )
        == [4.0, 6.0, 8.0, 10.0]
    )

    Gui.Selection.clearSelection()
    for edge_index in range(1, 5):
        Gui.Selection.addSelection(circle_feature, f"Edge{edge_index}")
    circle_edge_geometry = woodcam_geometry.get_selected_geometry()
    selected_circle_edges_become_holes = (
        not circle_edge_geometry["contours"]
        and len(circle_edge_geometry["holes"]) == 4
    )

    source_circle_sketch = document.addObject(
        "Sketcher::SketchObject",
        "WoodCAMCircleSourceSketch",
    )
    for center_x, center_y, radius in (
        (70.0, 10.0, 2.0),
        (90.0, 10.0, 3.0),
        (70.0, 30.0, 4.0),
        (90.0, 30.0, 5.0),
    ):
        source_circle_sketch.addGeometry(
            Part.Circle(
                App.Vector(center_x, center_y, 0),
                App.Vector(0, 0, 1),
                radius,
            ),
            False,
        )
    document.recompute()
    external_circle_sketch = document.addObject(
        "Sketcher::SketchObject",
        "WoodCAMExternalCircleSketch",
    )
    for edge_index in range(1, 5):
        external_circle_sketch.addExternal(
            source_circle_sketch.Name,
            f"Edge{edge_index}",
        )
    document.recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(external_circle_sketch)
    external_circle_geometry = woodcam_geometry.get_selected_geometry()
    external_sketch_circles_become_holes = (
        not external_circle_geometry["contours"]
        and sorted(
            round(hole["diameter_mm"], 3)
            for hole in external_circle_geometry["holes"]
        )
        == [4.0, 6.0, 8.0, 10.0]
    )

    screenshot_directory = os.environ.get("PANELNEST_GUI_SCREENSHOT_DIR", "").strip()
    if screenshot_directory:
        screenshot_root = Path(screenshot_directory).expanduser()
        screenshot_root.mkdir(parents=True, exist_ok=True)
        for tab_name in (
            "Trabalho",
            "Material",
            "Fresas",
            "Corte",
            "Furo",
            "Preenchimento",
            "Simulação e Salvar",
        ):
            dialog.operation_tabs.setCurrentIndex(tab_indexes[tab_name])
            Gui.updateGui()
            dialog.grab().save(
                str(
                    screenshot_root
                    / f"woodcam-{tab_name.lower().replace(' ', '-')}.png"
                )
            )

    with tempfile.TemporaryDirectory() as temporary_directory:
        document_path = Path(temporary_directory) / "operacoes-aplicadas.FCStd"
        document.recompute()
        document.saveAs(str(document_path))
        document_name = document.Name
        App.closeDocument(document_name)
        document = App.openDocument(str(document_path))
        reopened_operations_root = document.getObject(
            "WoodCAM2D_Operations"
        )
        reopened_operations = (
            list(reopened_operations_root.Group)
            if reopened_operations_root is not None
            else []
        )
        applied_operations_survive_reopen = (
            [operation.Label for operation in reopened_operations]
            == ["Furo 01", "Corte 01", "Furo 02"]
            and all(operation.Group for operation in reopened_operations)
            and json.loads(reopened_operations[0].SettingsJSON)[
                "operation_mode"
            ]
            == "holes"
            and json.loads(reopened_operations[1].SettingsJSON)[
                "operation_mode"
            ]
            == "cut"
        )

    dialog.operation_tabs.setCurrentIndex(tab_indexes["Trabalho"])
    Gui.updateGui()
    result = {
        "panelnest_workbench": "PanelNestWorkbench" in workbenches,
        "woodcam_workbench": "WoodCAM2DWorkbench" in workbenches,
        "panelnest_woodcam_command": "PanelNest_OpenWoodCAM2D" in commands,
        "legacy_woodcam_command": "WoodCAM2D_OpenDialog" in commands,
        "woodcam_dialog_opened": woodcam_dialog_opened,
        "operation_tabs": operation_tabs,
        "operation_tab_icons": operation_tab_icons,
        "origin_dynamic_images": origin_dynamic_images,
        "job_setup_controls": job_setup_controls,
        "material_setup_controls": material_setup_controls,
        "tab_specific_controls": tab_specific_controls,
        "operation_types": operation_types,
        "machine_limits_hidden": (
            "machine_x_size" not in dialog.fields
            and "machine_y_size" not in dialog.fields
        ),
        "operation_diagrams": sorted(dialog.operation_diagrams) == [
            "cut",
            "holes",
            "pocket",
        ],
        "setup_diagram_count": len(dialog.setup_diagrams) >= 20,
        "tool_database_controls": (
            hasattr(dialog, "tool_list")
            and dialog.tool_list.count() >= 6
            and "Fresa 6 mm MDF" in dialog.tool_database
            and "V-Bit 90° 6 mm" in dialog.tool_database
            and dialog.tool_type_combo.findData("end_mill") >= 0
            and dialog.tool_type_combo.findData("v_bit") >= 0
            and dialog.tool_type_combo.findData("drill") >= 0
            and "notes" in getattr(dialog, "tool_database", {}).get("Fresa 6 mm MDF", {})
            and hasattr(dialog, "tool_type_diagram")
            and not hasattr(dialog, "tool_concept_label")
            and bool(dialog.tool_type_combo.itemData(
                dialog.tool_type_combo.findData("end_mill"),
                QtCore.Qt.ToolTipRole,
            ))
            and not dialog.tool_list.item(0).icon().isNull()
            and all(
                key in dialog.tool_editor_fields
                for key in (
                    "included_angle",
                    "stepover",
                    "stepover_percent",
                    "tool_number",
                )
            )
            and all(
                combo.findText("Fresa 6 mm MDF") >= 0
                for combo in dialog.operation_tool_combos.values()
            )
        ),
        "tool_reorder_controls": (
            hasattr(dialog, "tool_list")
            and dialog.tool_list.dragEnabled()
            and dialog.tool_list.acceptDrops()
            and hasattr(dialog, "_tool_list_rows_moved")
            and hasattr(dialog, "tool_order")
        ),
        "work_tab_actions": (
            dialog.apply_button.isEnabled()
            and dialog.preview_button.isEnabled()
            and not dialog.simulate_button.isEnabled()
            and not dialog.generate_button.isEnabled()
            and not dialog.stop_sim_button.isVisible()
        ),
        "applied_operation_labels": applied_operation_labels,
        "applied_operations_persist": applied_operations_persist,
        "work_area_preview_created": work_area_preview_created,
        "preview_cleanup_preserves_operations": (
            preview_cleanup_preserves_operations
        ),
        "applied_operations_survive_reopen": (
            applied_operations_survive_reopen
        ),
        "redundant_compensation_removed": not hasattr(dialog, "compensate_external"),
        "redundant_depth_extra_removed": "depth_extra" not in dialog.fields,
        "detected_holes": sorted([
            {
                "diameter": round(float(hole["diameter_mm"]), 3),
                "depth": round(float(hole["depth_mm"]), 3),
            }
            for hole in holes
        ], key=lambda item: item["diameter"]),
        "inner_cut_precedes_outer": inner_cut_precedes_outer,
        "corner_slowdown_generated": corner_slowdown_generated,
        "machine_bed_z_zero_transform": machine_bed_z_zero_transform,
        "helical_hole_generated": helical_hole_generated,
        "peck_dwell_generated": peck_dwell_generated,
        "hole_tab_only": list(hole_stages) == ["holes"],
        "pocket_tab_only": list(pocket_stages) == ["pocket"],
        "pocket_continuous_links": pocket_continuous_links,
        "pocket_finishes_area_before_next": pocket_finishes_area_before_next,
        "pocket_diagram_direction_changes": pocket_diagram_direction_changes,
        "partial_edge_selection_only": partial_selection_only,
        "partial_open_selection_blocked": partial_open_selection_blocked,
        "sketch_circles_become_holes": sketch_circles_become_holes,
        "selected_circle_edges_become_holes": selected_circle_edges_become_holes,
        "external_sketch_circles_become_holes": external_sketch_circles_become_holes,
        "configured_start_used": hole_uses_start and pocket_uses_start,
        "pocket_extent": [
            round(min(pocket_xs), 3),
            round(max(pocket_xs), 3),
        ],
        "non_overwrite_name": non_overwrite_name,
        "panelnest_root_uses_cam_only": (
            len(contours) == 2
            and max(contour_widths) <= 120.001
            and all(width < 2750 for width in contour_widths)
        ),
        "cut_side_extents": cut_side_extents,
    }
    print("PANELNEST_GUI_CHECK=" + json.dumps(result))
    woodcam_ui._DIALOG_INSTANCE.hide()
    App.closeDocument(document.Name)

    QtCore.QTimer.singleShot(100, Gui.getMainWindow().close)

    if (
        not result["panelnest_workbench"]
        or not result["panelnest_woodcam_command"]
        or not result["woodcam_dialog_opened"]
        or result["operation_tabs"]
        != [
            "Trabalho",
            "Material",
            "Fresas",
            "Corte",
            "Furo",
            "Preenchimento",
            "Simulação e Salvar",
        ]
        or not result["operation_tab_icons"]
        or not result["origin_dynamic_images"]
        or not result["job_setup_controls"]
        or not result["material_setup_controls"]
        or not all(result["tab_specific_controls"].values())
        or result["operation_types"] != [
            "Corte externo",
            "Corte sobre a linha",
            "Corte interno",
        ]
        or not result["machine_limits_hidden"]
        or not result["operation_diagrams"]
        or not result["setup_diagram_count"]
        or not result["tool_database_controls"]
        or not result["tool_reorder_controls"]
        or not result["work_tab_actions"]
        or not result["applied_operations_persist"]
        or not result["work_area_preview_created"]
        or not result["preview_cleanup_preserves_operations"]
        or not result["applied_operations_survive_reopen"]
        or not result["redundant_compensation_removed"]
        or result["detected_holes"] != [
            {"diameter": 8.0, "depth": 15.0},
            {"diameter": 10.0, "depth": 5.0},
        ]
        or not result["inner_cut_precedes_outer"]
        or not result["corner_slowdown_generated"]
        or not result["machine_bed_z_zero_transform"]
        or not result["helical_hole_generated"]
        or not result["hole_tab_only"]
        or not result["pocket_tab_only"]
        or not result["pocket_continuous_links"]
        or not result["pocket_finishes_area_before_next"]
        or not result["pocket_diagram_direction_changes"]
        or not result["partial_edge_selection_only"]
        or not result["partial_open_selection_blocked"]
        or not result["sketch_circles_become_holes"]
        or not result["selected_circle_edges_become_holes"]
        or not result["external_sketch_circles_become_holes"]
        or not result["configured_start_used"]
        or result["pocket_extent"] != [3.0, 267.0]
        or result["non_overwrite_name"] != "trabalho_furos_02.nc"
        or not result["panelnest_root_uses_cam_only"]
        or result["cut_side_extents"] != {
            "Corte externo": [-3.0, 273.0],
            "Corte sobre a linha": [0.0, 270.0],
            "Corte interno": [3.0, 267.0],
        }
    ):
        raise AssertionError(result)


main()
