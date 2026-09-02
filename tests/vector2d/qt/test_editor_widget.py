"""Offscreen interaction tests for the document-backed canvas.

Run with::

    QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests/vector2d/qt
"""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtCore, QtTest, QtWidgets
except ImportError:  # pragma: no cover - depends on the FreeCAD Python runtime
    QtCore = QtTest = QtWidgets = None


@unittest.skipIf(QtWidgets is None, "PySide6/QtTest indisponível")
class EditorWidgetInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from woodcam_editor.application import EditorMode
        from woodcam_editor.domain import (
            PathEntity,
            AddEntitiesCommand,
            CompositeCommand,
            GroupEntity,
            Piece2D,
            ReplacePiecesCommand,
            SetDocumentMetadataCommand,
            SetWorkAreaCommand,
            Vec2,
            VectorDocument,
            WorkArea,
        )
        from woodcam_editor.presentation.widget import Editor2DWidget

        cls.EditorMode = EditorMode
        cls.PathEntity = PathEntity
        cls.AddEntitiesCommand = AddEntitiesCommand
        cls.CompositeCommand = CompositeCommand
        cls.GroupEntity = GroupEntity
        cls.Piece2D = Piece2D
        cls.ReplacePiecesCommand = ReplacePiecesCommand
        cls.SetDocumentMetadataCommand = SetDocumentMetadataCommand
        cls.SetWorkAreaCommand = SetWorkAreaCommand
        cls.Vec2 = Vec2
        cls.VectorDocument = VectorDocument
        cls.WorkArea = WorkArea
        cls.Editor2DWidget = Editor2DWidget
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        document = self.VectorDocument.create_default(self.WorkArea(0.0, 0.0, 300.0, 200.0))
        path = self.PathEntity.from_points(
            document.active_layer_id,
            (self.Vec2(50.0, 100.0), self.Vec2(250.0, 100.0)),
        )
        document.add_entities((path,))
        self.document = document
        self.path_id = path.id
        self.widget = self.Editor2DWidget(document)
        self.widget.resize(900, 620)
        self.widget.show()
        self.widget.fit_work_area()
        self.app.processEvents()

    def test_exact_panel_applies_percentage_scale_as_one_undo(self):
        original = self.document.get_entity(self.path_id)
        self.widget.controller.selection.replace((self.path_id,))
        self.app.processEvents()

        self.widget.properties_panel.scale_percent_field.setValue(50.0)
        self.widget.properties_panel.apply_scale_button.click()
        self.app.processEvents()

        scaled = self.document.get_entity(self.path_id)
        self.assertAlmostEqual(scaled.bounds().width, original.bounds().width * 0.5)
        self.assertTrue(
            scaled.bounds().center.almost_equals(original.bounds().center, 1e-9)
        )
        self.widget.controller.undo()
        self.assertEqual(self.document.get_entity(self.path_id), original)

    def tearDown(self):
        self.widget.close()
        self.app.processEvents()

    def viewport_point(self, x, y):
        return self.widget.view.mapFromScene(QtCore.QPointF(x, y))

    def test_single_click_selects_whole_entity(self):
        point = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertFalse(self.widget.overlays.node_items)

    def test_visual_cadence_follows_high_refresh_display(self):
        cadence = self.widget.view._frame_interval_for_refresh_rate
        self.assertEqual(cadence(60.0), 16)
        self.assertEqual(cadence(144.0), 7)
        self.assertEqual(cadence(165.0), 6)
        self.assertEqual(cadence(240.0), 4)
        self.assertEqual(cadence(0.0), 16)

    def test_external_cam_point_capture_is_modal_and_never_mutates_vectors(self):
        captured = []
        cancelled = []
        revision_before = self.document.revision
        self.widget.controller.selection.select_only(self.path_id)

        self.widget.begin_point_capture(
            captured.append,
            cancel_callback=lambda: cancelled.append(True),
            status="Posicione a tab",
        )
        self.assertTrue(self.widget.tool_manager.point_capture_active)
        self.assertIn("Posicione", self.widget.mode_label.text())

        point = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseClick(
            self.widget.view.viewport(),
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
            point,
        )
        self.app.processEvents()
        self.assertEqual(len(captured), 1)
        self.assertLess(abs(captured[0].scene_pos.x() - 150.0), 0.5)
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))
        self.assertEqual(self.document.revision, revision_before)

        self.widget.show_tab_markers(({"x": 150.0, "y": 100.0},))
        self.assertEqual(len(self.widget.overlays.tab_marker_items), 1)
        self.assertEqual(self.document.revision, revision_before)

        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_Escape)
        self.app.processEvents()
        self.assertFalse(self.widget.tool_manager.point_capture_active)
        self.assertEqual(cancelled, [True])
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertEqual(self.document.revision, revision_before)

        self.widget.controller.selection.select_only(self.path_id)
        self.widget.begin_point_capture(
            captured.append,
            cancel_callback=lambda: cancelled.append(True),
        )
        QtTest.QTest.mouseClick(
            self.widget.view.viewport(),
            QtCore.Qt.RightButton,
            QtCore.Qt.NoModifier,
            point,
        )
        self.app.processEvents()
        self.assertFalse(self.widget.tool_manager.point_capture_active)
        self.assertEqual(cancelled, [True, True])
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))
        self.assertEqual(self.document.revision, revision_before)

    def test_click_near_endpoint_does_not_enter_node_mode(self):
        point = self.viewport_point(50.0, 100.0) + QtCore.QPoint(3, 0)
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertFalse(self.widget.overlays.node_items)

    def test_empty_rectangle_interior_is_background_not_a_selection_hit(self):
        rectangle = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(20.0, 20.0), self.Vec2(100.0, 20.0),
                self.Vec2(100.0, 70.0), self.Vec2(20.0, 70.0),
            ),
            closed=True,
        )
        self.widget.controller.execute(self.AddEntitiesCommand((rectangle,)))
        self.widget.controller.selection.select_only(rectangle.id)
        self.widget.activate_tool(self.EditorMode.SELECT)
        interior = self.viewport_point(60.0, 45.0)
        QtTest.QTest.mouseClick(
            self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, interior
        )
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, ())

    def test_imported_pocket_hatch_is_filled_and_selectable_inside_piece_group(self):
        outer = replace(
            self.PathEntity.from_points(
                self.document.active_layer_id,
                (
                    self.Vec2(20.0, 20.0), self.Vec2(140.0, 20.0),
                    self.Vec2(140.0, 80.0), self.Vec2(20.0, 80.0),
                ),
                closed=True,
                id="imported-outer",
            ),
            metadata={"source_shape_component_id": "component-001"},
        )
        pocket = replace(
            self.PathEntity.from_points(
                self.document.active_layer_id,
                (
                    self.Vec2(20.0, 35.0), self.Vec2(75.0, 35.0),
                    self.Vec2(75.0, 65.0), self.Vec2(20.0, 65.0),
                ),
                closed=True,
                id="imported-pocket",
            ),
            metadata={
                "source_shape_component_id": "component-001",
                "import_role": "pocket_region",
                "pocket_depth_mm": 5.0,
            },
        )
        group = self.GroupEntity(
            self.document.active_layer_id,
            (outer.id, pocket.id),
            id="imported-piece-group",
        )
        self.widget.controller.execute(
            self.AddEntitiesCommand((outer, pocket, group))
        )
        self.app.processEvents()

        pocket_item = self.widget.adapter.items_by_id[pocket.id]
        no_brush = getattr(QtCore.Qt, "NoBrush", QtCore.Qt.BrushStyle.NoBrush)
        self.assertNotEqual(pocket_item.brush().style(), no_brush)
        interior = self.viewport_point(50.0, 50.0)
        self.assertEqual(
            self.widget.adapter.hit_test_body(self.widget.view, interior),
            pocket.id,
        )

        QtTest.QTest.mouseClick(
            self.widget.view.viewport(),
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
            interior,
        )
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, (pocket.id,))

    def test_marquee_in_empty_space_does_not_select_surrounding_contour(self):
        outer = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(20.0, 20.0), self.Vec2(280.0, 20.0),
                self.Vec2(280.0, 180.0), self.Vec2(20.0, 180.0),
            ),
            closed=True,
        )
        inner = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(75.0, 60.0), self.Vec2(175.0, 60.0),
                self.Vec2(175.0, 140.0), self.Vec2(75.0, 140.0),
            ),
            closed=True,
        )
        self.widget.controller.execute(self.AddEntitiesCommand((outer, inner)))
        viewport = self.widget.view.viewport()
        for start, end in (
            (self.viewport_point(200.0, 145.0), self.viewport_point(230.0, 165.0)),
            (self.viewport_point(230.0, 165.0), self.viewport_point(200.0, 145.0)),
        ):
            QtTest.QTest.mousePress(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
            QtTest.QTest.mouseMove(viewport, end, 20)
            QtTest.QTest.mouseRelease(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
            self.app.processEvents()
            self.assertEqual(self.widget.controller.selection.ids, ())

    def test_transform_pivot_is_an_explicit_move_target(self):
        point = self.viewport_point(150.0, 100.0)
        viewport = self.widget.view.viewport()
        QtTest.QTest.mouseClick(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        QtTest.QTest.mouseClick(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        pivot = self.widget.view.mapFromScene(self.widget.overlays.selection_pivot_item.scenePos())
        before = self.widget.controller.get_entity(self.path_id)
        QtTest.QTest.mousePress(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pivot)
        QtTest.QTest.mouseMove(viewport, pivot + QtCore.QPoint(30, 0), 20)
        QtTest.QTest.mouseRelease(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, pivot + QtCore.QPoint(30, 0))
        self.app.processEvents()
        after = self.widget.controller.get_entity(self.path_id)
        self.assertGreater(after.start.x, before.start.x)

    def test_second_click_enters_transform_and_double_click_never_enters_nodes(self):
        point = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertFalse(self.widget.overlays.selection_handle_items)
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.TRANSFORM)
        self.assertTrue(self.widget.overlays.selection_handle_items)
        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_S)
        self.app.processEvents()
        QtTest.QTest.mouseDClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        self.assertNotEqual(self.widget.controller.mode, self.EditorMode.NODE_EDIT)
        self.assertFalse(self.widget.overlays.node_items)

    def test_n_enters_node_mode_and_escape_returns_to_selected_object(self):
        point = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_N)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.NODE_EDIT)
        self.assertEqual(len(self.widget.overlays.node_items), 2)
        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_Escape)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))

    def test_alt_drag_restricts_to_the_dominant_axis(self):
        viewport = self.widget.view.viewport()
        start = self.viewport_point(150.0, 100.0)
        end = self.viewport_point(190.0, 115.0)
        before = self.widget.controller.get_entity(self.path_id)
        QtTest.QTest.mousePress(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
        QtTest.QTest.mouseMove(viewport, end, 20)
        QtTest.QTest.mouseRelease(viewport, QtCore.Qt.LeftButton, QtCore.Qt.AltModifier, end)
        self.app.processEvents()
        after = self.widget.controller.get_entity(self.path_id)
        self.assertGreater(after.start.x - before.start.x, 1.0)
        self.assertAlmostEqual(after.start.y, before.start.y, places=9)

    def test_ctrl_drag_copies_selection_and_leaves_original_in_place(self):
        viewport = self.widget.view.viewport()
        start = self.viewport_point(150.0, 100.0)
        end = self.viewport_point(190.0, 100.0)
        original = self.widget.controller.get_entity(self.path_id)
        QtTest.QTest.mousePress(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
        QtTest.QTest.mouseMove(viewport, end, 20)
        QtTest.QTest.mouseRelease(viewport, QtCore.Qt.LeftButton, QtCore.Qt.ControlModifier, end)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.get_entity(self.path_id), original)
        self.assertEqual(len(self.document.entities_by_id), 2)
        self.assertNotEqual(self.widget.controller.selection.primary_id, self.path_id)

    def test_plain_node_click_never_moves_or_teleports(self):
        body = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, body)
        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_N)
        self.app.processEvents()
        entity = self.widget.controller.get_entity(self.path_id)
        handle = self.widget.overlays.node_items[0]
        node_id = handle.node_id
        before = entity.node_position(node_id)
        point = self.widget.view.mapFromScene(handle.scenePos())
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        after = self.widget.controller.get_entity(self.path_id).node_position(node_id)
        self.assertEqual(after, before)
        self.assertEqual(self.widget.controller.mode, self.EditorMode.NODE_EDIT)

    def test_hit_target_stays_usable_at_different_zoom_levels(self):
        for rect in (
            QtCore.QRectF(0.0, 0.0, 1200.0, 800.0),
            QtCore.QRectF(125.0, 85.0, 50.0, 30.0),
        ):
            self.widget.view.fit_model_rect(rect)
            self.app.processEvents()
            point = self.viewport_point(150.0, 100.0) + QtCore.QPoint(0, 5)
            self.assertEqual(self.widget.adapter.hit_test_body(self.widget.view, point), self.path_id)

    def test_modifier_node_hit_uses_nearby_scene_entities(self):
        near = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(20.0, 20.0), self.Vec2(40.0, 20.0),
                self.Vec2(40.0, 40.0), self.Vec2(20.0, 40.0),
            ),
            closed=True,
            id="near-corner",
        )
        far_paths = tuple(
            self.PathEntity.from_points(
                self.document.active_layer_id,
                (
                    self.Vec2(1000.0 + index * 30.0, 1000.0),
                    self.Vec2(1020.0 + index * 30.0, 1000.0),
                    self.Vec2(1020.0 + index * 30.0, 1020.0),
                    self.Vec2(1000.0 + index * 30.0, 1020.0),
                ),
                closed=True,
                id="far-%03d" % index,
            )
            for index in range(120)
        )
        self.widget.controller.execute(
            self.AddEntitiesCommand((near,) + far_paths)
        )
        self.app.processEvents()

        visited = set()
        original_nodes = self.PathEntity.nodes

        def tracked_nodes(entity):
            visited.add(entity.id)
            return original_nodes(entity)

        with patch.object(self.PathEntity, "nodes", tracked_nodes):
            hit = self.widget.adapter.hit_test_path_node(
                self.widget.view,
                self.viewport_point(20.0, 20.0),
                closed_only=True,
            )

        self.assertIsNotNone(hit)
        self.assertEqual(hit.entity_id, near.id)
        self.assertIn(near.id, visited)
        self.assertFalse({path.id for path in far_paths}.intersection(visited))

    def test_zoom_does_not_change_domain_geometry(self):
        before = self.widget.controller.get_entity(self.path_id)
        initial_scale = self.widget.view.pixels_per_mm()
        self.widget.view.fit_model_rect(QtCore.QRectF(100.0, 50.0, 100.0, 100.0))
        self.app.processEvents()
        self.assertNotEqual(self.widget.view.pixels_per_mm(), initial_scale)
        self.assertEqual(self.widget.controller.get_entity(self.path_id), before)

    def test_pan_can_move_work_area_fully_offscreen_and_fit_restores_it(self):
        view = self.widget.view
        viewport = view.viewport()
        work_rect = self.widget.adapter.work_area_item.rect()
        before_revision = self.document.revision
        before_entity = self.widget.controller.get_entity(self.path_id)

        # Three ordinary middle-button drags must be allowed to carry the
        # entire fitted sheet beyond the right edge of the visible canvas.
        for _drag in range(3):
            start = viewport.rect().center()
            end = QtCore.QPoint(viewport.width() - 5, start.y())
            QtTest.QTest.mousePress(viewport, QtCore.Qt.MiddleButton, pos=start)
            QtTest.QTest.mouseMove(viewport, end, 30)
            QtTest.QTest.mouseRelease(viewport, QtCore.Qt.MiddleButton, pos=end)
            self.app.processEvents()

        screen_x = (
            view.mapFromScene(work_rect.topLeft()).x(),
            view.mapFromScene(work_rect.topRight()).x(),
            view.mapFromScene(work_rect.bottomLeft()).x(),
            view.mapFromScene(work_rect.bottomRight()).x(),
        )
        self.assertGreater(min(screen_x), viewport.width())
        self.assertEqual(self.document.revision, before_revision)
        self.assertEqual(self.widget.controller.get_entity(self.path_id), before_entity)

        self.widget.fit_work_area()
        self.app.processEvents()
        restored_x = (
            view.mapFromScene(work_rect.topLeft()).x(),
            view.mapFromScene(work_rect.topRight()).x(),
        )
        self.assertGreaterEqual(min(restored_x), 0)
        self.assertLessEqual(max(restored_x), viewport.width())

    def test_work_area_origin_moves_dashed_rect_without_reframing_entities(self):
        probe = QtCore.QPointF(50.0, 100.0)
        screen_before = self.widget.view.mapFromScene(probe)
        updated = self.WorkArea(40.0, 25.0, 340.0, 225.0)

        self.widget.controller.execute(self.SetWorkAreaCommand(updated))
        # Mirrors the UI commit, which explicitly reapplies the persisted area.
        self.widget.set_work_area(updated)
        self.app.processEvents()

        rect = self.widget.adapter.work_area_item.rect()
        self.assertEqual(
            (rect.x(), rect.y(), rect.width(), rect.height()),
            (40.0, 25.0, 300.0, 200.0),
        )
        self.assertEqual(self.widget.view.mapFromScene(probe), screen_before)

    def test_creating_a_vector_never_recenters_the_camera(self):
        view = self.widget.view
        probe = QtCore.QPointF(85.0, 65.0)
        view.centerOn(probe)
        self.app.processEvents()
        screen_before = view.mapFromScene(probe)
        transform_before = view.transform()

        created_id = self.widget.controller.add_line(
            self.Vec2(520.0, 470.0),
            self.Vec2(580.0, 510.0),
        )
        self.app.processEvents()

        self.assertTrue(created_id)
        self.assertEqual(view.transform(), transform_before)
        screen_after = view.mapFromScene(probe)
        self.assertLessEqual(abs(screen_after.x() - screen_before.x()), 1)
        self.assertLessEqual(abs(screen_after.y() - screen_before.y()), 1)

    def test_compact_vertical_drawing_toolbar_has_clear_icons_and_spacing(self):
        self.widget.resize(680, 620)
        self.app.processEvents()
        self.assertLessEqual(self.widget.toolbar.sizeHint().width(), self.widget.width())
        self.assertLessEqual(self.widget.toolbar.height(), 30)
        # The host must include the toolbar row and the optional horizontal
        # scrollbar; otherwise the row is vertically clipped in a narrow dock.
        self.assertGreaterEqual(self.widget.top_toolbar_scroll.height(), 48)
        self.assertGreaterEqual(
            self.widget.top_toolbar_scroll.viewport().height(),
            self.widget.toolbar.height(),
        )
        self.assertEqual(self.widget.drawing_toolbar.orientation(), QtCore.Qt.Vertical)
        self.assertEqual(self.widget.drawing_toolbar.width(), 40)
        self.assertEqual(len(self.widget._mode_buttons), 11)
        rectangles = []
        for mode in (
            self.EditorMode.SELECT,
            self.EditorMode.NODE_EDIT,
            self.EditorMode.DRAW_LINE,
            self.EditorMode.DRAW_POLYLINE,
            self.EditorMode.DRAW_RECTANGLE,
            self.EditorMode.DRAW_CIRCLE,
            self.EditorMode.DRAW_ELLIPSE,
            self.EditorMode.DRAW_ARC,
            self.EditorMode.DRAW_BEZIER,
            self.EditorMode.DRAW_POLYGON,
            self.EditorMode.DRAW_STAR,
        ):
            button = self.widget._mode_buttons[mode]
            self.assertFalse(button.icon().isNull())
            self.assertEqual(button.toolButtonStyle(), QtCore.Qt.ToolButtonIconOnly)
            self.assertEqual(button.size(), QtCore.QSize(34, 34))
            self.assertTrue(button.toolTip())
            rectangles.append(button.geometry())
        for first, second in zip(rectangles, rectangles[1:]):
            self.assertFalse(first.intersects(second))
            self.assertGreaterEqual(second.top() - first.bottom(), 2)
        for button in (
            self.widget.file_menu_button,
            self.widget.edit_menu_button,
            self.widget.repair_menu_button,
            self.widget.fillet_menu_button,
            self.widget.pieces_menu_button,
            self.widget.cam_menu_button,
        ):
            # In a narrow dock the top command strip scrolls horizontally
            # instead of forcing the whole editor wider or dropping menus.
            self.assertIs(button.parentWidget(), self.widget.toolbar)
        self.assertGreater(
            self.widget.top_toolbar_scroll.horizontalScrollBar().maximum(), 0
        )

        self.assertFalse(
            self.widget.drawing_toolbar.findChildren(QtWidgets.QSpinBox)
        )
        self.assertFalse(self.widget.polygon_context.isVisible())
        self.widget.activate_tool(self.EditorMode.DRAW_POLYGON)
        self.app.processEvents()
        self.assertTrue(self.widget.polygon_context.isVisible())
        self.assertIs(self.widget.polygon_sides.parent(), self.widget.polygon_context)
        self.widget.activate_tool(self.EditorMode.DRAW_STAR)
        self.app.processEvents()
        self.assertTrue(self.widget.polygon_context.isVisible())
        self.assertTrue(self.widget.star_inner_ratio.isVisible())
        self.assertEqual(self.widget.polygon_context_label.text(), "Estrela — pontas")

    def test_canvas_ctrl_a_and_delete_do_not_fall_through_to_host_actions(self):
        self.widget.view.setFocus()
        self.app.processEvents()
        QtTest.QTest.keyClick(
            self.widget.view,
            QtCore.Qt.Key_A,
            QtCore.Qt.ControlModifier,
        )
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))

        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_Delete)
        self.app.processEvents()
        self.assertNotIn(self.path_id, self.document.entities_by_id)

    def test_piece_recognition_does_not_rebuild_unrelated_vector_projection(self):
        """Piece2D is topology metadata, not a reason to redraw a cabinet."""
        item = self.widget.adapter.items_by_id[self.path_id]
        item_type = type(item)
        original_update = item_type.update_entity
        updated_ids = []

        def observe_update(graphics_item, entity):
            updated_ids.append(str(entity.id))
            return original_update(graphics_item, entity)

        piece = self.Piece2D(
            id="piece-performance",
            name="Peça de teste",
            outer_path_id=self.path_id,
        )
        with patch.object(item_type, "update_entity", new=observe_update):
            self.widget.controller.execute(self.ReplacePiecesCommand((piece,)))
        self.assertEqual(updated_ids, [])
        self.assertIn(self.path_id, self.widget.adapter.items_by_id)

    def test_preview_confirmation_is_pinned_above_canvas(self):
        self.widget.begin_workflow_preview(
            (self.document.get_entity(self.path_id),),
            "2 peças prontas para organizar",
            lambda: None,
            apply_label="Aplicar organização",
        )
        self.app.processEvents()
        bar = self.widget.workflow_preview_bar
        self.assertIs(bar.parent(), self.widget.canvas_surface)
        self.assertTrue(bar.isVisible())
        self.assertEqual(bar.apply_button.text(), "Aplicar organização")
        bar_bottom = bar.mapTo(self.widget, QtCore.QPoint(0, bar.height())).y()
        view_top = self.widget.view.mapTo(self.widget, QtCore.QPoint(0, 0)).y()
        self.assertLessEqual(bar_bottom, view_top)
        self.assertIs(self.widget.view.parent(), self.widget.ruler_surface)

    def test_compact_labels_and_menu_actions_explain_their_effect(self):
        self.assertEqual(self.widget.snap_checkbox.text(), "Imã (Snap)")
        self.assertIn("controle separado", self.widget.snap_checkbox.toolTip())
        self.assertEqual(self.widget.grid_checkbox.text(), "Grade")
        self.assertIn("fundo branco", self.widget.grid_checkbox.toolTip())
        cam = self.widget._menu_actions["CAM"]["Usar Editor 2D como fonte"]
        self.assertIn("vetores persistidos", cam.toolTip())
        recognize = self.widget._menu_actions["Peças"][
            "Reconhecer peças e furos"
        ]
        self.assertIn("Não cria geometria", recognize.toolTip())
        balanced = self.widget._menu_actions["Peças"]["Organizar inteligente"]
        thorough = self.widget._menu_actions["Peças"]["Organizar profundo"]
        self.assertIn("prévia", balanced.toolTip())
        self.assertIn("mais ordens", thorough.toolTip())
        nesting_spy = QtTest.QSignalSpy(self.widget.organizePiecesRequested)
        balanced.trigger()
        self.assertEqual(nesting_spy.count(), 1)
        self.assertFalse(self.widget.measure_button.icon().isNull())
        self.assertFalse(self.widget.recognize_pieces_button.icon().isNull())
        self.assertFalse(self.widget.organize_pieces_button.icon().isNull())
        self.widget.measure_button.click()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.MEASURE)
        recognize_spy = QtTest.QSignalSpy(self.widget.createPiecesRequested)
        self.widget.recognize_pieces_button.click()
        self.assertEqual(recognize_spy.count(), 1)
        toolbar_nesting_spy = QtTest.QSignalSpy(self.widget.organizePiecesRequested)
        self.widget.organize_pieces_button.click()
        self.assertEqual(toolbar_nesting_spy.count(), 1)
        trace_spy = QtTest.QSignalSpy(self.widget.traceBitmapRequested)
        self.widget._menu_actions["Arquivo"]["Vetorizar imagem…"].trigger()
        self.assertEqual(trace_spy.count(), 1)

    def test_language_menu_translates_presentation_and_keeps_actions(self):
        from woodcam_editor.presentation.i18n import (
            language,
            set_language,
            translate_text,
            translate_widget_tree,
        )

        self.assertEqual(language(), "pt")
        revision_before = self.document.revision
        try:
            self.widget._menu_actions["Idioma"]["English"].trigger()
            self.app.processEvents()
            self.assertEqual(self.widget.file_menu_button.text(), "File")
            self.assertEqual(self.widget.edit_menu_button.text(), "Edit")
            self.assertEqual(self.widget.language_menu_button.text(), "Language")
            self.assertEqual(
                self.widget._menu_actions["Arquivo"]["Exportar"].text(),
                "Export",
            )
            self.assertEqual(self.widget.snap_checkbox.text(), "Snap")
            self.assertEqual(self.widget.grid_checkbox.text(), "Grid")
            self.widget.tool_manager.set_status(
                "Selecionar — 1 clique seleciona; arraste move o corpo"
            )
            self.assertEqual(
                self.widget.mode_label.text(),
                "Select — one click selects; drag moves the object",
            )
            self.assertEqual(self.widget.layer_panel.title(), "Layers")
            self.assertEqual(
                [
                    self.widget.layer_panel.tree.headerItem().text(column)
                    for column in range(4)
                ],
                ["Active", "Layer", "Visible", "Locked"],
            )
            piece_labels = {
                label.text()
                for label in self.widget.pieces_panel.findChildren(QtWidgets.QLabel)
            }
            self.assertIn("Grain direction", piece_labels)
            self.assertIn("Rotation", piece_labels)
            self.assertEqual(self.widget.pieces_panel.apply_button.text(), "Apply to part")
            self.assertEqual(
                self.widget.pieces_panel.rotation_mode.itemText(0),
                "0° only",
            )
            self.assertTrue(
                bool(
                    self.widget.pieces_panel.rotation_mode.property(
                        "woodcam_i18n_owned"
                    )
                )
            )
            # The English refresh timer may inspect this tree repeatedly, but
            # an already translated combo must not be rewritten.  Rewriting
            # its current item emits currentTextChanged and can trigger costly
            # downstream CAM refreshes in a large FCStd.
            combo_refresh_spy = QtTest.QSignalSpy(
                self.widget.pieces_panel.rotation_mode.currentTextChanged
            )
            translate_widget_tree(self.widget)
            self.assertEqual(combo_refresh_spy.count(), 0)
            self.assertEqual(
                self.widget.pieces_panel.summary_label.text(),
                "No classified part.",
            )
            self.assertEqual(
                self.widget.properties_panel.title(),
                "Exact position and dimensions",
            )
            exact_labels = {
                label.text()
                for label in self.widget.properties_panel.findChildren(QtWidgets.QLabel)
            }
            self.assertIn("Minimum X (mm)", exact_labels)
            self.assertIn("Minimum Y (mm)", exact_labels)
            self.assertIn("Width (mm)", exact_labels)
            self.assertIn("Height (mm)", exact_labels)
            self.assertIn("Uniform scale (%)", exact_labels)
            self.assertEqual(
                self.widget.properties_panel.apply_scale_button.text(),
                "Apply scale",
            )

            # QListWidget/QTreeWidget text lives in the item model, not in a
            # QLabel. It must be translated and restored without changing
            # UserRole identifiers or any editor command state.
            model_list = QtWidgets.QListWidget(self.widget)
            model_item = QtWidgets.QListWidgetItem(
                "Nenhum percurso aplicado ainda", model_list
            )
            model_item.setData(QtCore.Qt.UserRole, "stable-operation-id")
            translate_widget_tree(model_list)
            self.assertEqual(model_item.text(), "No applied toolpath yet")
            self.assertEqual(
                model_item.data(QtCore.Qt.UserRole),
                "stable-operation-id",
            )
            self.assertEqual(translate_text("6 passagens"), "6 passes")
            self.assertEqual(translate_text("1 selecionado"), "1 selected")
            self.assertEqual(translate_text("12 selecionados"), "12 selected")
            self.assertEqual(translate_text("Passagens"), "Passes")
            self.assertEqual(translate_text("Percursos 2D"), "2D Toolpaths")
            self.assertEqual(
                translate_text("Ver percurso de Corte no 2D"),
                "Show Cut toolpath in 2D",
            )
            self.assertEqual(
                translate_text("Preparando a primeira solução…"),
                "Preparing the first solution…",
            )
            self.assertEqual(
                translate_text(
                    "Tentativa %d/%d — %s\n%s • melhor: %s"
                ) % (1, 4, translate_text("Resposta rápida"), "15.1 s", "none"),
                "Attempt 1/4 — Quick result\n15.1 s • best: none",
            )
            self.assertEqual(
                translate_text(
                    "Organização não executada: O nesting foi bloqueado pela "
                    "validação vetorial final: As peças piece-a#1 e piece-b#1 "
                    "ficaram com folga de 0.008 mm; a folga mínima é 4.000 mm."
                ),
                "Nesting was not run: final vector validation blocked the "
                "layout: parts piece-a#1 and piece-b#1 ended with 0.008 mm "
                "clearance; the minimum clearance is 4.000 mm.",
            )
            translated_summary = translate_text(
                "Prévia do nesting inteligente: magenta = destino, azul = posição atual. "
                "%d peça(s) em %d chapa(s); %d não couberam. Eficiência %.1f%%; "
                "%s venceu entre %d layouts%s%s."
            ) % (
                13,
                2,
                0,
                42.5,
                translate_text("contorno real"),
                8,
                "",
                translate_text("; busca concluída"),
            )
            self.assertIn("Smart nesting preview", translated_summary)
            self.assertIn("real contour", translated_summary)
            self.assertNotIn("peça", translated_summary)
            self.assertEqual(
                translate_text(
                    "Organização não executada: Busca interrompida antes de "
                    "produzir a primeira solução."
                ),
                "Nesting was not run: Search stopped before producing the "
                "first solution.",
            )
            layer_row = self.widget.layer_panel.tree.topLevelItem(0)
            self.assertEqual(layer_row.text(1), "Drawing")
            self.widget.layer_panel._sync_layer_controls()
            self.assertEqual(layer_row.text(1), "Drawing")
            translated_block = translate_text(
                "Linha comum bloqueada: os contornos originais são válidos, "
                "mas os percursos externos compensados se cruzam. A folga "
                "entre algumas peças é menor que o diâmetro efetivo de 4.00 "
                "mm. Execute Organizar peças novamente com Linha comum ativa; "
                "o organizador ajustará essa folga automaticamente. Detalhes: "
                "Os contornos outer-0011 e outer-0023 possuem cruzamento real "
                "próximo de X 288.35 / Y 413.65."
            )
            self.assertIn("Common-line cutting blocked", translated_block)
            self.assertIn(
                "Contours outer-0011 and outer-0023 have a real crossing",
                translated_block,
            )
            self.assertNotIn("contornos originais", translated_block)
            self.assertEqual(
                translate_text("Altura intacta no MDF (mm)"),
                "Untouched MDF height (mm)",
            )
            self.assertEqual(
                translate_text("Restos soltos: parafusos"),
                "Loose waste: screws",
            )
            self.assertEqual(
                translate_text("Pausa antes de cada peça"),
                "Pause before each part",
            )
            from cam_advisor import analyze_cam_settings

            advice = analyze_cam_settings(
                {
                    "operation_mode": "finish3d",
                    "tool_type": "end_mill",
                    "tool_diameter": 6.0,
                    "stepdown": 3.0,
                    "safe_height": 8.0,
                    "retract_height": 15.0,
                    "finish3d_stepover_percent": 25.0,
                }
            ).to_plain_text(translator=translate_text)
            self.assertIn("[SUGGESTION]", advice)
            self.assertIn("Suggested value to review: 10", advice)
            self.assertNotIn("fresa esférica", advice.lower())
            self.assertEqual(
                translate_text("Fresa maior que 2 região(ões) selecionada(s)"),
                "Tool larger than 2 selected region(s)",
            )
            # Switching language is presentation-only; it cannot create an
            # editor command or alter the document revision.
            self.assertEqual(self.document.revision, revision_before)
        finally:
            set_language("pt")
            self.app.processEvents()
            if "model_item" in locals():
                translate_widget_tree(model_list)
                self.assertEqual(model_item.text(), "Nenhum percurso aplicado ainda")
        self.assertEqual(self.widget.file_menu_button.text(), "Arquivo")

    def test_cam_plan_preview_is_a_non_mutating_editor_overlay(self):
        """The Aspire-style plan view must not become vector geometry."""
        before = dict(self.document.entities_by_id)
        shown = self.widget.show_cut_toolpath_preview(
            {
                "rapid": [((0.0, 0.0, 20.0), (20.0, 0.0, 20.0))],
                "ramp": [((20.0, 0.0, 20.0), (20.0, 0.0, 0.0))],
                "cut": [((20.0, 0.0, 0.0), (80.0, 0.0, 0.0))],
                "corner": [((80.0, 0.0, 0.0), (80.0, 30.0, 0.0))],
                "entry_points": [(20.0, 0.0)],
            }
        )
        self.assertTrue(shown)
        self.assertEqual(self.document.entities_by_id, before)
        self.assertFalse(self.widget.overlays.toolpath_items["cut"].path().isEmpty())
        self.assertTrue(self.widget.overlays.toolpath_entry_item.isVisible())
        self.widget.clear_cut_toolpath_preview()
        self.assertTrue(self.widget.overlays.toolpath_items["cut"].path().isEmpty())
        self.assertFalse(self.widget.overlays.toolpath_entry_item.isVisible())

    def test_cam_menu_emits_editor_plan_preview_request(self):
        show_spy = QtTest.QSignalSpy(self.widget.showCutToolpathRequested)
        clear_spy = QtTest.QSignalSpy(self.widget.clearCutToolpathRequested)
        actions = self.widget._menu_actions["CAM"]
        actions["Ver percurso de Corte aqui"].trigger()
        actions["Ocultar percurso"].trigger()
        self.assertEqual(show_spy.count(), 1)
        self.assertEqual(clear_spy.count(), 1)

    def test_rulers_use_the_same_compact_thickness(self):
        self.assertEqual(self.widget.horizontal_ruler.height(), 22)
        self.assertEqual(self.widget.vertical_ruler.width(), 22)
        self.assertEqual(self.widget.ruler_corner.width(), 22)
        self.assertEqual(self.widget.ruler_corner.height(), 22)

    def test_cam_menu_routes_cut_holes_and_pocket_without_bottom_button_clutter(self):
        configure_spy = QtTest.QSignalSpy(self.widget.configureToolpathRequested)
        show_spy = QtTest.QSignalSpy(self.widget.showToolpathRequested)
        actions = self.widget._menu_actions["CAM"]

        actions["Configurar/criar Rebaixo…"].trigger()
        actions["Ver percurso de Furos aqui"].trigger()

        self.assertEqual(configure_spy.count(), 1)
        self.assertEqual(configure_spy.at(0)[0], "pocket")
        self.assertEqual(show_spy.count(), 1)
        self.assertEqual(show_spy.at(0)[0], "holes")

    def test_virtual_sheets_are_named_without_becoming_vectors(self):
        """Extra nesting sheets should read like separate Aspire material pages."""

        before = dict(self.document.entities_by_id)
        self.widget.controller.execute(
            self.SetDocumentMetadataCommand(
                "organization_sheet_bounds",
                [[0.0, 0.0, 300.0, 200.0], [350.0, 0.0, 650.0, 200.0]],
            )
        )
        self.app.processEvents()
        self.assertEqual(self.document.entities_by_id, before)
        self.assertEqual(len(self.widget.adapter.sheet_area_items), 2)
        self.assertEqual(len(self.widget.adapter.sheet_label_items), 2)
        self.assertEqual(
            [item.text() for item in self.widget.adapter.sheet_label_items],
            ["Chapa 01  ·  X0 Y0", "Chapa 02  ·  X0 Y0"],
        )
        self.widget.adapter.show_preview_sheet_bounds(
            ((0.0, 0.0, 300.0, 200.0), (350.0, 0.0, 650.0, 200.0))
        )
        self.assertEqual(len(self.widget.adapter.preview_sheet_label_items), 2)
        self.assertEqual(
            [item.text() for item in self.widget.adapter.preview_sheet_label_items],
            [
                "Chapa 01 — prévia  ·  X0 Y0",
                "Chapa 02 — prévia  ·  X0 Y0",
            ],
        )
        self.widget.adapter.clear_preview_sheet_bounds()
        self.assertFalse(self.widget.adapter.preview_sheet_label_items)

    def test_remnant_cut_preview_becomes_a_selectable_document_vector(self):
        before = dict(self.document.entities_by_id)
        cut = {
            "sheet_index": 0,
            "start": [0.0, 170.0],
            "end": [300.0, 170.0],
            "remnant_bounds": [0.0, 170.0, 300.0, 200.0],
            "area": 9000.0,
        }

        self.widget.adapter.show_preview_remnant_cuts([cut])
        self.assertEqual(len(self.widget.adapter.preview_remnant_cut_items), 1)
        self.assertEqual(len(self.widget.adapter.preview_remnant_label_items), 1)
        self.widget.adapter.clear_preview_remnant_cuts()
        self.assertFalse(self.widget.adapter.preview_remnant_cut_items)

        remnant = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(0.0, 170.0), self.Vec2(300.0, 170.0)),
            metadata={
                "woodcam_role": "remnant_cut",
                "sheet_index": 0,
                "cut_start": cut["start"],
                "cut_end": cut["end"],
                "remnant_bounds": cut["remnant_bounds"],
                "remnant_area_mm2": cut["area"],
            },
        )
        cut["entity_id"] = remnant.id
        self.widget.controller.execute(
            self.CompositeCommand(
                (
                    self.AddEntitiesCommand((remnant,)),
                    self.SetDocumentMetadataCommand(
                        "organization_remnant_cuts",
                        [cut],
                    ),
                ),
                label="Criar linha de separação",
            )
        )
        self.app.processEvents()

        self.assertEqual(len(self.document.entities_by_id), len(before) + 1)
        self.assertIn(remnant.id, self.widget.adapter.items_by_id)
        # Metadata retains the upright area label, while the actual dashed
        # line is the ordinary selectable EntityGraphicsItem (no duplicate).
        self.assertEqual(len(self.widget.adapter.remnant_cut_items), 0)
        self.assertEqual(len(self.widget.adapter.remnant_label_items), 1)
        self.assertIn(
            "Retalho 300 × 30 mm",
            self.widget.adapter.remnant_label_items[0].text(),
        )
        self.widget.controller.selection.select_only(remnant.id)
        self.widget.adapter.update_selection()
        self.assertEqual(self.widget.selected_entity_ids, (remnant.id,))
        self.widget.controller.undo()
        self.app.processEvents()
        self.assertNotIn(remnant.id, self.document.entities_by_id)
        self.assertFalse(self.widget.adapter.remnant_cut_items)

    def test_sheet_panel_uses_a_local_origin_for_status_snap_and_exact_position(self):
        self.widget.controller.execute(
            self.SetDocumentMetadataCommand(
                "organization_sheet_bounds",
                [[0.0, 0.0, 300.0, 200.0], [350.0, 0.0, 650.0, 200.0]],
            )
        )
        second_id = self.widget.controller.add_line(
            self.Vec2(370.0, 50.0), self.Vec2(400.0, 50.0)
        )
        self.app.processEvents()

        self.assertEqual(self.widget.sheet_panel.list.count(), 2)
        self.assertEqual(self.widget.sheet_panel.current_index(), 1)
        self.assertEqual(self.widget._active_sheet_origin, QtCore.QPointF(350.0, 0.0))
        self.assertAlmostEqual(self.widget.properties_panel.x_field.value(), 20.0)
        self.widget.view.cursorMoved.emit(QtCore.QPointF(351.0, 2.0))
        self.app.processEvents()
        self.assertIn("X 1.000", self.widget.position_label.text())
        self.assertIn("Y 2.000", self.widget.position_label.text())

        settings = self.widget.controller.snap_engine.settings
        self.assertEqual(settings.grid_origin_x_mm, 350.0)
        self.assertEqual(settings.grid_origin_y_mm, 0.0)
        self.assertEqual(
            self.widget.horizontal_ruler._coordinate_origin,
            QtCore.QPointF(350.0, 0.0),
        )

        self.widget.properties_panel.x_field.setValue(30.0)
        self.widget.properties_panel.apply_button.click()
        self.app.processEvents()
        self.assertAlmostEqual(self.document.get_entity(second_id).bounds().min_x, 380.0)
        self.widget.undo()
        self.assertAlmostEqual(self.document.get_entity(second_id).bounds().min_x, 370.0)

    def test_hovering_an_empty_sheet_activates_its_local_zero_before_drawing(self):
        self.widget.controller.execute(
            self.SetDocumentMetadataCommand(
                "organization_sheet_bounds",
                [[0.0, 0.0, 300.0, 200.0], [350.0, 0.0, 650.0, 200.0]],
            )
        )
        self.widget.controller.selection.clear()
        self.widget.sheet_panel.list.setCurrentRow(0)
        self.app.processEvents()

        self.widget.view.cursorMoved.emit(QtCore.QPointF(351.0, 2.0))
        self.app.processEvents()

        self.assertEqual(self.widget.sheet_panel.current_index(), 1)
        self.assertEqual(
            self.widget._active_sheet_origin,
            QtCore.QPointF(350.0, 0.0),
        )
        self.assertIn("X 1.000", self.widget.position_label.text())
        self.assertIn("Y 2.000", self.widget.position_label.text())

    def test_side_sections_collapse_without_touching_document_or_selection(self):
        self.widget.controller.selection.select_only(self.path_id)
        revision = self.document.revision
        selected = self.widget.controller.selection.ids
        panels = (
            self.widget.sheet_panel,
            self.widget.layer_panel,
            self.widget.pieces_panel,
            self.widget.properties_panel,
            self.widget.transform_panel,
        )
        for panel in panels:
            expanded_maximum = panel.maximumHeight()
            self.assertEqual(panel._collapse_button.text(), "▼")
            self.assertGreaterEqual(panel._collapse_button.width(), 27)
            panel._collapse_button.click()
            self.app.processEvents()
            self.assertTrue(panel.collapsed)
            self.assertEqual(panel._collapse_button.text(), "▶")
            self.assertLessEqual(panel.maximumHeight(), panel._header_height())
            panel._collapse_button.click()
            self.app.processEvents()
            self.assertFalse(panel.collapsed)
            self.assertEqual(panel._collapse_button.text(), "▼")
            self.assertEqual(panel.maximumHeight(), expanded_maximum)
        self.assertEqual(self.document.revision, revision)
        self.assertEqual(self.widget.controller.selection.ids, selected)

    def test_right_click_cancels_tool_and_workflow_then_clears_selection(self):
        viewport = self.widget.view.viewport()
        before_ids = set(self.document.entities_by_id)
        self.widget.controller.selection.select_only(self.path_id)
        self.widget.activate_tool(self.EditorMode.DRAW_LINE)
        QtTest.QTest.mouseClick(
            viewport,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
            self.viewport_point(80.0, 60.0),
        )
        QtTest.QTest.mouseMove(viewport, self.viewport_point(120.0, 80.0), 20)
        self.app.processEvents()
        self.assertTrue(self.widget.overlays.preview_item.isVisible())
        QtTest.QTest.mouseClick(
            viewport,
            QtCore.Qt.RightButton,
            QtCore.Qt.NoModifier,
            self.viewport_point(120.0, 80.0),
        )
        self.app.processEvents()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertEqual(self.widget.controller.selection.ids, ())
        self.assertFalse(self.widget.overlays.preview_item.isVisible())
        self.assertEqual(set(self.document.entities_by_id), before_ids)

        self.widget.controller.selection.select_only(self.path_id)
        self.widget.begin_workflow_preview(
            (self.document.get_entity(self.path_id),),
            "Prévia de teste",
            lambda: None,
        )
        self.assertTrue(self.widget.workflow_preview_bar.is_active)
        QtTest.QTest.mouseClick(
            viewport,
            QtCore.Qt.RightButton,
            QtCore.Qt.NoModifier,
            self.viewport_point(150.0, 100.0),
        )
        self.app.processEvents()
        self.assertFalse(self.widget.workflow_preview_bar.is_active)
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertEqual(self.widget.controller.selection.ids, ())

    def test_grid_has_its_own_visibility_and_snap_control(self):
        from woodcam_editor.presentation.compat import qt_enum

        self.widget.grid_spacing.setValue(7.5)
        self.app.processEvents()
        self.assertAlmostEqual(self.widget.view.grid_spacing_mm, 7.5)
        self.assertAlmostEqual(
            self.widget.controller.snap_engine.settings.grid_spacing_mm, 7.5
        )
        self.assertTrue(self.widget.view.grid_visible)
        self.widget.snap_checkbox.setChecked(False)
        self.app.processEvents()
        self.assertTrue(self.widget.view.grid_visible)
        self.assertAlmostEqual(self.widget.view.grid_spacing_mm, 7.5)
        self.assertTrue(self.widget.controller.snap_engine.settings.grid)
        self.assertFalse(self.widget.controller.snap_engine.settings.endpoint)

        self.widget.grid_checkbox.setChecked(False)
        self.app.processEvents()
        self.assertFalse(self.widget.view.grid_visible)
        self.assertFalse(self.widget.controller.snap_engine.settings.grid)
        self.assertFalse(self.widget.grid_spacing.isEnabled())
        self.assertEqual(
            self.widget.adapter.work_area_item.brush().style(),
            qt_enum(QtCore.Qt, "NoBrush", "BrushStyle"),
        )

        # Geometry snap and grid snap are independent in both directions.
        self.widget.snap_checkbox.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.widget.controller.snap_engine.settings.endpoint)
        self.assertFalse(self.widget.controller.snap_engine.settings.grid)

    def test_rulers_show_mouse_coordinate_markers_and_clear_on_leave(self):
        viewport_position = self.widget.view.viewport().rect().center()
        scene_position = self.widget.view.mapToScene(viewport_position)
        self.widget.view.cursorMoved.emit(scene_position)
        self.app.processEvents()

        for ruler in (self.widget.horizontal_ruler, self.widget.vertical_ruler):
            marker_position = ruler.cursor_scene_position
            self.assertIsNotNone(marker_position)
            self.assertAlmostEqual(marker_position.x(), scene_position.x(), places=6)
            self.assertAlmostEqual(marker_position.y(), scene_position.y(), places=6)

            coordinate = ruler._marker_coordinate(scene_position)
            self.assertIsNotNone(coordinate)
            dirty_rect = ruler._marker_rect(coordinate)
            self.assertFalse(dirty_rect.isNull())
            # Repaint executes the triangle-arrow branch without touching the
            # vector document or requiring a screenshot assertion.
            ruler.repaint(dirty_rect)

        self.widget.view.cursorLeft.emit()
        self.app.processEvents()
        self.assertIsNone(self.widget.horizontal_ruler.cursor_scene_position)
        self.assertIsNone(self.widget.vertical_ruler.cursor_scene_position)

    def test_empty_canvas_coalesces_only_visual_cursor_feedback(self):
        """High-rate mouse packets must not repaint both rulers per packet."""

        view = self.widget.view
        view._cursor_display_timer.stop()
        view._pending_cursor_display = None
        received = []
        view.cursorMoved.connect(received.append)
        for index in range(200):
            view._queue_cursor_display(QtCore.QPointF(index, index * 2))
        self.assertTrue(view._cursor_display_timer.isActive())
        self.assertEqual(received, [QtCore.QPointF(0.0, 0.0)])
        view._cursor_display_timer.stop()
        view._flush_cursor_display()
        self.assertEqual(len(received), 2)
        self.assertEqual(received[-1], QtCore.QPointF(199.0, 398.0))

    def test_pan_and_rectangle_preview_use_frames_but_commit_exact_click(self):
        view = self.widget.view
        viewport = view.viewport()
        center = viewport.rect().center()
        before_center = view.mapToScene(center)
        extent_calls = []
        original_extent = view._ensure_free_pan_extent

        def counted_extent():
            extent_calls.append(True)
            return original_extent()

        view._ensure_free_pan_extent = counted_extent
        try:
            QtTest.QTest.mousePress(
                viewport, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier, center
            )
            for index in range(200):
                QtTest.QTest.mouseMove(
                    viewport,
                    center + QtCore.QPoint(index % 80, (index * 3) % 50),
                    -1,
                )
            QtTest.QTest.mouseRelease(
                viewport, QtCore.Qt.MiddleButton, QtCore.Qt.NoModifier,
                center + QtCore.QPoint(39, 47),
            )
            self.app.processEvents()
        finally:
            view._ensure_free_pan_extent = original_extent
        self.assertLess(len(extent_calls), 10)
        self.assertNotEqual(view.mapToScene(center), before_center)

        self.widget.activate_tool(self.EditorMode.DRAW_RECTANGLE)
        before_ids = set(self.document.entities_by_id)
        first_screen = QtCore.QPoint(
            max(10, viewport.width() // 4), max(10, viewport.height() // 4)
        )
        final_screen = QtCore.QPoint(
            max(20, viewport.width() * 3 // 4),
            max(20, viewport.height() * 3 // 4),
        )
        first_scene = view.mapToScene(first_screen)
        final_scene = view.mapToScene(final_screen)
        QtTest.QTest.mouseClick(
            viewport,
            QtCore.Qt.LeftButton,
            QtCore.Qt.ShiftModifier,
            first_screen,
        )
        preview_frames = []
        view.pointerMoved.connect(preview_frames.append)
        for index in range(200):
            QtTest.QTest.mouseMove(
                viewport,
                QtCore.QPoint(
                    first_screen.x()
                    + (final_screen.x() - first_screen.x()) * index // 199,
                    first_screen.y()
                    + (final_screen.y() - first_screen.y()) * index // 199,
                ),
                -1,
            )
        self.assertLess(len(preview_frames), 50)
        QtTest.QTest.mouseClick(
            viewport,
            QtCore.Qt.LeftButton,
            QtCore.Qt.ShiftModifier,
            final_screen,
        )
        self.app.processEvents()
        added_ids = set(self.document.entities_by_id) - before_ids
        self.assertEqual(len(added_ids), 1)
        bounds = self.document.get_entity(next(iter(added_ids))).bounds()
        self.assertAlmostEqual(
            bounds.min_x, min(first_scene.x(), final_scene.x()), places=6
        )
        self.assertAlmostEqual(
            bounds.max_x, max(first_scene.x(), final_scene.x()), places=6
        )
        self.assertAlmostEqual(
            bounds.min_y, min(first_scene.y(), final_scene.y()), places=6
        )
        self.assertAlmostEqual(
            bounds.max_y, max(first_scene.y(), final_scene.y()), places=6
        )
        self.widget.undo()
        self.assertEqual(set(self.document.entities_by_id), before_ids)

    def test_body_and_node_drags_are_single_undoable_commands(self):
        viewport = self.widget.view.viewport()
        start = self.viewport_point(150.0, 100.0)
        end = self.viewport_point(170.0, 100.0)
        QtTest.QTest.mousePress(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, start)
        QtTest.QTest.mouseMove(viewport, end, 30)
        QtTest.QTest.mouseRelease(viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, end)
        self.app.processEvents()
        moved = self.document.get_entity(self.path_id)
        self.assertAlmostEqual(moved.start.x, 70.0, delta=1.0)
        self.widget.undo()
        self.assertEqual(self.document.get_entity(self.path_id).start, self.Vec2(50.0, 100.0))

    def test_ellipse_tool_creates_exact_ellipse_entity(self):
        self.widget.activate_tool(self.EditorMode.DRAW_ELLIPSE)
        viewport = self.widget.view.viewport()
        QtTest.QTest.mouseClick(
            viewport, QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier,
            self.viewport_point(100.0, 60.0),
        )
        QtTest.QTest.mouseClick(
            viewport, QtCore.Qt.LeftButton, QtCore.Qt.ShiftModifier,
            self.viewport_point(140.0, 80.0),
        )
        self.app.processEvents()
        ellipses = [
            entity for entity in self.document.entities_by_id.values()
            if type(entity).__name__ == "EllipseEntity"
        ]
        self.assertEqual(len(ellipses), 1)
        # The mouse path is quantized to whole screen pixels; geometry remains
        # an exact EllipseEntity and the numerical panel is available when an
        # exact 40 × 20 mm dimension is required.
        self.assertAlmostEqual(ellipses[0].radius_x, 40.0, delta=1.0)
        self.assertAlmostEqual(ellipses[0].radius_y, 20.0, delta=1.0)

    def test_exact_properties_apply_one_undoable_transform(self):
        self.widget.controller.selection.select_only(self.path_id)
        self.app.processEvents()
        panel = self.widget.properties_panel
        panel.x_field.setValue(60.0)
        panel.width_field.setValue(100.0)
        panel.apply()
        entity = self.document.get_entity(self.path_id)
        self.assertAlmostEqual(entity.bounds().min_x, 60.0, places=6)
        self.assertAlmostEqual(entity.bounds().width, 100.0, places=6)
        self.widget.undo()
        entity = self.document.get_entity(self.path_id)
        self.assertAlmostEqual(entity.bounds().min_x, 50.0, places=6)
        self.assertAlmostEqual(entity.bounds().width, 200.0, places=6)

    def test_exact_dimensions_use_height_and_explicit_ratio_driver(self):
        rectangle_id = self.widget.controller.add_rectangle(
            self.Vec2(10.0, 20.0), self.Vec2(50.0, 30.0)
        )
        self.widget.controller.selection.select_only(rectangle_id)
        self.app.processEvents()
        panel = self.widget.properties_panel
        self.assertFalse(panel.keep_ratio.isChecked())
        panel.height_field.setValue(25.0)
        panel.apply()
        bounds = self.document.get_entity(rectangle_id).bounds()
        self.assertAlmostEqual(bounds.width, 40.0, places=6)
        self.assertAlmostEqual(bounds.height, 25.0, places=6)
        self.widget.undo()

        panel.refresh()
        panel.keep_ratio.setChecked(True)
        panel.height_field.setValue(20.0)
        self.assertAlmostEqual(panel.width_field.value(), 80.0, places=6)
        panel.apply()
        bounds = self.document.get_entity(rectangle_id).bounds()
        self.assertAlmostEqual(bounds.width, 80.0, places=6)
        self.assertAlmostEqual(bounds.height, 20.0, places=6)

    def test_circle_and_ellipse_have_exact_type_specific_properties(self):
        from woodcam_editor.domain import (
            AddEntitiesCommand,
            CircleEntity,
            EllipseEntity,
        )

        circle = CircleEntity(
            self.document.active_layer_id, self.Vec2(20.0, 30.0), 5.0
        )
        ellipse = EllipseEntity(
            self.document.active_layer_id,
            self.Vec2(100.0, 80.0),
            10.0,
            4.0,
        )
        self.widget.controller.execute(AddEntitiesCommand((circle, ellipse)))
        panel = self.widget.properties_panel

        self.widget.controller.selection.select_only(circle.id)
        self.app.processEvents()
        self.assertTrue(panel.primitive_box.isVisible())
        self.assertFalse(panel.width_field.isVisible())
        self.assertTrue(panel.radius_field.isVisible())
        self.assertTrue(panel.diameter_field.isVisible())
        original_min = circle.bounds().min_x
        panel.diameter_field.setValue(30.0)
        self.assertAlmostEqual(panel.radius_field.value(), 15.0, places=6)
        panel.apply()
        changed_circle = self.document.get_entity(circle.id)
        self.assertAlmostEqual(changed_circle.radius, 15.0, places=6)
        self.assertAlmostEqual(changed_circle.bounds().min_x, original_min, places=6)
        self.widget.undo()
        self.assertEqual(self.document.get_entity(circle.id), circle)

        self.widget.controller.selection.select_only(ellipse.id)
        self.app.processEvents()
        self.assertTrue(panel.radius_x_field.isVisible())
        self.assertTrue(panel.radius_y_field.isVisible())
        self.assertTrue(panel.rotation_field.isVisible())
        original_min_x = ellipse.bounds().min_x
        original_min_y = ellipse.bounds().min_y
        panel.radius_x_field.setValue(12.0)
        panel.radius_y_field.setValue(6.0)
        panel.rotation_field.setValue(30.0)
        panel.apply()
        changed_ellipse = self.document.get_entity(ellipse.id)
        self.assertAlmostEqual(changed_ellipse.radius_x, 12.0, places=6)
        self.assertAlmostEqual(changed_ellipse.radius_y, 6.0, places=6)
        self.assertAlmostEqual(changed_ellipse.rotation, 3.141592653589793 / 6.0, places=6)
        self.assertAlmostEqual(changed_ellipse.bounds().min_x, original_min_x, places=6)
        self.assertAlmostEqual(changed_ellipse.bounds().min_y, original_min_y, places=6)
        self.widget.undo()
        self.assertEqual(self.document.get_entity(ellipse.id), ellipse)

    def test_layer_panel_visibility_and_active_layer_are_commands(self):
        original_layer_id = self.document.active_layer_id
        new_layer_id = self.widget.controller.add_layer("Detalhes")
        self.app.processEvents()
        self.assertEqual(self.document.active_layer_id, new_layer_id)
        self.assertEqual(self.widget.layer_panel.tree.topLevelItemCount(), 2)
        self.widget.controller.update_layer(original_layer_id, visible=False)
        self.app.processEvents()
        self.assertFalse(self.document.layers_by_id[original_layer_id].visible)
        self.assertFalse(self.widget.adapter.items_by_id[self.path_id].isVisible())
        self.widget.undo()
        self.assertTrue(self.document.layers_by_id[original_layer_id].visible)

    def test_layer_visibility_does_not_rebuild_paths_or_scene_bounds(self):
        item = self.widget.adapter.items_by_id[self.path_id]
        item_type = type(item)
        original_update = item_type.update_entity
        updated_ids = []

        def observe_update(graphics_item, entity):
            updated_ids.append(str(entity.id))
            return original_update(graphics_item, entity)

        with patch.object(item_type, "update_entity", new=observe_update), patch.object(
            self.widget.adapter,
            "_update_scene_rect",
            wraps=self.widget.adapter._update_scene_rect,
        ) as update_bounds:
            self.widget.controller.update_layer(
                self.document.active_layer_id,
                visible=False,
            )

        self.assertEqual(updated_ids, [])
        self.assertEqual(update_bounds.call_count, 0)
        self.assertFalse(item.isVisible())

    def test_layer_checkbox_reuses_existing_tree_controls(self):
        from woodcam_editor.presentation.compat import qt_enum

        panel = self.widget.layer_panel
        row = panel.tree.topLevelItem(0)
        checkbox = panel.tree.itemWidget(row, 2)
        role = qt_enum(QtCore.Qt, "UserRole", "ItemDataRole")
        layer_ids = row.data(0, role)

        panel._set_visible(layer_ids, False)

        self.assertIs(panel.tree.itemWidget(row, 2), checkbox)
        self.assertFalse(checkbox.isChecked())
        self.assertTrue(
            all(not self.document.layers_by_id[value].visible for value in layer_ids)
        )

    def test_external_reload_refreshes_every_side_panel_through_controller(self):
        from woodcam_editor.adapters.freecad_commands import copy_document_state
        from woodcam_editor.domain import Piece2D, ReplacePiecesCommand

        baseline = self.document.clone()
        self.widget.controller.add_layer("Depois")
        outer_id = self.widget.controller.add_rectangle(
            self.Vec2(20.0, 20.0), self.Vec2(120.0, 80.0)
        )
        self.widget.controller.execute(
            ReplacePiecesCommand((Piece2D("Peça", outer_id, (), id="piece-reload"),))
        )
        self.app.processEvents()
        self.assertEqual(self.widget.layer_panel.tree.topLevelItemCount(), 2)
        self.assertEqual(self.widget.pieces_panel.list.count(), 1)
        self.assertTrue(self.widget.properties_panel.apply_button.isEnabled())
        self.assertTrue(self.widget.transform_panel.rotate_button.isEnabled())

        # Mirrors FreeCADCommandSession.reload(): same object, replaced state.
        copy_document_state(self.document, baseline)
        self.widget.refresh_from_document()
        self.app.processEvents()
        self.assertEqual(self.widget.layer_panel.tree.topLevelItemCount(), 1)
        self.assertEqual(self.widget.pieces_panel.list.count(), 0)
        self.assertFalse(self.widget.properties_panel.apply_button.isEnabled())
        self.assertFalse(self.widget.transform_panel.rotate_button.isEnabled())
        self.assertEqual(set(self.widget.adapter.items_by_id), {self.path_id})

    def test_advanced_toolbar_actions_activate_internal_tools(self):
        trim_spy = QtTest.QSignalSpy(self.widget.trimRequested)
        dogbone_spy = QtTest.QSignalSpy(self.widget.dogboneRequested)
        panelnest_spy = QtTest.QSignalSpy(self.widget.sendPanelNestRequested)
        join_open_spy = QtTest.QSignalSpy(self.widget.joinOpenPathsRequested)
        repair_actions = self.widget._menu_actions["Reparar"]
        edit_actions = self.widget._menu_actions["Editar"]
        fillet_actions = self.widget._menu_actions["Filetes"]
        self.assertIn("Fechar caminho / unir próximas", repair_actions)
        self.assertIn("Unir vetores abertos (por tolerância)", repair_actions)
        self.assertIn("Unir 2 pontas (reta)", repair_actions)
        self.assertIn("Projetar ponta na geometria", repair_actions)
        self.assertIn("Limpar sobrelinhas/duplicados…", repair_actions)
        self.assertIn("Soldar vetores sobrepostos", edit_actions)
        self.assertIn("Subtrair vetores (criar furo/recorte interno)", edit_actions)
        self.assertIn("Interseção de vetores", edit_actions)
        self.assertIn("Inverter direção dos vetores", edit_actions)
        self.assertIn("Editar texto vetorial…", edit_actions)
        self.assertTrue(all(not action.icon().isNull() for action in edit_actions.values()))
        self.assertTrue(all(not action.icon().isNull() for action in repair_actions.values()))
        edit_flyout = self.widget.findChild(
            QtWidgets.QToolButton,
            "drawingGroupEditar",
        )
        repair_flyout = self.widget.findChild(
            QtWidgets.QToolButton,
            "drawingGroupReparar",
        )
        self.assertIsNotNone(edit_flyout)
        self.assertIsNotNone(repair_flyout)
        self.assertIn(edit_actions["Copiar"], edit_flyout.menu().actions())
        self.assertIn(repair_actions["Trim interativo"], repair_flyout.menu().actions())
        self.assertTrue(
            self.widget.findChild(QtWidgets.QToolButton, "drawingActionTextovetorial")
        )
        repair_actions["Unir vetores abertos (por tolerância)"].trigger()
        self.assertEqual(join_open_spy.count(), 1)
        self.assertIn(
            "Importar itens da árvore…",
            self.widget._menu_actions["Arquivo"],
        )
        self.assertIn(
            "Criar relevo 3D por imagem…",
            self.widget._menu_actions["Arquivo"],
        )
        relief_spy = QtTest.QSignalSpy(self.widget.createReliefRequested)
        self.widget._menu_actions["Arquivo"]["Criar relevo 3D por imagem…"].trigger()
        self.assertEqual(relief_spy.count(), 1)
        cleanup_spy = QtTest.QSignalSpy(self.widget.cleanupDuplicatesRequested)
        repair_actions["Limpar sobrelinhas/duplicados…"].trigger()
        self.assertEqual(cleanup_spy.count(), 1)
        repair_action = repair_actions["Trim interativo"]
        fillet_action = fillet_actions["Dogbone"]
        repair_action.trigger()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.TRIM)
        self.assertTrue(self.widget.modifier_panel.isVisible())
        repair_actions["Unir 2 pontas (reta)"].trigger()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.JOIN_ENDPOINTS)
        self.assertTrue(self.widget.modifier_panel.isVisible())
        fillet_action.trigger()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.DOGBONE)
        self.widget._menu_actions["CAM"]["Enviar PanelNest"].trigger()
        self.assertEqual(trim_spy.count(), 0)
        self.assertEqual(dogbone_spy.count(), 0)
        self.assertEqual(panelnest_spy.count(), 1)

    def test_trim_hover_is_pure_click_applies_and_one_undo_restores(self):
        from woodcam_editor.domain import AddEntitiesCommand

        left_cutter = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(100.0, 50.0), self.Vec2(100.0, 150.0)),
        )
        right_cutter = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(200.0, 50.0), self.Vec2(200.0, 150.0)),
        )
        self.widget.controller.execute(AddEntitiesCommand((left_cutter, right_cutter)))
        before = dict(self.document.entities_by_id)
        self.widget._activate_modifier(self.EditorMode.TRIM)
        point = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseMove(self.widget.view.viewport(), point, 20)
        self.app.processEvents()
        self.assertTrue(self.widget.overlays.preview_item.isVisible())
        self.assertEqual(self.document.entities_by_id, before)
        QtTest.QTest.mouseClick(
            self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point
        )
        self.app.processEvents()
        self.assertNotEqual(self.document.entities_by_id, before)
        self.assertFalse(self.widget.overlays.preview_item.isVisible())
        self.widget.undo()
        self.assertEqual(self.document.entities_by_id, before)

    def test_ctrl_a_selects_only_visible_unlocked_entities(self):
        from woodcam_editor.domain import AddEntitiesCommand, Layer

        hidden = Layer(name="Oculta", visible=False, order=1)
        locked = Layer(name="Travada", locked=True, order=2)
        self.document.add_layers((hidden, locked))
        hidden_path = self.PathEntity.from_points(
            hidden.id, (self.Vec2(20, 20), self.Vec2(40, 20))
        )
        locked_path = self.PathEntity.from_points(
            locked.id, (self.Vec2(20, 30), self.Vec2(40, 30))
        )
        self.widget.controller.execute(AddEntitiesCommand((hidden_path, locked_path)))
        self.widget.activate_tool(self.EditorMode.SELECT)
        self.widget.view.setFocus()
        QtTest.QTest.keyClick(
            self.widget.view, QtCore.Qt.Key_A, QtCore.Qt.ControlModifier
        )
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))

    def test_ctrl_c_ctrl_v_pastes_whole_group_once_and_undoes_once(self):
        from woodcam_editor.domain import CircleEntity, GroupEntitiesCommand

        hole = CircleEntity(
            self.document.active_layer_id, self.Vec2(150.0, 100.0), 8.0
        )
        self.widget.controller.execute(self.AddEntitiesCommand((hole,)))
        self.widget.controller.execute(
            GroupEntitiesCommand((self.path_id, hole.id), group_id="clipboard-piece")
        )
        self.widget.controller.selection.select_only("clipboard-piece")
        self.widget.view.setFocus()

        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_C, QtCore.Qt.ControlModifier)
        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_V, QtCore.Qt.ControlModifier)
        self.app.processEvents()

        copied_id = self.widget.controller.selection.primary_id
        copied_group = self.document.get_entity(copied_id)
        self.assertIsInstance(copied_group, self.GroupEntity)
        self.assertEqual(len(copied_group.child_ids), 2)
        self.assertEqual(len(self.document.entities_by_id), 6)
        self.assertIn("peça inteira", self.widget.mode_label.text().lower())

        self.widget.undo()
        self.assertEqual(set(self.document.entities_by_id), {self.path_id, hole.id, "clipboard-piece"})

    def test_canvas_keeps_focus_for_delete_shortcut(self):
        self.widget.activate_tool(self.EditorMode.SELECT)
        point = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseClick(
            self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point
        )
        self.app.processEvents()
        self.assertTrue(self.widget.view.hasFocus())
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))
        QtTest.QTest.keyClick(self.widget.view, QtCore.Qt.Key_Delete)
        self.app.processEvents()
        self.assertNotIn(self.path_id, self.document.entities_by_id)

    def test_offset_fillet_reliefs_and_extend_are_preview_first(self):
        from woodcam_editor.domain import AddEntitiesCommand

        square = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(20, 130), self.Vec2(80, 130),
                self.Vec2(80, 190), self.Vec2(20, 190),
            ),
            closed=True,
        )
        open_path = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(150, 30), self.Vec2(200, 30)),
        )
        boundary = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(250, 10), self.Vec2(250, 50)),
        )
        self.widget.controller.execute(AddEntitiesCommand((square, open_path, boundary)))
        role_index = self.widget.modifier_panel.contour_role.findData("inner")
        self.widget.modifier_panel.contour_role.setCurrentIndex(role_index)
        viewport = self.widget.view.viewport()
        cases = (
            (self.EditorMode.OFFSET, (50.0, 130.0)),
            (self.EditorMode.FILLET, (80.0, 130.0)),
            (self.EditorMode.DOGBONE, (80.0, 130.0)),
            (self.EditorMode.TBONE, (80.0, 130.0)),
            (self.EditorMode.EXTEND, (200.0, 30.0)),
        )
        for mode, model_point in cases:
            with self.subTest(mode=mode):
                self.widget._activate_modifier(mode)
                QtTest.QTest.mouseMove(viewport, self.viewport_point(280.0, 190.0), 5)
                point = self.viewport_point(*model_point)
                QtTest.QTest.mouseMove(viewport, point, 10)
                self.app.processEvents()
                before = dict(self.document.entities_by_id)
                self.assertTrue(self.widget.overlays.preview_item.isVisible())
                self.assertEqual(self.document.entities_by_id, before)
                QtTest.QTest.mouseClick(
                    viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point
                )
                self.app.processEvents()
                self.assertNotEqual(self.document.entities_by_id, before)
                self.widget.undo()
                self.assertEqual(self.document.entities_by_id, before)

    def test_piece_panel_lists_only_piece_and_selects_outer_with_inners(self):
        from woodcam_editor.domain import (
            AddEntitiesCommand, Piece2D, ReplacePiecesCommand,
        )

        outer = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(20, 20), self.Vec2(120, 20),
                self.Vec2(120, 80), self.Vec2(20, 80),
            ),
            closed=True,
        )
        inner = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(50, 40), self.Vec2(70, 40),
                self.Vec2(70, 60), self.Vec2(50, 60),
            ),
            closed=True,
        )
        self.widget.controller.execute(AddEntitiesCommand((outer, inner)))
        piece = Piece2D("Porta", outer.id, (inner.id,), id="piece-door")
        self.widget.controller.execute(ReplacePiecesCommand((piece,)))
        self.app.processEvents()
        panel = self.widget.pieces_panel
        self.assertEqual(panel.list.count(), 1)
        self.assertIn("1 interno", panel.list.item(0).toolTip())
        spy = QtTest.QSignalSpy(self.widget.pieceSelected)
        panel.list.setCurrentRow(-1)
        panel.list.setCurrentRow(0)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, (outer.id, inner.id))
        self.assertEqual(spy.count(), 1)
        panel.name_field.setText("Porta esquerda")
        panel.quantity_field.setValue(3)
        panel.material_field.setText("MDF Branco")
        panel.thickness_field.setValue(18.0)
        panel.grain_enabled.setChecked(True)
        panel.grain_angle.setValue(90.0)
        panel.rotation_mode.setCurrentIndex(panel.rotation_mode.findData("zero_ninety"))
        panel.apply_button.click()
        updated = self.document.pieces_by_id[piece.id]
        self.assertEqual(updated.name, "Porta esquerda")
        self.assertEqual(updated.quantity, 3)
        self.assertEqual(updated.inner_path_ids, (inner.id,))
        self.widget.undo()
        self.assertEqual(self.document.pieces_by_id[piece.id], piece)

    def test_connect_two_stage_preview_warns_branch_applies_and_undoes(self):
        from woodcam_editor.domain import AddEntitiesCommand

        source = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(30, 10.1), self.Vec2(30, 50)),
        )
        target = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(10, 10), self.Vec2(50, 10)),
        )
        self.widget.controller.execute(AddEntitiesCommand((source, target)))
        self.widget.join_tolerance.setValue(0.2)
        before = dict(self.document.entities_by_id)
        viewport = self.widget.view.viewport()
        self.widget._activate_modifier(self.EditorMode.CONNECT)
        QtTest.QTest.mouseClick(
            viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
            self.viewport_point(30, 10.1),
        )
        self.assertEqual(self.document.entities_by_id, before)
        target_point = self.viewport_point(30, 10)
        QtTest.QTest.mouseMove(viewport, target_point, 10)
        self.app.processEvents()
        self.assertTrue(self.widget.overlays.preview_item.isVisible())
        self.assertEqual(self.document.entities_by_id, before)
        self.assertIn("gera ramificação", self.widget.mode_label.text())
        QtTest.QTest.mouseClick(
            viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, target_point
        )
        self.app.processEvents()
        self.assertNotEqual(self.document.entities_by_id, before)
        self.widget.undo()
        self.assertEqual(self.document.entities_by_id, before)

    def test_join_two_explicit_endpoints_previews_line_and_is_one_undo(self):
        from woodcam_editor.domain import AddEntitiesCommand

        second = self.PathEntity.from_points(
            self.document.active_layer_id,
            (self.Vec2(270, 120), self.Vec2(290, 120)),
            id="explicit-second",
        )
        self.widget.controller.execute(AddEntitiesCommand((second,)))
        before = dict(self.document.entities_by_id)
        viewport = self.widget.view.viewport()
        self.widget._activate_modifier(self.EditorMode.JOIN_ENDPOINTS)
        QtTest.QTest.mouseClick(
            viewport,
            QtCore.Qt.LeftButton,
            QtCore.Qt.NoModifier,
            self.viewport_point(250, 100),
        )
        target = self.viewport_point(270, 120)
        QtTest.QTest.mouseMove(viewport, target, 10)
        self.app.processEvents()
        preview = self.widget.tool_manager.active.preview
        self.assertIsNotNone(preview)
        self.assertEqual(preview.metadata["join_mode"], "line")
        self.assertIn("criar uma reta", self.widget.mode_label.text())
        self.assertEqual(self.document.entities_by_id, before)
        QtTest.QTest.mouseClick(
            viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, target
        )
        self.app.processEvents()
        self.assertNotIn(second.id, self.document.entities_by_id)
        joined = self.document.get_entity(self.path_id)
        self.assertEqual(len(joined.spans), 3)
        self.assertEqual(joined.spans[1].start, self.Vec2(250, 100))
        self.assertEqual(joined.spans[1].end, self.Vec2(270, 120))
        self.widget.undo()
        self.assertEqual(self.document.entities_by_id, before)

    def test_splice_neck_circle_short_long_preview_apply_and_undo(self):
        from woodcam_editor.domain import AddEntitiesCommand, CircleEntity

        neck = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(130, 180), self.Vec2(100, 180),
                self.Vec2(100, 140), self.Vec2(130, 140),
            ),
        )
        circle = CircleEntity(
            self.document.active_layer_id, self.Vec2(150, 160), 30
        )
        self.widget.controller.execute(AddEntitiesCommand((neck, circle)))
        self.widget.join_tolerance.setValue(5.0)
        viewport = self.widget.view.viewport()
        before = dict(self.document.entities_by_id)
        route_lengths = {}
        for route in ("short", "long"):
            with self.subTest(route=route):
                combo = self.widget.modifier_panel.splice_route
                combo.setCurrentIndex(combo.findData(route))
                self.widget._activate_modifier(self.EditorMode.SPLICE)
                QtTest.QTest.mouseClick(
                    viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                    self.viewport_point(100, 160),
                )
                QtTest.QTest.mouseMove(viewport, self.viewport_point(280, 195), 5)
                target_point = self.viewport_point(120, 160)
                QtTest.QTest.mouseMove(viewport, target_point, 10)
                self.app.processEvents()
                preview = self.widget.tool_manager.active.preview
                self.assertIsNotNone(preview)
                self.assertTrue(self.widget.overlays.preview_item.isVisible())
                self.assertEqual(self.document.entities_by_id, before)
                route_lengths[route] = preview.metadata["route_length_mm"]
                QtTest.QTest.mouseClick(
                    viewport, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                    target_point,
                )
                self.app.processEvents()
                self.assertTrue(self.document.get_entity(neck.id).closed)
                self.assertNotIn(circle.id, self.document.entities_by_id)
                self.widget.undo()
                self.assertEqual(self.document.entities_by_id, before)
        self.assertLess(route_lengths["short"], route_lengths["long"])

    def test_automatic_dogbone_classified_piece_preview_confirm_and_undo(self):
        from woodcam_editor.domain import (
            AddEntitiesCommand, Piece2D, ReplacePiecesCommand,
        )

        outer = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(0, 0), self.Vec2(100, 0),
                self.Vec2(100, 100), self.Vec2(60, 100),
                self.Vec2(60, 40), self.Vec2(40, 40),
                self.Vec2(40, 100), self.Vec2(0, 100),
            ),
            closed=True,
        )
        inner = self.PathEntity.from_points(
            self.document.active_layer_id,
            (
                self.Vec2(70, 60), self.Vec2(90, 60),
                self.Vec2(90, 80), self.Vec2(70, 80),
            ),
            closed=True,
        )
        self.widget.controller.execute(AddEntitiesCommand((outer, inner)))
        piece = Piece2D("Entalhada", outer.id, (inner.id,), id="piece-auto")
        self.widget.controller.execute(ReplacePiecesCommand((piece,)))
        self.widget.controller.selection.clear()
        before = dict(self.document.entities_by_id)
        self.widget.modifier_panel.radius_field.setValue(5.0)
        self.widget._activate_modifier(self.EditorMode.AUTO_DOGBONE)
        self.app.processEvents()
        tool = self.widget.tool_manager.active
        self.assertIsNotNone(tool.preview)
        self.assertTrue(self.widget.overlays.preview_item.isVisible())
        self.assertEqual(self.document.entities_by_id, before)
        self.assertEqual(tool.preview.metadata["applied_count"], 6)
        self.assertGreater(tool.preview.metadata["rejected_count"], 0)
        outer_applied = {
            tuple(item["point"])
            for item in tool.preview.metadata["applied"]
            if item["path_id"] == outer.id
        }
        self.assertEqual(outer_applied, {(60.0, 40.0), (40.0, 40.0)})
        self.assertTrue(self.widget.modifier_panel.apply_automatic_button.isEnabled())
        self.widget.modifier_panel.apply_automatic_button.click()
        self.app.processEvents()
        self.assertNotEqual(self.document.entities_by_id, before)
        self.widget.undo()
        self.assertEqual(self.document.entities_by_id, before)

    def test_transform_panel_rotates_and_undoes_without_scene_mutation(self):
        self.widget.controller.selection.select_only(self.path_id)
        self.app.processEvents()
        before = self.document.get_entity(self.path_id)
        panel = self.widget.transform_panel
        panel.angle_field.setValue(90.0)
        panel.rotate_button.click()
        self.app.processEvents()
        rotated = self.document.get_entity(self.path_id)
        self.assertAlmostEqual(rotated.bounds().width, 0.0, delta=1e-7)
        self.assertAlmostEqual(rotated.bounds().height, 200.0, delta=1e-7)
        self.assertEqual(self.widget.adapter.items_by_id[self.path_id].pos(), QtCore.QPointF(0.0, 0.0))
        self.widget.undo()
        self.assertEqual(self.document.get_entity(self.path_id), before)

    def test_external_workflow_preview_cancel_apply_stale_and_mode_change(self):
        from woodcam_editor.domain import MoveEntitiesCommand

        before = self.document.get_entity(self.path_id)
        delta = self.Vec2(12.0, 0.0)
        preview = before.transformed(
            __import__(
                "woodcam_editor.domain", fromlist=["Affine2D"]
            ).Affine2D.translation(delta)
        )

        def apply_move():
            self.widget.controller.execute(
                MoveEntitiesCommand((self.path_id,), delta)
            )

        self.widget.begin_workflow_preview(
            (preview,), "Prévia externa", apply_move
        )
        self.assertTrue(self.widget.workflow_preview_bar.is_active)
        self.assertEqual(self.document.get_entity(self.path_id), before)
        self.widget.workflow_preview_bar.cancel_button.click()
        self.assertFalse(self.widget.workflow_preview_bar.is_active)
        self.assertEqual(self.document.get_entity(self.path_id), before)

        self.widget.begin_workflow_preview(
            (preview,), "Prévia externa", apply_move
        )
        self.widget.activate_tool(self.EditorMode.DRAW_LINE)
        self.assertFalse(self.widget.workflow_preview_bar.is_active)
        self.assertEqual(self.document.get_entity(self.path_id), before)

        self.widget.begin_workflow_preview(
            (preview,), "Prévia externa", apply_move
        )
        self.widget.workflow_preview_bar.apply_button.click()
        self.assertFalse(self.widget.workflow_preview_bar.is_active)
        self.assertNotEqual(self.document.get_entity(self.path_id), before)


if __name__ == "__main__":
    unittest.main()
