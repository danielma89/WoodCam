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
    Piece2D,
    ReplacePiecesCommand,
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

    def test_copy_group_recreates_piece_relation_with_fresh_references(self):
        outer = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(40, 0), Vec2(40, 30), Vec2(0, 30)),
            closed=True,
            id="outer-piece",
        )
        hole = CircleEntity(
            self.document.active_layer_id, Vec2(20, 15), 3, id="hole-piece"
        )
        pocket = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(5, 5), Vec2(15, 5), Vec2(15, 10), Vec2(5, 10)),
            closed=True,
            id="pocket-piece",
        )
        marking = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(10, 20), Vec2(30, 20)),
            id="marking-piece",
        )
        self.history.execute(AddEntitiesCommand((outer, hole, pocket, marking)))
        self.history.execute(
            GroupEntitiesCommand(
                (outer.id, hole.id, pocket.id, marking.id),
                group_id="piece-group",
            )
        )
        source_piece = Piece2D(
            "Frente",
            outer.id,
            (hole.id,),
            id="piece-source",
            metadata={
                "pocket_path_ids": [pocket.id],
                "marking_path_ids": [marking.id],
            },
        )
        self.history.execute(ReplacePiecesCommand((source_piece,)))

        command = ArrayCopyCommand(("piece-group",), 2, 1, 60.0, 0.0)
        self.history.execute(command)

        self.assertEqual(len(command.created_piece_ids), 1)
        copied_piece = self.document.pieces_by_id[command.created_piece_ids[0]]
        copied_group = self.document.get_entity(command.created_root_ids[0])
        self.assertNotEqual(copied_piece.id, source_piece.id)
        self.assertEqual(
            {
                copied_piece.outer_path_id,
                *copied_piece.inner_path_ids,
                *copied_piece.pocket_path_ids,
                *copied_piece.marking_path_ids,
            },
            set(copied_group.child_ids),
        )
        self.assertEqual(copied_piece.metadata["copy_source_piece_id"], source_piece.id)
        self.history.undo()
        self.assertEqual(set(self.document.pieces_by_id), {source_piece.id})

    def test_clipboard_snapshot_pastes_after_source_is_deleted_and_cascades(self):
        circle = CircleEntity(self.document.active_layer_id, Vec2(10, 10), 2, id="clip")
        self.history.execute(AddEntitiesCommand((circle,)))
        controller = EditorController(self.document, history=self.history)
        controller.selection.select_only(circle.id)

        self.assertEqual(controller.copy_selection(), 1)
        self.assertTrue(controller.delete_selected())
        self.assertEqual(controller.paste_copied(), 1)
        first = self.document.get_entity(controller.selection.primary_id)
        self.assertEqual(first.center, Vec2(20, 10))
        self.assertEqual(controller.paste_copied(), 1)
        second = self.document.get_entity(controller.selection.primary_id)
        self.assertEqual(second.center, Vec2(30, 10))
        self.assertNotEqual(first.id, second.id)

    def test_clipboard_snapshot_does_not_clone_unrelated_cabinet_vectors(self):
        selected = CircleEntity(
            self.document.active_layer_id, Vec2(10, 10), 2, id="selected"
        )
        unrelated = tuple(
            CircleEntity(
                self.document.active_layer_id,
                Vec2(float(index), 100.0),
                1.0,
                id="unrelated-%04d" % index,
            )
            for index in range(1000)
        )
        self.history.execute(AddEntitiesCommand((selected, *unrelated)))
        controller = EditorController(self.document, history=self.history)
        controller.selection.select_only(selected.id)

        self.assertEqual(controller.copy_selection(), 1)

        self.assertEqual(
            set(controller._clipboard_document.entities_by_id),
            {selected.id},
        )

    def test_copying_recognized_outer_includes_ungrouped_hole_and_pocket(self):
        outer = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(50, 0), Vec2(50, 40), Vec2(0, 40)),
            closed=True,
            id="recognized-outer",
        )
        hole = CircleEntity(
            self.document.active_layer_id, Vec2(25, 20), 3, id="recognized-hole"
        )
        pocket = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(5, 5), Vec2(15, 5), Vec2(15, 15), Vec2(5, 15)),
            closed=True,
            id="recognized-pocket",
        )
        self.history.execute(AddEntitiesCommand((outer, hole, pocket)))
        self.history.execute(
            ReplacePiecesCommand(
                (
                    Piece2D(
                        "Lateral",
                        outer.id,
                        (hole.id,),
                        metadata={"pocket_path_ids": [pocket.id]},
                    ),
                )
            )
        )
        controller = EditorController(self.document, history=self.history)
        controller.selection.select_only(outer.id)

        self.assertEqual(controller.copy_selection(), 3)
        self.assertEqual(controller.paste_copied(), 3)
        self.assertEqual(len(self.document.pieces_by_id), 2)
        copied_piece = next(
            piece
            for piece in self.document.pieces_by_id.values()
            if piece.outer_path_id != outer.id
        )
        self.assertNotEqual(copied_piece.inner_path_ids, (hole.id,))
        self.assertNotEqual(copied_piece.pocket_path_ids, (pocket.id,))


if __name__ == "__main__":
    unittest.main()
