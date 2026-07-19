import unittest

from woodcam_editor.domain.document import Layer, VectorDocument
from woodcam_editor.domain.entities import CircleEntity
from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.importers.layers import remap_import_layers
from woodcam_editor.importers.part_shape import ImportResult, import_part_shape


class _Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
        self.z = 0.0


class _Vertex:
    def __init__(self, point):
        self.Point = point


class _LineCurve:
    pass


class _Edge:
    def __init__(self, start, end):
        self.Vertexes = (_Vertex(_Point(*start)), _Vertex(_Point(*end)))
        self.Curve = _LineCurve()

    def isClosed(self):
        return False

    def isSame(self, other):
        return self is other


class _Wire:
    def __init__(self, edges, closed=True):
        self.OrderedEdges = tuple(edges)
        self.Edges = tuple(edges)
        self._closed = closed

    def isClosed(self):
        return self._closed


class _Shape:
    Solids = ()

    def __init__(self, wires, edges):
        self.Wires = tuple(wires)
        self.Edges = tuple(edges)

    def isNull(self):
        return False


class ImportLayerRemapTests(unittest.TestCase):
    def test_part_wire_reorients_alternating_occ_edges_without_moving_points(self):
        edges = (
            _Edge((0, 0), (10, 0)),
            _Edge((10, 10), (10, 0)),
            _Edge((0, 10), (10, 10)),
            _Edge((0, 0), (0, 10)),
        )
        result = import_part_shape(
            _Shape((_Wire(edges),), edges),
            layer_id="layer",
        )

        self.assertEqual(len(result.entities), 1)
        imported = result.entities[0]
        self.assertTrue(imported.closed)
        self.assertEqual(
            {(span.start, span.end) for span in imported.spans},
            {
                (Vec2(0, 0), Vec2(10, 0)),
                (Vec2(10, 0), Vec2(10, 10)),
                (Vec2(10, 10), Vec2(0, 10)),
                (Vec2(0, 10), Vec2(0, 0)),
            },
        )

    def test_three_argument_import_result_remains_compatible(self):
        document = VectorDocument.create_default()
        entity = CircleEntity(document.active_layer_id, Vec2(10, 10), 2)
        result = ImportResult((entity,), (), {"source_kind": "legacy"})

        remapped = remap_import_layers(result, document)

        self.assertEqual(remapped.entities, (entity,))
        self.assertEqual(remapped.layers, ())
        self.assertEqual(remapped.source_to_layer_id, {})

    def test_mapping_descriptors_are_normalized_and_ids_avoid_collisions(self):
        source_document = VectorDocument.create_default()
        entity = CircleEntity(
            source_document.active_layer_id,
            Vec2(5, 5),
            1,
            metadata={"source_layer_key": "source-A"},
        )
        result = ImportResult(
            (entity,),
            (),
            {"source_kind": "test", "source_fingerprint": "same-file"},
            {
                "source-A": {
                    "name": "Camada importada",
                    "color": "#123456",
                    "purpose": "cut",
                }
            },
        )
        first_target = VectorDocument.create_default()
        first = remap_import_layers(result, first_target)
        blocked_id = first.layers[0].id

        second_target = VectorDocument.create_default()
        second_target.add_layers(
            (Layer(id=blocked_id, name="ID já usado", order=99),)
        )
        before = second_target.clone()
        second = remap_import_layers(result, second_target)

        self.assertNotEqual(second.layers[0].id, blocked_id)
        self.assertTrue(second.layers[0].id.endswith("-2"))
        self.assertEqual(second.layers[0].name, "Camada importada")
        self.assertEqual(second.layers[0].color, "#123456")
        self.assertEqual(second.layers[0].purpose, "cut")
        self.assertEqual(second.entities[0].layer_id, second.layers[0].id)
        self.assertEqual(second_target.layers_by_id, before.layers_by_id)
        self.assertEqual(second_target.revision, before.revision)


if __name__ == "__main__":
    unittest.main()
