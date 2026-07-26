import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import (
    AddEntitiesCommand,
    GroupEntitiesCommand,
    InMemoryCommandHistory,
    PathEntity,
    ReversePathDirectionCommand,
    Vec2,
    VectorDocument,
)
from woodcam_editor.geometry.math2d import signed_area


class ReversePathDirectionTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.history = InMemoryCommandHistory(self.document)
        self.path = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(80, 0), Vec2(80, 40), Vec2(0, 40)),
            closed=True,
            id="outer",
        )
        self.inner = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(20, 10), Vec2(20, 30), Vec2(50, 30), Vec2(50, 10)),
            closed=True,
            id="inner",
        )
        self.history.execute(AddEntitiesCommand((self.path, self.inner)))

    def test_command_reverses_exact_traversal_with_stable_ids_and_undo(self):
        before = self.document.get_entity("outer")
        before_area = signed_area(before.flatten(include_closure=False))
        before_nodes = before.node_ids

        self.history.execute(ReversePathDirectionCommand(("outer",)))
        reversed_path = self.document.get_entity("outer")
        after_area = signed_area(reversed_path.flatten(include_closure=False))

        self.assertEqual(reversed_path.id, "outer")
        self.assertEqual(set(reversed_path.node_ids), set(before_nodes))
        self.assertAlmostEqual(after_area, -before_area)
        self.assertEqual(reversed_path.bounds(), before.bounds())

        self.history.undo()
        self.assertEqual(self.document.get_entity("outer"), before)

    def test_controller_expands_piece_group_and_reverses_paths_as_one_command(self):
        controller = EditorController(self.document, history=self.history)
        self.history.execute(GroupEntitiesCommand(("outer", "inner"), group_id="piece"))
        outer_before = self.document.get_entity("outer")
        inner_before = self.document.get_entity("inner")
        controller.selection.select_only("piece")

        changed = controller.reverse_path_directions()

        self.assertEqual(changed, ("outer", "inner"))
        self.assertAlmostEqual(
            signed_area(self.document.get_entity("outer").flatten(include_closure=False)),
            -signed_area(outer_before.flatten(include_closure=False)),
        )
        self.assertAlmostEqual(
            signed_area(self.document.get_entity("inner").flatten(include_closure=False)),
            -signed_area(inner_before.flatten(include_closure=False)),
        )
        self.assertIn("piece", self.document.entities_by_id)

        self.history.undo()
        self.assertEqual(self.document.get_entity("outer"), outer_before)
        self.assertEqual(self.document.get_entity("inner"), inner_before)


if __name__ == "__main__":
    unittest.main()
