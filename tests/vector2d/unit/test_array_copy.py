import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import (
    AddEntitiesCommand,
    ArrayCopyCommand,
    CircleEntity,
    GroupEntitiesCommand,
    GroupEntity,
    InMemoryCommandHistory,
    PathEntity,
    Vec2,
    VectorDocument,
)


class ArrayCopyCommandTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.history = InMemoryCommandHistory(self.document)

    def test_copy_path_renews_entity_span_and_node_ids_and_undoes_once(self):
        source = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(20, 0)),
            id="source",
        )
        self.history.execute(AddEntitiesCommand((source,)))

        command = ArrayCopyCommand((source.id,), 3, 1, 50.0, 0.0)
        self.history.execute(command)

        self.assertEqual(len(self.document.entities_by_id), 3)
        copies = [self.document.get_entity(entity_id) for entity_id in command.created_root_ids]
        self.assertEqual([copy.start for copy in copies], [Vec2(50, 0), Vec2(100, 0)])
        self.assertTrue(all(copy.id != source.id for copy in copies))
        self.assertTrue(all(copy.spans[0].id != source.spans[0].id for copy in copies))
        self.assertTrue(all(copy.node_ids != source.node_ids for copy in copies))
        self.assertTrue(all(copy.metadata["copy_source_id"] == "source" for copy in copies))

        self.history.undo()
        self.assertEqual(set(self.document.entities_by_id), {"source"})
        self.history.redo()
        self.assertEqual(len(self.document.entities_by_id), 3)

    def test_copy_group_keeps_hole_together_with_outer_profile(self):
        outer = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(40, 0), Vec2(40, 30), Vec2(0, 30)),
            closed=True,
            id="outer",
        )
        hole = CircleEntity(self.document.active_layer_id, Vec2(20, 15), 3, id="hole")
        self.history.execute(AddEntitiesCommand((outer, hole)))
        self.history.execute(GroupEntitiesCommand((outer.id, hole.id), group_id="piece"))

        command = ArrayCopyCommand(("piece",), 2, 2, 100.0, 50.0)
        self.history.execute(command)

        self.assertEqual(len(command.created_root_ids), 3)
        copies = [self.document.get_entity(entity_id) for entity_id in command.created_root_ids]
        self.assertTrue(all(isinstance(copy, GroupEntity) for copy in copies))
        self.assertTrue(all(len(copy.child_ids) == 2 for copy in copies))
        copied_centers = sorted(
            (self.document.get_entity(group.child_ids[1]).center for group in copies),
            key=lambda center: (center.x, center.y),
        )
        self.assertEqual(copied_centers, [Vec2(20, 65), Vec2(120, 15), Vec2(120, 65)])

    def test_controller_replaces_selection_with_copied_roots(self):
        circle = CircleEntity(self.document.active_layer_id, Vec2(10, 10), 2, id="circle")
        self.history.execute(AddEntitiesCommand((circle,)))
        controller = EditorController(self.document, history=self.history)
        controller.selection.select_only(circle.id)

        self.assertTrue(controller.array_copy_selection(2, 1, 25.0, 0.0))
        self.assertEqual(len(controller.selection.ids), 1)
        copied = self.document.get_entity(controller.selection.primary_id)
        self.assertEqual(copied.center, Vec2(35, 10))


if __name__ == "__main__":
    unittest.main()
