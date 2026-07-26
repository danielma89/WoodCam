"""Visibility/locking safety and local intersection snapping."""

from __future__ import annotations

import unittest

from woodcam_editor.application import EditorController, SnapEngine, SnapKind, SnapSettings
from woodcam_editor.domain import ArcSpan, CircleEntity, Layer, PathEntity, Vec2, VectorDocument


class SelectionAndSnapSafetyTests(unittest.TestCase):
    def test_snap_ignores_hidden_and_locked_layers(self):
        document = VectorDocument.create_default()
        hidden = Layer(name="Oculta", visible=False, order=1)
        locked = Layer(name="Travada", locked=True, order=2)
        document.add_layers((hidden, locked))
        hidden_path = PathEntity.from_points(hidden.id, (Vec2(0, 0), Vec2(10, 0)))
        locked_path = PathEntity.from_points(locked.id, (Vec2(0, 1), Vec2(10, 1)))
        document.add_entities((hidden_path, locked_path))
        settings = SnapSettings(
            endpoint=True,
            intersection=False,
            midpoint=False,
            center=False,
            quadrant=False,
            on_geometry=False,
            grid=False,
        )
        self.assertIsNone(SnapEngine(settings).find(Vec2(0.05, 0.0), document, 10.0))

    def test_visible_line_intersection_is_ranked_in_screen_radius(self):
        document = VectorDocument.create_default()
        first = PathEntity.from_points(
            document.active_layer_id, (Vec2(0, 5), Vec2(10, 5))
        )
        second = PathEntity.from_points(
            document.active_layer_id, (Vec2(5, 0), Vec2(5, 10))
        )
        document.add_entities((first, second))
        settings = SnapSettings(
            endpoint=False,
            intersection=True,
            midpoint=False,
            center=False,
            quadrant=False,
            on_geometry=False,
            grid=False,
            radius_px=10.0,
        )
        candidate = SnapEngine(settings).find(Vec2(5.2, 5.1), document, 10.0)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, SnapKind.INTERSECTION)
        self.assertTrue(candidate.point.almost_equals(Vec2(5, 5), 1e-9))

    def test_smart_snap_uses_screen_tolerance_from_reference_point(self):
        document = VectorDocument.create_default()
        settings = SnapSettings(
            endpoint=False,
            intersection=False,
            midpoint=False,
            center=False,
            quadrant=False,
            on_geometry=False,
            grid=False,
            radius_px=10.0,
            horizontal=True,
            vertical=True,
            angle=False,
        )
        engine = SnapEngine(settings)
        candidate = engine.find(
            Vec2(50.0, 0.5), document, 10.0, reference_point=Vec2(0.0, 0.0)
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, SnapKind.HORIZONTAL)
        self.assertTrue(candidate.point.almost_equals(Vec2(50.0, 0.0), 1e-9))
        # Same world distance, higher zoom: now outside the 10 px radius.
        self.assertIsNone(
            engine.find(
                Vec2(50.0, 0.5), document, 40.0, reference_point=Vec2(0.0, 0.0)
            )
        )

    def test_smart_angle_snap_uses_configured_increment(self):
        document = VectorDocument.create_default()
        settings = SnapSettings(
            endpoint=False,
            intersection=False,
            midpoint=False,
            center=False,
            quadrant=False,
            on_geometry=False,
            grid=False,
            horizontal=False,
            vertical=False,
            angle=True,
            angle_increment_degrees=15.0,
            radius_px=10.0,
        )
        candidate = SnapEngine(settings).find(
            Vec2(10.0, 5.9), document, 10.0, reference_point=Vec2(0.0, 0.0)
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, SnapKind.ANGLE)
        self.assertIn("30", candidate.label)

    def test_smart_snap_finds_perpendicular_foot_on_finite_line(self):
        document = VectorDocument.create_default()
        line = PathEntity.from_points(
            document.active_layer_id, (Vec2(0.0, 0.0), Vec2(20.0, 0.0))
        )
        document.add_entities((line,))
        settings = SnapSettings(
            endpoint=False,
            intersection=False,
            midpoint=False,
            center=False,
            quadrant=False,
            on_geometry=False,
            grid=False,
            horizontal=False,
            vertical=False,
            angle=False,
            perpendicular=True,
            tangent=False,
            radius_px=10.0,
        )
        candidate = SnapEngine(settings).find(
            Vec2(8.0, 0.6), document, 10.0, reference_point=Vec2(8.0, 12.0)
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, SnapKind.PERPENDICULAR)
        self.assertTrue(candidate.point.almost_equals(Vec2(8.0, 0.0), 1e-9))

    def test_smart_snap_finds_tangent_on_circle(self):
        document = VectorDocument.create_default()
        circle = CircleEntity(document.active_layer_id, Vec2(0.0, 0.0), 5.0)
        document.add_entities((circle,))
        settings = SnapSettings(
            endpoint=False,
            intersection=False,
            midpoint=False,
            center=False,
            quadrant=False,
            on_geometry=False,
            grid=False,
            horizontal=False,
            vertical=False,
            angle=False,
            perpendicular=False,
            tangent=True,
            radius_px=10.0,
        )
        candidate = SnapEngine(settings).find(
            Vec2(2.55, 4.30), document, 10.0, reference_point=Vec2(10.0, 0.0)
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, SnapKind.TANGENT)
        self.assertTrue(candidate.point.almost_equals(Vec2(2.5, 4.3301270189), 1e-6))

    def test_smart_tangent_respects_arc_sweep_not_full_circle(self):
        document = VectorDocument.create_default()
        quarter_arc = ArcSpan(
            Vec2(10.0, 0.0), Vec2(0.0, 10.0), Vec2(0.0, 0.0), clockwise=False
        )
        path = PathEntity(document.active_layer_id, (quarter_arc,), closed=False)
        document.add_entities((path,))
        settings = SnapSettings(
            endpoint=False,
            intersection=False,
            midpoint=False,
            center=False,
            quadrant=False,
            on_geometry=False,
            grid=False,
            horizontal=False,
            vertical=False,
            angle=False,
            perpendicular=False,
            tangent=True,
            radius_px=10.0,
        )
        candidate = SnapEngine(settings).find(
            Vec2(5.02, 8.68), document, 10.0, reference_point=Vec2(20.0, 0.0)
        )
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.kind, SnapKind.TANGENT)
        self.assertTrue(candidate.point.almost_equals(Vec2(5.0, 8.6602540378), 1e-6))

    def test_delete_rejects_preselected_entity_after_layer_is_locked(self):
        document = VectorDocument.create_default()
        path = PathEntity.from_points(
            document.active_layer_id, (Vec2(0, 0), Vec2(10, 0))
        )
        document.add_entities((path,))
        controller = EditorController(document)
        controller.selection.select_only(path.id)
        controller.update_layer(document.active_layer_id, locked=True)
        with self.assertRaises(PermissionError):
            controller.delete_selected()
        self.assertIn(path.id, document.entities_by_id)


if __name__ == "__main__":
    unittest.main()
