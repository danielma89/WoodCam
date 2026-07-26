import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import (
    AddEntitiesCommand,
    CircleEntity,
    GroupEntitiesCommand,
    InMemoryCommandHistory,
    UngroupEntitiesCommand,
    Vec2,
    VectorDocument,
)


class GroupingCommandTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.history = InMemoryCommandHistory(self.document)
        self.first = CircleEntity(
            self.document.active_layer_id, Vec2(10, 10), 2, id="first"
        )
        self.second = CircleEntity(
            self.document.active_layer_id, Vec2(30, 10), 2, id="second"
        )
        self.history.execute(AddEntitiesCommand((self.first, self.second)))

    def test_group_and_ungroup_preserve_child_geometry_and_undo(self):
        command = GroupEntitiesCommand(("first", "second"), group_id="group")
        self.history.execute(command)

        group = self.document.get_entity("group")
        self.assertEqual(group.child_ids, ("first", "second"))
        self.assertEqual(self.document.get_entity("first").center, Vec2(10, 10))
        self.assertEqual(self.document.get_entity("second").center, Vec2(30, 10))

        self.history.execute(UngroupEntitiesCommand(("group",)))
        self.assertNotIn("group", self.document.entities_by_id)
        self.assertIn("first", self.document.entities_by_id)
        self.assertIn("second", self.document.entities_by_id)

        self.history.undo()
        self.assertIn("group", self.document.entities_by_id)
        self.history.undo()
        self.assertNotIn("group", self.document.entities_by_id)

    def test_command_rejects_child_and_selected_parent_together(self):
        self.history.execute(GroupEntitiesCommand(("first", "second"), group_id="group"))
        with self.assertRaisesRegex(Exception, "parent group"):
            self.history.execute(GroupEntitiesCommand(("group", "first")))


class GroupingControllerTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.history = InMemoryCommandHistory(self.document)
        self.controller = EditorController(self.document, history=self.history)
        self.history.execute(
            AddEntitiesCommand(
                (
                    CircleEntity(self.document.active_layer_id, Vec2(10, 10), 2, id="first"),
                    CircleEntity(self.document.active_layer_id, Vec2(30, 10), 2, id="second"),
                )
            )
        )
        self.controller.execute(GroupEntitiesCommand(("first", "second"), group_id="group"))

    def test_click_member_resolves_group_and_move_moves_each_leaf_once(self):
        self.assertEqual(self.controller.grouped_selection_id_for_hit("first"), "group")
        self.controller.selection.select_only("group")
        self.assertTrue(self.controller.move_entities(self.controller.selection.ids, Vec2(5, -2)))
        self.assertEqual(self.document.get_entity("first").center, Vec2(15, 8))
        self.assertEqual(self.document.get_entity("second").center, Vec2(35, 8))
        self.assertIn("group", self.document.entities_by_id)

    def test_canonical_selection_uses_group_once(self):
        self.assertEqual(
            self.controller.canonical_group_selection(("first", "second", "group")),
            ("group",),
        )


if __name__ == "__main__":
    unittest.main()
