import math
import os
import tempfile
import unittest

from woodcam_editor.domain.document import Layer, VectorDocument
from woodcam_editor.domain.entities import CircleEntity, EllipseEntity, PathEntity
from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.domain.spans import ArcSpan, CubicBezierSpan, LineSpan
from woodcam_editor.domain.validation import redundant_open_overline_entity_ids
from woodcam_editor.exporters.dxf import export_dxf
from woodcam_editor.importers.dxf import import_dxf
from woodcam_editor.importers.layers import remap_import_layers


FIXTURE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "fixtures", "inch_line.dxf")
)


class DxfInteropTests(unittest.TestCase):
    def test_panelnest_style_line_copies_are_detected_over_closed_polyline(self):
        document = VectorDocument.create_default()
        layer = document.active_layer_id
        rectangle = PathEntity.from_points(
            layer,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
            closed=True,
        )
        copied_sides = tuple(
            PathEntity(layer, (LineSpan(span.end, span.start),))
            for span in rectangle.spans
        )
        document.add_entities((rectangle,) + copied_sides)

        handle = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
        handle.close()
        target = VectorDocument.create_default()
        try:
            export_dxf(document, handle.name)
            imported = import_dxf(
                handle.name,
                layer_id=target.active_layer_id,
            )
        finally:
            os.unlink(handle.name)

        target.add_entities(imported.entities, bump_revision=False)
        self.assertEqual(
            len(redundant_open_overline_entity_ids(target)),
            4,
        )

    def test_insunits_inches_are_scaled_to_mm(self):
        result = import_dxf(FIXTURE, layer_id="layer")

        self.assertEqual(len(result.entities), 1)
        path = result.entities[0]
        self.assertIsInstance(path, PathEntity)
        self.assertAlmostEqual(path.length(), 25.4, places=9)
        self.assertEqual(result.source_metadata["dxf_insunits"], 1)

    def test_export_round_trip_preserves_common_exact_entities(self):
        document = VectorDocument.create_default()
        layer = document.active_layer_id
        rectangle = PathEntity.from_points(
            layer,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
            closed=True,
        )
        mixed = PathEntity(
            layer,
            (
                LineSpan(Vec2(0, 100), Vec2(10, 100)),
                ArcSpan(Vec2(10, 100), Vec2(15, 105), Vec2(10, 105), False),
                CubicBezierSpan(
                    Vec2(15, 105),
                    Vec2(17, 108),
                    Vec2(19, 103),
                    Vec2(20, 100),
                ),
            ),
            closed=False,
        )
        circle = CircleEntity(layer, Vec2(20, 20), 5)
        ellipse = EllipseEntity(layer, Vec2(70, 25), 8, 4, rotation=0.2)
        document.add_entities((rectangle, mixed, circle, ellipse))

        handle = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
        handle.close()
        try:
            export_dxf(document, handle.name)
            imported = import_dxf(handle.name, layer_id=layer)
        finally:
            os.unlink(handle.name)

        self.assertFalse([issue for issue in imported.issues if issue.severity != "info"])
        self.assertEqual(
            sorted(type(entity).__name__ for entity in imported.entities),
            ["CircleEntity", "EllipseEntity", "PathEntity", "PathEntity"],
        )
        closed = next(
            entity
            for entity in imported.entities
            if isinstance(entity, PathEntity) and entity.closed
        )
        self.assertAlmostEqual(closed.bounds().width, 100.0, places=8)
        imported_mixed = next(
            entity
            for entity in imported.entities
            if isinstance(entity, PathEntity) and not entity.closed
        )
        self.assertEqual(
            [type(span).__name__ for span in imported_mixed.spans],
            ["LineSpan", "ArcSpan", "CubicBezierSpan"],
        )

    def test_multilayer_round_trip_preserves_names_colors_and_membership(self):
        document = VectorDocument.create_default()
        exterior_layer = Layer(
            id="layer-exterior",
            name="Contorno externo",
            color="#e11d48",
            purpose="cut",
            order=10,
        )
        holes_layer = Layer(
            id="layer-holes",
            name="Furos",
            color="#2563eb",
            purpose="drill",
            order=11,
        )
        document.add_layers((exterior_layer, holes_layer))
        exterior = PathEntity.from_points(
            exterior_layer.id,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
            closed=True,
        )
        hole = CircleEntity(holes_layer.id, Vec2(25, 25), 5)
        document.add_entities((exterior, hole))

        handle = tempfile.NamedTemporaryFile(suffix=".dxf", delete=False)
        handle.close()
        try:
            export_dxf(document, handle.name, entity_ids=(exterior.id, hole.id))
            imported = import_dxf(handle.name, layer_id="temporary-import-layer")
        finally:
            os.unlink(handle.name)

        self.assertEqual(set(imported.layers), {"Contorno externo", "Furos"})
        self.assertEqual(imported.layers["Contorno externo"].color, "#e11d48")
        self.assertEqual(imported.layers["Furos"].color, "#2563eb")
        self.assertEqual(
            {entity.metadata["source_layer_key"] for entity in imported.entities},
            {"Contorno externo", "Furos"},
        )

        target = VectorDocument.create_default()
        original_layer_ids = set(target.layers_by_id)
        remapped = remap_import_layers(imported, target)

        self.assertEqual(set(target.layers_by_id), original_layer_ids)
        self.assertEqual(len(remapped.layers), 2)
        self.assertTrue(set(layer.id for layer in remapped.layers).isdisjoint(original_layer_ids))
        for entity in remapped.entities:
            source_key = entity.metadata["source_layer_key"]
            self.assertEqual(entity.layer_id, remapped.source_to_layer_id[source_key])


if __name__ == "__main__":
    unittest.main()
