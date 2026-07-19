import unittest

from woodcam_editor.application.piece_organizer import classify_document_pieces
from woodcam_editor.domain import (
    CircleEntity,
    InMemoryCommandHistory,
    Layer,
    PathEntity,
    Piece2D,
    SetDocumentMetadataCommand,
    SetWorkAreaCommand,
    Vec2,
    VectorDocument,
    WorkArea,
    deserialize_document,
    serialize_document,
    validate_document,
)


def path(document, layer_id, points, *, closed=False, entity_id):
    return PathEntity.from_points(
        layer_id,
        tuple(points),
        closed=closed,
        id=entity_id,
    )


class SetWorkAreaCommandTests(unittest.TestCase):
    def test_apply_serialize_undo_redo_preserves_piece_state_and_revision(self):
        old_area = WorkArea(0, 0, 100, 100, "old")
        new_area = WorkArea(-10, -20, 300, 200, "woodcam_trabalho")
        document = VectorDocument.create_default(old_area)
        outer = path(
            document,
            document.active_layer_id,
            (Vec2(0, 0), Vec2(50, 0), Vec2(50, 50), Vec2(0, 50)),
            closed=True,
            entity_id="outer",
        )
        document.add_entities((outer,), bump_revision=False)
        document.add_pieces(
            (Piece2D("P", outer.id, id="piece", stale=True),),
            bump_revision=False,
        )
        history = InMemoryCommandHistory(document)

        changes = history.execute(SetWorkAreaCommand(new_area))
        self.assertTrue(changes.work_area_changed)
        self.assertEqual(document.work_area, new_area)
        self.assertEqual(document.revision, 1)
        self.assertTrue(document.pieces_by_id["piece"].stale)

        stored = serialize_document(document)
        restored = deserialize_document(stored.json_text, stored.checksum)
        self.assertEqual(restored.work_area, new_area)
        self.assertTrue(restored.pieces_by_id["piece"].stale)

        undo_changes = history.undo()
        self.assertTrue(undo_changes.work_area_changed)
        self.assertEqual(document.work_area, old_area)
        self.assertEqual(document.revision, 2)
        self.assertTrue(document.pieces_by_id["piece"].stale)

        history.redo()
        self.assertEqual(document.work_area, new_area)
        self.assertEqual(document.revision, 3)
        self.assertTrue(document.pieces_by_id["piece"].stale)
        document.validate_invariants()

    def test_virtual_sheet_bounds_are_undoable_and_count_as_work_area(self):
        document = VectorDocument.create_default(WorkArea(0, 0, 100, 100))
        second_sheet_path = path(
            document,
            document.active_layer_id,
            (Vec2(160, 10), Vec2(180, 10), Vec2(180, 30), Vec2(160, 30)),
            closed=True,
            entity_id="second-sheet-piece",
        )
        document.add_entities((second_sheet_path,))
        history = InMemoryCommandHistory(document)
        self.assertEqual(
            len(validate_document(document).by_code("OUTSIDE_WORK_AREA")), 1
        )

        history.execute(
            SetDocumentMetadataCommand(
                "organization_sheet_bounds",
                [[0, 0, 100, 100], [150, 0, 250, 100]],
            )
        )

        self.assertFalse(validate_document(document).by_code("OUTSIDE_WORK_AREA"))
        history.undo()
        self.assertEqual(
            len(validate_document(document).by_code("OUTSIDE_WORK_AREA")), 1
        )

    def test_command_can_clear_area_and_rejects_non_domain_value_before_history(self):
        original = WorkArea(0, 0, 100, 100)
        document = VectorDocument.create_default(original)
        history = InMemoryCommandHistory(document)
        history.execute(SetWorkAreaCommand(None))
        self.assertIsNone(document.work_area)
        self.assertIsNone(deserialize_document(serialize_document(document).json_text).work_area)
        history.undo()
        self.assertEqual(document.work_area, original)

        with self.assertRaises(TypeError):
            SetWorkAreaCommand({"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10})
        self.assertEqual(document.work_area, original)


class ValidationLayerScopeTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.design = self.document.active_layer
        self.hidden = Layer(name="Oculta", id="hidden", visible=False, order=1)
        self.reference = Layer(
            name="Referência", id="reference", purpose="reference", order=2
        )
        self.construction = Layer(
            name="Construção", id="construction", purpose="construction", order=3
        )
        self.document.add_layers(
            (self.hidden, self.reference, self.construction),
            bump_revision=False,
        )

    def test_open_paths_on_hidden_reference_and_construction_layers_do_not_block_cam(self):
        entities = (
            path(
                self.document,
                self.design.id,
                (Vec2(0, 0), Vec2(10, 0)),
                entity_id="visible-open",
            ),
            path(
                self.document,
                self.hidden.id,
                (Vec2(0, 10), Vec2(10, 10)),
                entity_id="hidden-open",
            ),
            path(
                self.document,
                self.reference.id,
                (Vec2(0, 20), Vec2(10, 20)),
                entity_id="reference-open",
            ),
            path(
                self.document,
                self.construction.id,
                (Vec2(0, 30), Vec2(10, 30)),
                entity_id="construction-open",
            ),
        )
        self.document.add_entities(entities, bump_revision=False)

        report = validate_document(self.document)
        self.assertEqual(
            {issue.entity_ids[0] for issue in report.by_code("OPEN_PATH")},
            {"visible-open"},
        )
        all_layers = validate_document(self.document, include_non_cam_layers=True)
        self.assertEqual(
            {issue.entity_ids[0] for issue in all_layers.by_code("OPEN_PATH")},
            {"visible-open", "hidden-open", "reference-open", "construction-open"},
        )

    def test_pieces_with_hidden_or_reference_outer_do_not_create_containment_blockers(self):
        hidden_outer = path(
            self.document,
            self.hidden.id,
            (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)),
            closed=True,
            entity_id="hidden-outer",
        )
        reference_outer = path(
            self.document,
            self.reference.id,
            (Vec2(100, 0), Vec2(110, 0), Vec2(110, 10), Vec2(100, 10)),
            closed=True,
            entity_id="reference-outer",
        )
        first_inner = CircleEntity(
            self.design.id, Vec2(30, 30), 2, id="first-outside-inner"
        )
        second_inner = CircleEntity(
            self.design.id, Vec2(130, 30), 2, id="second-outside-inner"
        )
        self.document.add_entities(
            (hidden_outer, reference_outer, first_inner, second_inner),
            bump_revision=False,
        )
        self.document.add_pieces(
            (
                Piece2D("Hidden", hidden_outer.id, (first_inner.id,), id="hidden-piece"),
                Piece2D(
                    "Reference",
                    reference_outer.id,
                    (second_inner.id,),
                    id="reference-piece",
                ),
            ),
            bump_revision=False,
        )

        self.assertFalse(validate_document(self.document).by_code("INNER_OUTSIDE_PIECE"))
        self.assertEqual(
            len(
                validate_document(
                    self.document,
                    include_non_cam_layers=True,
                ).by_code("INNER_OUTSIDE_PIECE")
            ),
            2,
        )


class _LayeredPath:
    def __init__(self, entity_id, layer_id, points, *, closed=True, visible=True):
        self.id = entity_id
        self.layer_id = layer_id
        self.points = points
        self.closed = closed
        # Deliberately conflicts with the layer in one test.  Classification
        # must not use this presentation-like entity attribute.
        self.visible = visible


class _LayeredDocument:
    def __init__(self, layers, entities):
        self.layers_by_id = {layer.id: layer for layer in layers}
        self.entities_by_id = {entity.id: entity for entity in entities}


class PieceClassificationLayerScopeTests(unittest.TestCase):
    def test_only_cam_layers_are_classified_and_entity_visible_is_ignored(self):
        layers = (
            Layer(name="Produção", id="design"),
            Layer(name="Oculta", id="hidden", visible=False),
            Layer(name="Referência", id="reference", purpose="reference"),
            Layer(name="Construção", id="construction", purpose="construction"),
        )

        def rectangle(entity_id, layer_id, x, *, visible=True):
            return _LayeredPath(
                entity_id,
                layer_id,
                ((x, 0), (x + 10, 0), (x + 10, 10), (x, 10)),
                visible=visible,
            )

        entities = (
            rectangle("design-outer", "design", 0, visible=False),
            rectangle("hidden-outer", "hidden", 20),
            rectangle("reference-outer", "reference", 40),
            rectangle("construction-outer", "construction", 60),
        )
        document = _LayeredDocument(layers, entities)

        classified = classify_document_pieces(document)
        self.assertEqual(set(classified.loops), {"design-outer"})
        self.assertEqual(
            {piece.outer_id for piece in classified.pieces},
            {"design-outer"},
        )

        all_layers = classify_document_pieces(
            document,
            include_non_cam_layers=True,
        )
        self.assertEqual(set(all_layers.loops), {entity.id for entity in entities})

    def test_ignored_layer_open_paths_do_not_enter_open_diagnostics(self):
        layers = (
            Layer(name="Oculta", id="hidden", visible=False),
            Layer(name="Referência", id="reference", purpose="reference"),
        )
        entities = (
            _LayeredPath("hidden-open", "hidden", ((0, 0), (10, 0)), closed=False),
            _LayeredPath(
                "reference-open",
                "reference",
                ((0, 10), (10, 10)),
                closed=False,
            ),
        )
        document = _LayeredDocument(layers, entities)
        self.assertFalse(classify_document_pieces(document).open_entity_ids)
        self.assertEqual(
            set(
                classify_document_pieces(
                    document,
                    include_non_cam_layers=True,
                ).open_entity_ids
            ),
            {"hidden-open", "reference-open"},
        )


if __name__ == "__main__":
    unittest.main()
