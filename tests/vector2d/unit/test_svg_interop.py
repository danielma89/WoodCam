import os
import tempfile
import unittest

from woodcam_editor.domain.document import Layer, VectorDocument
from woodcam_editor.domain.entities import (
    CircleEntity,
    EllipseEntity,
    GroupEntity,
    PathEntity,
)
from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.domain.spans import ArcSpan
from woodcam_editor.domain.validation import validate_document
from woodcam_editor.exporters.svg import export_svg
from woodcam_editor.importers.svg import import_svg
from woodcam_editor.importers.layers import remap_import_layers


FIXTURE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "fixtures", "complex.svg")
)


class SvgInteropTests(unittest.TestCase):
    def test_open_subpath_inside_closed_sibling_is_imported_as_piece_marking(self):
        svg = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
          <path d="M0 0H100V50H0Z M20 25H80" fill="none" stroke="#000"/>
        </svg>"""
        handle = tempfile.NamedTemporaryFile(
            suffix=".svg",
            mode="w",
            encoding="utf-8",
            delete=False,
        )
        try:
            handle.write(svg)
            handle.close()
            document = VectorDocument.create_default()
            imported = import_svg(
                handle.name,
                layer_id=document.active_layer_id,
            )
        finally:
            os.unlink(handle.name)

        self.assertEqual(len(imported.entities), 2)
        marking = next(entity for entity in imported.entities if not entity.closed)
        self.assertEqual(marking.metadata.get("woodcam_role"), "piece_marking")
        document.add_entities(imported.entities)
        self.assertFalse(validate_document(document).by_code("OPEN_PATH"))

    def test_selected_compound_expands_all_children_once(self):
        document = VectorDocument.create_default()
        contour_layer = Layer(
            id="layer-contour-hidden",
            name="Corte externo",
            color="#dc2626",
            purpose="cut",
            visible=False,
            order=10,
        )
        pocket_layer = Layer(
            id="layer-pocket",
            name="Rebaixo",
            color="#16a34a",
            purpose="pocket",
            order=11,
        )
        document.add_layers((contour_layer, pocket_layer))
        contour = PathEntity.from_points(
            contour_layer.id,
            (Vec2(0, 0), Vec2(80, 0), Vec2(80, 40), Vec2(0, 40)),
            closed=True,
        )
        pocket = CircleEntity(pocket_layer.id, Vec2(20, 20), 6)
        compound = GroupEntity(
            layer_id=document.active_layer_id,
            child_ids=(contour.id, pocket.id),
        )
        document.add_entities((contour, pocket, compound))

        handle = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
        handle.close()
        try:
            export_svg(
                document,
                handle.name,
                entity_ids=(compound.id, pocket.id),
                visible_only=False,
            )
            imported = import_svg(handle.name, layer_id="temporary")
        finally:
            os.unlink(handle.name)

        self.assertEqual(len(imported.entities), 2)
        self.assertEqual(
            sorted(type(entity).__name__ for entity in imported.entities),
            ["CircleEntity", "PathEntity"],
        )

    def test_imports_shapes_paths_transforms_units_and_y_up(self):
        result = import_svg(FIXTURE, layer_id="layer")

        self.assertEqual(len(result.entities), 3)
        plate = next(
            entity
            for entity in result.entities
            if isinstance(entity, PathEntity) and entity.closed
        )
        hole = next(entity for entity in result.entities if isinstance(entity, CircleEntity))
        curves = next(
            entity
            for entity in result.entities
            if isinstance(entity, PathEntity) and not entity.closed
        )
        self.assertAlmostEqual(plate.bounds().min_x, 10.0, places=8)
        self.assertAlmostEqual(plate.bounds().max_x, 50.0, places=8)
        self.assertAlmostEqual(plate.bounds().min_y, 35.0, places=8)
        self.assertAlmostEqual(plate.bounds().max_y, 55.0, places=8)
        self.assertAlmostEqual(hole.center.x, 20.0, places=8)
        self.assertAlmostEqual(hole.center.y, 45.0, places=8)
        self.assertEqual(
            [type(span).__name__ for span in curves.spans],
            ["CubicBezierSpan", "CubicBezierSpan", "ArcSpan"],
        )
        self.assertFalse(result.issues)

    def test_svg_export_round_trip_preserves_entity_kinds_and_bounds(self):
        document = VectorDocument.create_default()
        layer = document.active_layer_id
        rectangle = PathEntity.from_points(
            layer,
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
            closed=True,
        )
        circle = CircleEntity(layer, Vec2(20, 20), 5)
        ellipse = EllipseEntity(layer, Vec2(70, 25), 8, 4, rotation=0.25)
        document.add_entities((rectangle, circle, ellipse))

        handle = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
        handle.close()
        try:
            export_svg(document, handle.name)
            imported = import_svg(handle.name, layer_id=layer)
        finally:
            os.unlink(handle.name)

        self.assertEqual(len(imported.entities), 3)
        self.assertEqual(
            sorted(type(entity).__name__ for entity in imported.entities),
            ["CircleEntity", "EllipseEntity", "PathEntity"],
        )
        imported_path = next(entity for entity in imported.entities if isinstance(entity, PathEntity))
        self.assertAlmostEqual(imported_path.bounds().width, 100.0, places=7)
        self.assertAlmostEqual(imported_path.bounds().height, 50.0, places=7)

    def test_svg_round_trip_preserves_large_clockwise_arc(self):
        document = VectorDocument.create_default()
        layer = document.active_layer_id
        path = PathEntity(
            layer,
            (ArcSpan(Vec2(10, 0), Vec2(0, 10), Vec2(0, 0), clockwise=True),),
            closed=False,
        )
        document.add_entities((path,))
        handle = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
        handle.close()
        try:
            export_svg(document, handle.name)
            imported = import_svg(handle.name, layer_id=layer)
        finally:
            os.unlink(handle.name)

        imported_arc = imported.entities[0].spans[0]
        self.assertIsInstance(imported_arc, ArcSpan)
        self.assertTrue(imported_arc.clockwise)
        self.assertGreater(abs(imported_arc.sweep_angle), 3.14159)

    def test_multilayer_round_trip_preserves_name_color_purpose_and_membership(self):
        document = VectorDocument.create_default()
        contour_layer = Layer(
            id="layer-contour",
            name="Contorno",
            color="#dc2626",
            purpose="cut",
            order=10,
        )
        pocket_layer = Layer(
            id="layer-pocket",
            name="Bolsos",
            color="#16a34a",
            purpose="pocket",
            order=11,
        )
        document.add_layers((contour_layer, pocket_layer))
        contour = PathEntity.from_points(
            contour_layer.id,
            (Vec2(0, 0), Vec2(80, 0), Vec2(80, 40), Vec2(0, 40)),
            closed=True,
        )
        pocket = CircleEntity(pocket_layer.id, Vec2(20, 20), 6)
        document.add_entities((contour, pocket))

        handle = tempfile.NamedTemporaryFile(suffix=".svg", delete=False)
        handle.close()
        try:
            export_svg(document, handle.name, entity_ids=(contour.id, pocket.id))
            imported = import_svg(handle.name, layer_id="temporary-import-layer")
        finally:
            os.unlink(handle.name)

        self.assertEqual(set(imported.layers), {"layer-contour", "layer-pocket"})
        self.assertEqual(imported.layers["layer-contour"].name, "Contorno")
        self.assertEqual(imported.layers["layer-contour"].color, "#dc2626")
        self.assertEqual(imported.layers["layer-contour"].purpose, "cut")
        self.assertEqual(imported.layers["layer-pocket"].name, "Bolsos")
        self.assertEqual(imported.layers["layer-pocket"].color, "#16a34a")
        self.assertEqual(imported.layers["layer-pocket"].purpose, "pocket")
        self.assertEqual(
            {entity.metadata["source_layer_key"] for entity in imported.entities},
            {"layer-contour", "layer-pocket"},
        )

        target = VectorDocument.create_default()
        target_revision = target.revision
        remapped = remap_import_layers(imported, target)

        self.assertEqual(target.revision, target_revision)
        self.assertEqual(len(target.layers_by_id), 1)
        self.assertEqual(
            {(layer.name, layer.color, layer.purpose) for layer in remapped.layers},
            {("Contorno", "#dc2626", "cut"), ("Bolsos", "#16a34a", "pocket")},
        )
        for entity in remapped.entities:
            self.assertEqual(
                entity.layer_id,
                remapped.source_to_layer_id[entity.metadata["source_layer_key"]],
            )

    def test_nested_groups_inherit_layer_name_id_and_stroke(self):
        svg = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
          <g id="pockets" data-layer-name="Bolsos internos" style="stroke:#0f8;fill:none" data-layer-purpose="pocket">
            <g transform="translate(10 0)"><circle id="c1" cx="10" cy="20" r="5"/></g>
          </g>
        </svg>"""
        handle = tempfile.NamedTemporaryFile(suffix=".svg", mode="w", encoding="utf-8", delete=False)
        try:
            handle.write(svg)
            handle.close()
            imported = import_svg(handle.name, layer_id="temporary")
        finally:
            os.unlink(handle.name)

        descriptor = imported.layers["pockets"]
        self.assertEqual(descriptor.name, "Bolsos internos")
        self.assertEqual(descriptor.color, "#00ff88")
        self.assertEqual(descriptor.purpose, "pocket")
        self.assertEqual(imported.entities[0].metadata["source_layer_key"], "pockets")


if __name__ == "__main__":
    unittest.main()
