import json
import unittest

from woodcam_editor.domain import (
    ArcSpan,
    CircleEntity,
    CubicBezierSpan,
    EllipseEntity,
    Layer,
    LineSpan,
    PathEntity,
    Piece2D,
    SerializationError,
    Vec2,
    VectorDocument,
    WorkArea,
    deserialize_document,
    document_checksum,
    geometry_checksum,
    document_from_dict,
    document_to_dict,
    serialize_document,
)


def complex_document():
    design = Layer(name="Corte", id="layer-design", order=0, purpose="cut")
    reference = Layer(name="Referência", id="layer-ref", order=1, purpose="reference", locked=True)
    path = PathEntity(
        id="path-main",
        layer_id=design.id,
        spans=(
            LineSpan(Vec2(10, 10), Vec2(50, 10), id="span-line"),
            ArcSpan(Vec2(50, 10), Vec2(60, 20), Vec2(50, 20), False, id="span-arc"),
            CubicBezierSpan(Vec2(60, 20), Vec2(60, 40), Vec2(10, 40), Vec2(10, 10), id="span-bezier"),
        ),
        closed=True,
        node_ids=("node-a", "node-b", "node-c"),
        metadata={"code": "P01"},
    )
    hole = CircleEntity(
        design.id,
        Vec2(30, 22),
        3,
        id="circle-hole",
        center_node_id="node-center",
        radius_node_id="node-radius",
    )
    ellipse = EllipseEntity(reference.id, Vec2(80, 30), 8, 3, id="ellipse-ref", center_node_id="node-e")
    piece = Piece2D(
        "Lateral",
        path.id,
        (hole.id,),
        id="piece-main",
        quantity=2,
        material="MDF",
        thickness=15,
        metadata={"grain": "x"},
    )
    return VectorDocument(
        document_uuid="document-fixed",
        revision=17,
        work_area=WorkArea(0, 0, 1850, 2750),
        layers_by_id={design.id: design, reference.id: reference},
        entities_by_id={path.id: path, hole.id: hole, ellipse.id: ellipse},
        pieces_by_id={piece.id: piece},
        active_layer_id=design.id,
        metadata={"project": "Teste"},
    )


class DocumentTests(unittest.TestCase):
    def test_default_document_and_batch_revision(self):
        document = VectorDocument.create_default(WorkArea(0, 0, 100, 200))
        self.assertEqual(len(document.layers_by_id), 1)
        self.assertEqual(document.revision, 0)
        entities = (
            CircleEntity(document.active_layer_id, Vec2(10, 10), 2),
            CircleEntity(document.active_layer_id, Vec2(20, 10), 3),
        )
        document.add_entities(entities)
        self.assertEqual(document.revision, 1)
        self.assertEqual(document.bounds().max_x, 23)

    def test_clone_is_independent(self):
        document = complex_document()
        clone = document.clone()
        clone.metadata["project"] = "Outro"
        clone.entities_by_id.clear()
        self.assertEqual(document.metadata["project"], "Teste")
        self.assertEqual(len(document.entities_by_id), 3)

    def test_remove_outer_removes_piece_but_not_inner_entity(self):
        document = complex_document()
        document.remove_entities(("path-main",))
        self.assertNotIn("piece-main", document.pieces_by_id)
        self.assertIn("circle-hole", document.entities_by_id)


class SerializationTests(unittest.TestCase):
    def test_round_trip_is_semantically_identical_and_ids_stable(self):
        original = complex_document()
        serialized = serialize_document(original)
        restored = deserialize_document(serialized.json_text, serialized.checksum)
        self.assertEqual(document_to_dict(restored), document_to_dict(original))
        self.assertEqual(restored.document_uuid, "document-fixed")
        self.assertIn("span-arc", [span.id for span in restored.get_entity("path-main").spans])
        self.assertEqual(serialized.schema_version, 1)
        self.assertEqual(serialized.revision, 17)

    def test_canonical_checksum_ignores_input_key_order_and_whitespace(self):
        document = complex_document()
        data = document_to_dict(document)
        reordered_text = json.dumps(dict(reversed(list(data.items()))), indent=4, ensure_ascii=False)
        restored = deserialize_document(reordered_text, document_checksum(document))
        self.assertEqual(document_checksum(restored), document_checksum(document))

    def test_unknown_top_level_field_survives_round_trip(self):
        data = document_to_dict(complex_document())
        data["future_extension"] = {"enabled": True, "value": 42}
        restored = document_from_dict(data)
        self.assertEqual(document_to_dict(restored)["future_extension"], data["future_extension"])

    def test_corrupt_checksum_invalid_json_and_future_schema_are_rejected(self):
        serialized = serialize_document(complex_document())
        with self.assertRaises(SerializationError):
            deserialize_document(serialized.json_text, "0" * 64)
        with self.assertRaises(SerializationError):
            deserialize_document("{broken")
        data = document_to_dict(complex_document())
        data["schema_version"] = 999
        with self.assertRaises(SerializationError):
            document_from_dict(data)

    def test_non_json_metadata_is_rejected_before_overwriting_storage(self):
        document = complex_document()
        document.metadata["bad"] = object()
        with self.assertRaises(SerializationError):
            serialize_document(document)

    def test_geometry_checksum_ignores_piece_cache_but_changes_with_vectors(self):
        document = complex_document()
        before = geometry_checksum(document)
        document.pieces_by_id["piece-1"] = Piece2D(
            id="piece-1",
            name="Peça 01",
            outer_path_id="path-main",
            inner_path_ids=("circle-hole",),
        )
        self.assertEqual(before, geometry_checksum(document))
        document.entities_by_id["circle-hole"] = CircleEntity(
            document.active_layer_id, Vec2(31, 22), 3, id="circle-hole"
        )
        self.assertNotEqual(before, geometry_checksum(document))


if __name__ == "__main__":
    unittest.main()
