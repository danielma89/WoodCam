"""The star tool creates one ordinary closed, command-backed vector."""

from __future__ import annotations

import math
import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import InMemoryCommandHistory, Vec2, VectorDocument


class StarCreationTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.history = InMemoryCommandHistory(self.document)
        self.controller = EditorController(self.document, history=self.history)

    def test_star_has_alternating_tips_and_undoes_in_one_step(self):
        entity_id = self.controller.add_star(
            Vec2(10.0, 20.0), 30.0, points=5, inner_ratio=0.4, rotation=0.0
        )
        self.assertIsNotNone(entity_id)
        entity = self.document.get_entity(entity_id)
        self.assertTrue(entity.closed)
        self.assertEqual(len(entity.spans), 10)
        self.assertEqual(len(entity.node_ids), 10)
        self.assertEqual(entity.metadata["primitive"], "star")
        self.assertEqual(entity.metadata["star_points"], 5)
        self.assertAlmostEqual(entity.metadata["star_inner_ratio"], 0.4)
        radii = [
            math.hypot(span.start.x - 10.0, span.start.y - 20.0)
            for span in entity.spans
        ]
        self.assertEqual([round(value, 6) for value in radii[::2]], [30.0] * 5)
        self.assertEqual([round(value, 6) for value in radii[1::2]], [12.0] * 5)
        self.assertEqual(tuple(self.controller.selection.ids), (entity_id,))

        self.history.undo()
        self.assertNotIn(entity_id, self.document.entities_by_id)

    def test_zero_radius_is_rejected(self):
        self.assertIsNone(self.controller.add_star(Vec2(0.0, 0.0), 0.0))
        self.assertFalse(self.document.entities_by_id)


if __name__ == "__main__":
    unittest.main()
