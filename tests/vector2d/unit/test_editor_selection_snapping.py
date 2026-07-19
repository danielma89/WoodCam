"""Visibility/locking safety and local intersection snapping."""

from __future__ import annotations

import unittest

from woodcam_editor.application import EditorController, SnapEngine, SnapKind, SnapSettings
from woodcam_editor.domain import Layer, PathEntity, Vec2, VectorDocument


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

