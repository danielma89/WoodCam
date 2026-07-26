"""Exact cubic Bézier creation is command-backed and undoable."""

from __future__ import annotations

import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import CubicBezierSpan, InMemoryCommandHistory, Vec2, VectorDocument


class BezierCreationTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.history = InMemoryCommandHistory(self.document)
        self.controller = EditorController(self.document, history=self.history)

    def test_add_bezier_preserves_controls_and_undoes_as_one_command(self):
        entity_id = self.controller.add_bezier(
            Vec2(0, 0), Vec2(10, 20), Vec2(30, 20), Vec2(40, 0)
        )
        self.assertIsNotNone(entity_id)
        entity = self.document.get_entity(entity_id)
        self.assertEqual(len(entity.spans), 1)
        self.assertIsInstance(entity.spans[0], CubicBezierSpan)
        span = entity.spans[0]
        self.assertEqual(span.start, Vec2(0, 0))
        self.assertEqual(span.control1, Vec2(10, 20))
        self.assertEqual(span.control2, Vec2(30, 20))
        self.assertEqual(span.end, Vec2(40, 0))
        self.assertEqual(tuple(self.controller.selection.ids), (entity_id,))

        self.history.undo()
        self.assertNotIn(entity_id, self.document.entities_by_id)

    def test_zero_bezier_is_rejected_without_creating_history_entry(self):
        self.assertIsNone(
            self.controller.add_bezier(Vec2(5, 5), Vec2(5, 5), Vec2(5, 5), Vec2(5, 5))
        )
        self.assertFalse(self.document.entities_by_id)

    def test_control_handle_preview_and_move_keep_curve_exact_and_undoable(self):
        entity_id = self.controller.add_bezier(
            Vec2(0, 0), Vec2(10, 20), Vec2(30, 20), Vec2(40, 0)
        )
        entity = self.document.get_entity(entity_id)
        span = entity.spans[0]
        handle_id = self.controller.bezier_handle_id(span.id, "control1")

        handles = self.controller.editable_handle_positions(entity_id)
        self.assertIn((handle_id, Vec2(10, 20), "bezier_control"), handles)
        preview = self.controller.preview_bezier_handle(entity_id, handle_id, Vec2(8, 30))
        self.assertEqual(self.document.get_entity(entity_id).spans[0].control1, Vec2(10, 20))
        self.assertEqual(preview.spans[0].control1, Vec2(8, 30))
        self.assertEqual(preview.spans[0].control2, Vec2(30, 20))

        self.assertTrue(self.controller.move_bezier_handle(entity_id, handle_id, Vec2(8, 30)))
        moved = self.document.get_entity(entity_id).spans[0]
        self.assertEqual(moved.control1, Vec2(8, 30))
        self.assertEqual(moved.start, Vec2(0, 0))
        self.assertEqual(moved.end, Vec2(40, 0))

        self.history.undo()
        self.assertEqual(self.document.get_entity(entity_id).spans[0].control1, Vec2(10, 20))


if __name__ == "__main__":
    unittest.main()
