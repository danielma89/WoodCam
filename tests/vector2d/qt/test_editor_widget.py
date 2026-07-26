"""Offscreen interaction tests for the document-backed canvas.

Run with::

    QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests/vector2d/qt
"""

from __future__ import annotations

import os
import unittest
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

    def test_click_near_endpoint_does_not_enter_node_mode(self):
        point = self.viewport_point(50.0, 100.0) + QtCore.QPoint(3, 0)
        QtTest.QTest.mouseClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.selection.ids, (self.path_id,))
        self.assertEqual(self.widget.controller.mode, self.EditorMode.SELECT)
        self.assertFalse(self.widget.overlays.node_items)

    def test_double_click_enters_node_mode(self):
        point = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseDClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        self.app.processEvents()
        self.assertEqual(self.widget.controller.mode, self.EditorMode.NODE_EDIT)
        self.assertEqual(self.widget.controller.node_entity_id, self.path_id)
        self.assertEqual(len(self.widget.overlays.node_items), 2)

    def test_plain_node_click_never_moves_or_teleports(self):
        body = self.viewport_point(150.0, 100.0)
        QtTest.QTest.mouseDClick(self.widget.view.viewport(), QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, body)
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

    def test_zoom_does_not_change_domain_geometry(self):
        before = self.widget.controller.get_entity(self.path_id)
        initial_scale = self.widget.view.pixels_per_mm()
        self.widget.view.fit_model_rect(QtCore.QRectF(100.0, 50.0, 100.0, 100.0))
        self.app.processEvents()
        self.assertNotEqual(self.widget.view.pixels_per_mm(), initial_scale)
        self.assertEqual(self.widget.controller.get_entity(self.path_id), before)

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
        self.assertIn("não oculta a grade", self.widget.snap_checkbox.toolTip())
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
        trace_spy = QtTest.QSignalSpy(self.widget.traceBitmapRequested)
        self.widget._menu_actions["Arquivo"]["Vetorizar imagem…"].trigger()
        self.assertEqual(trace_spy.count(), 1)

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
            ["Chapa 01", "Chapa 02"],
        )
        self.widget.adapter.show_preview_sheet_bounds(
            ((0.0, 0.0, 300.0, 200.0), (350.0, 0.0, 650.0, 200.0))
        )
        self.assertEqual(len(self.widget.adapter.preview_sheet_label_items), 2)
        self.assertEqual(
            [item.text() for item in self.widget.adapter.preview_sheet_label_items],
            ["Chapa 01 — prévia", "Chapa 02 — prévia"],
        )
        self.widget.adapter.clear_preview_sheet_bounds()
        self.assertFalse(self.widget.adapter.preview_sheet_label_items)

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

    def test_grid_spacing_drives_visual_grid_and_snap_independently_of_snap_toggle(self):
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
        self.assertFalse(self.widget.controller.snap_engine.settings.grid)

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
