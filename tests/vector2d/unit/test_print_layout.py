import math
import unittest
import xml.etree.ElementTree as ET

from woodcam_editor.application.print_layout import (
    recommend_paper,
    resolve_print_layout,
)
from woodcam_editor.domain.document import VectorDocument, WorkArea
from woodcam_editor.domain.entities import CircleEntity, PathEntity
from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.exporters.svg import SVG_NAMESPACE, document_sheet_to_svg


class PrintLayoutTests(unittest.TestCase):
    def test_recommends_smallest_iso_paper_that_fits_at_one_to_one(self):
        self.assertEqual(recommend_paper(((0, 0, 200, 100),)).name, "A4")
        self.assertEqual(recommend_paper(((0, 0, 400, 250),)).name, "A3")

    def test_large_mdf_sheet_uses_a0_and_rotates_for_best_readability(self):
        paper = recommend_paper(((0, 0, 1780, 2725),))
        self.assertEqual(paper.name, "A0")
        layout = resolve_print_layout((0, 0, 1780, 2725), paper)
        self.assertEqual(layout.rotation_degrees, 90)
        self.assertTrue(layout.fits)
        self.assertAlmostEqual(layout.scale, min(1169 / 2725, 821 / 1780))

    def test_explicit_one_to_one_is_reported_as_not_fitting(self):
        layout = resolve_print_layout(
            (0, 0, 1780, 2725), "A0", scale_mode="actual"
        )
        self.assertFalse(layout.fits)
        self.assertEqual(layout.scale, 1.0)

    def test_fit_mode_does_not_enlarge_a_small_sheet(self):
        layout = resolve_print_layout((0, 0, 50, 30), "A4", scale_mode="fit")
        self.assertEqual(layout.scale, 1.0)
        self.assertTrue(layout.fits)


class PrintSvgTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default(WorkArea(0, 0, 200, 100))
        layer_id = self.document.active_layer_id
        self.inside = PathEntity.from_points(
            layer_id,
            (Vec2(10, 10), Vec2(80, 10), Vec2(80, 60), Vec2(10, 60)),
            closed=True,
        )
        self.hole = CircleEntity(layer_id, Vec2(30, 30), 5)
        self.outside = CircleEntity(layer_id, Vec2(260, 30), 5)
        self.document.add_entities((self.inside, self.hole, self.outside))

    def test_sheet_svg_preserves_boundary_and_only_internal_content(self):
        revision = self.document.revision
        svg = document_sheet_to_svg(self.document, (0, 0, 200, 100))
        root = ET.fromstring(svg)
        self.assertEqual(root.attrib["width"], "200mm")
        self.assertEqual(root.attrib["height"], "100mm")
        boundary = root.find(
            ".//*[@data-woodcam-role='sheet-boundary']",
            {"svg": SVG_NAMESPACE},
        )
        self.assertIsNotNone(boundary)
        ids = {
            element.attrib.get("data-woodcam-entity-id")
            for element in root.iter()
            if element.attrib.get("data-woodcam-entity-id")
        }
        self.assertEqual(ids, {self.inside.id, self.hole.id})
        self.assertEqual(self.document.revision, revision)

    def test_visible_toolpath_can_be_embedded_without_becoming_geometry(self):
        svg = document_sheet_to_svg(
            self.document,
            (0, 0, 200, 100),
            toolpath_components={
                "rapid": (((0, 0), (10, 10)),),
                "cut": (((10, 10), (80, 10)),),
                "entry_points": ((10, 10),),
            },
        )
        root = ET.fromstring(svg)
        toolpath = root.find(".//*[@id='woodcam-toolpath']")
        self.assertIsNotNone(toolpath)
        kinds = {
            element.attrib.get("data-toolpath-kind")
            for element in toolpath.iter()
            if element.attrib.get("data-toolpath-kind")
        }
        self.assertIn("rapid", kinds)
        self.assertIn("cut", kinds)
        self.assertEqual(len(self.document.entities_by_id), 3)


if __name__ == "__main__":
    unittest.main()
