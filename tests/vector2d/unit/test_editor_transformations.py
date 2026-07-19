"""Production transform use-cases exposed by EditorController."""

from __future__ import annotations

import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import Layer, PathEntity, Vec2, VectorDocument


def rectangle(document, x, y, width, height, layer_id=None):
    return PathEntity.from_points(
        layer_id or document.active_layer_id,
        (
            Vec2(x, y),
            Vec2(x + width, y),
            Vec2(x + width, y + height),
            Vec2(x, y + height),
        ),
        closed=True,
    )


class EditorTransformationTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.controller = EditorController(self.document)

    def add(self, *entities):
        self.document.add_entities(entities)
        self.controller.selection.replace(entity.id for entity in entities)

    def test_rotate_uses_selection_center_and_one_undo(self):
        entity = rectangle(self.document, 0.0, 0.0, 100.0, 40.0)
        self.add(entity)
        center = entity.bounds().center
        self.assertTrue(self.controller.rotate_selection(90.0))
        rotated = self.document.get_entity(entity.id)
        self.assertAlmostEqual(rotated.bounds().width, 40.0, places=7)
        self.assertAlmostEqual(rotated.bounds().height, 100.0, places=7)
        self.assertTrue(rotated.bounds().center.almost_equals(center, 1e-8))
        self.controller.undo()
        self.assertEqual(self.document.get_entity(entity.id), entity)

    def test_non_uniform_exact_scale_and_position(self):
        entity = rectangle(self.document, 0.0, 0.0, 20.0, 10.0)
        self.add(entity)
        self.assertTrue(self.controller.set_selection_bounds(5.0, 10.0, 40.0, 15.0))
        bounds = self.document.get_entity(entity.id).bounds()
        self.assertAlmostEqual(bounds.min_x, 5.0)
        self.assertAlmostEqual(bounds.min_y, 10.0)
        self.assertAlmostEqual(bounds.width, 40.0)
        self.assertAlmostEqual(bounds.height, 15.0)
        self.controller.undo()
        self.assertEqual(self.document.get_entity(entity.id), entity)

    def test_mirror_horizontal_and_vertical_are_undoable(self):
        triangle = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0.0, 0.0), Vec2(20.0, 0.0), Vec2(0.0, 10.0)),
            closed=True,
        )
        self.add(triangle)
        self.controller.mirror_selection("vertical")
        mirrored = self.document.get_entity(triangle.id)
        self.assertEqual(mirrored.spans[0].start, Vec2(20.0, 0.0))
        self.controller.undo()
        self.assertEqual(self.document.get_entity(triangle.id), triangle)
        self.controller.mirror_selection("horizontal")
        mirrored = self.document.get_entity(triangle.id)
        self.assertEqual(mirrored.spans[0].start, Vec2(0.0, 10.0))

    def test_align_and_distribute_are_each_one_composite_undo(self):
        first = rectangle(self.document, 0.0, 0.0, 10.0, 10.0)
        second = rectangle(self.document, 50.0, 20.0, 20.0, 10.0)
        third = rectangle(self.document, 120.0, 40.0, 10.0, 10.0)
        self.add(first, second, third)
        originals = dict(self.document.entities_by_id)
        self.assertTrue(self.controller.align_selection("bottom"))
        self.assertEqual(
            {round(self.document.get_entity(value.id).bounds().min_y, 8) for value in (first, second, third)},
            {0.0},
        )
        self.controller.undo()
        self.assertEqual(self.document.entities_by_id, originals)
        self.assertTrue(self.controller.distribute_selection("horizontal"))
        ordered = sorted(
            (self.document.get_entity(value.id).bounds() for value in (first, second, third)),
            key=lambda bounds: bounds.min_x,
        )
        gap1 = ordered[1].min_x - ordered[0].max_x
        gap2 = ordered[2].min_x - ordered[1].max_x
        self.assertAlmostEqual(gap1, gap2, places=8)
        self.controller.undo()
        self.assertEqual(self.document.entities_by_id, originals)

    def test_locked_layer_rejects_entire_transform(self):
        locked = Layer(name="Travada", locked=True, order=1)
        self.document.add_layers((locked,))
        entity = rectangle(self.document, 0.0, 0.0, 10.0, 10.0, locked.id)
        self.add(entity)
        before_revision = self.document.revision
        with self.assertRaises(PermissionError):
            self.controller.rotate_selection(45.0)
        self.assertEqual(self.document.get_entity(entity.id), entity)
        self.assertEqual(self.document.revision, before_revision)


if __name__ == "__main__":
    unittest.main()

