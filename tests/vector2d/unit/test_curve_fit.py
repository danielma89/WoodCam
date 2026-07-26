import math
import unittest

from woodcam_editor.domain import PathEntity, Vec2, VectorDocument
from woodcam_editor.geometry.curve_fit import fit_polyline_to_arc


class CurveFitTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()

    def test_closed_polyline_circle_becomes_exact_circle(self):
        points = tuple(
            Vec2(20 + 10 * math.cos(index * math.tau / 16), 30 + 10 * math.sin(index * math.tau / 16))
            for index in range(16)
        )
        source = PathEntity.from_points(self.document.active_layer_id, points, closed=True, id="ring")
        fitted = fit_polyline_to_arc(source, 0.01)
        self.assertEqual(type(fitted).__name__, "CircleEntity")
        self.assertEqual(fitted.id, "ring")
        self.assertAlmostEqual(fitted.center.x, 20.0, places=6)
        self.assertAlmostEqual(fitted.center.y, 30.0, places=6)
        self.assertAlmostEqual(fitted.radius, 10.0, places=6)

    def test_open_polyline_arc_becomes_one_exact_arc_span(self):
        points = tuple(
            Vec2(10 * math.cos(index * math.pi / 8), 10 * math.sin(index * math.pi / 8))
            for index in range(5)
        )
        source = PathEntity.from_points(self.document.active_layer_id, points, id="arc")
        fitted = fit_polyline_to_arc(source, 0.01)
        self.assertEqual(len(fitted.spans), 1)
        self.assertEqual(type(fitted.spans[0]).__name__, "ArcSpan")
        self.assertAlmostEqual(fitted.spans[0].sweep_angle, math.pi / 2.0, places=6)

    def test_rectangle_is_preserved_when_not_a_circular_fit(self):
        source = PathEntity.from_points(
            self.document.active_layer_id,
            (Vec2(0, 0), Vec2(40, 0), Vec2(40, 20), Vec2(0, 20)),
            closed=True,
        )
        self.assertIsNone(fit_polyline_to_arc(source, 0.1))


if __name__ == "__main__":
    unittest.main()
