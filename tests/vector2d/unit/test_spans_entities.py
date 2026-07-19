import math
import unittest

from woodcam_editor.domain import (
    Affine2D,
    ArcSpan,
    CircleEntity,
    CubicBezierSpan,
    EllipseEntity,
    GeometryError,
    InvariantError,
    LineSpan,
    PathEntity,
    Vec2,
)


class LineSpanTests(unittest.TestCase):
    def test_line_contract(self):
        line = LineSpan(Vec2(0, 0), Vec2(10, 0), id="span-line")
        self.assertEqual(line.point_at(0.25), Vec2(2.5, 0))
        self.assertEqual(line.tangent_at(0.5), Vec2(1, 0))
        self.assertAlmostEqual(line.length(), 10)
        nearest = line.nearest_point(Vec2(4, 3))
        self.assertEqual(nearest.point, Vec2(4, 0))
        self.assertAlmostEqual(nearest.parameter, 0.4)
        self.assertAlmostEqual(nearest.distance, 3)

    def test_split_reverse_transform_preserve_first_id(self):
        line = LineSpan(Vec2(0, 0), Vec2(10, 0), id="stable")
        left, right = line.split(0.3)
        self.assertEqual(left.id, "stable")
        self.assertEqual(left.end, right.start)
        self.assertEqual(line.reversed().id, "stable")
        moved = line.transformed(Affine2D.translation(Vec2(5, 2)))
        self.assertEqual((moved.start, moved.end), (Vec2(5, 2), Vec2(15, 2)))

    def test_zero_line_rejected(self):
        with self.assertRaises(GeometryError):
            LineSpan(Vec2(1, 1), Vec2(1, 1))


class ArcSpanTests(unittest.TestCase):
    def setUp(self):
        self.arc = ArcSpan(Vec2(10, 0), Vec2(0, 10), Vec2(0, 0), False, id="arc")

    def test_quarter_arc_geometry(self):
        self.assertAlmostEqual(self.arc.radius, 10)
        self.assertAlmostEqual(self.arc.length(), 5 * math.pi)
        self.assertTrue(self.arc.point_at(0.5).almost_equals(Vec2(math.sqrt(50), math.sqrt(50)), 1e-10))
        self.assertTrue(self.arc.tangent_at(0).almost_equals(Vec2(0, 1)))
        self.assertEqual(self.arc.bounds().min_x, 0)
        self.assertEqual(self.arc.bounds().max_y, 10)

    def test_arc_nearest_split_reverse_and_flatten(self):
        nearest = self.arc.nearest_point(Vec2(8, 8))
        self.assertAlmostEqual(nearest.parameter, 0.5, places=6)
        left, right = self.arc.split(0.5)
        self.assertEqual(left.end, right.start)
        self.assertAlmostEqual(left.length() + right.length(), self.arc.length())
        reversed_arc = self.arc.reversed()
        self.assertTrue(reversed_arc.clockwise)
        self.assertEqual(reversed_arc.id, self.arc.id)
        points = self.arc.flatten(0.05)
        self.assertEqual(points[0], self.arc.start)
        self.assertTrue(points[-1].almost_equals(self.arc.end))
        self.assertGreater(len(points), 2)

    def test_arc_transform_rules(self):
        reflected = self.arc.transformed(Affine2D.mirror_x())
        self.assertNotEqual(reflected.clockwise, self.arc.clockwise)
        with self.assertRaises(GeometryError):
            self.arc.transformed(Affine2D.scaling(2, 3))
        with self.assertRaises(GeometryError):
            ArcSpan(Vec2(1, 0), Vec2(2, 0), Vec2(0, 0))

    def test_moving_arc_endpoint_preserves_signed_sweep(self):
        moved = self.arc.with_end(Vec2(0, 20))
        self.assertAlmostEqual(moved.sweep_angle, self.arc.sweep_angle, places=10)
        self.assertEqual(moved.start, self.arc.start)
        self.assertEqual(moved.end, Vec2(0, 20))


class BezierSpanTests(unittest.TestCase):
    def setUp(self):
        self.curve = CubicBezierSpan(
            Vec2(0, 0), Vec2(0, 10), Vec2(10, 10), Vec2(10, 0), id="bezier"
        )

    def test_point_tangent_bounds_and_length(self):
        self.assertEqual(self.curve.point_at(0), Vec2(0, 0))
        self.assertEqual(self.curve.point_at(1), Vec2(10, 0))
        self.assertTrue(self.curve.point_at(0.5).almost_equals(Vec2(5, 7.5)))
        self.assertTrue(self.curve.tangent_at(0).almost_equals(Vec2(0, 1)))
        self.assertAlmostEqual(self.curve.bounds().max_y, 7.5)
        self.assertGreater(self.curve.length(), 10)

    def test_split_reverse_nearest_and_flatten(self):
        left, right = self.curve.split(0.5)
        self.assertEqual(left.end, right.start)
        self.assertEqual(left.id, self.curve.id)
        self.assertTrue(self.curve.reversed().point_at(0.25).almost_equals(self.curve.point_at(0.75)))
        nearest = self.curve.nearest_point(Vec2(5, 8))
        self.assertAlmostEqual(nearest.parameter, 0.5, places=3)
        flattened = self.curve.flatten(0.1)
        self.assertEqual(flattened[0], self.curve.start)
        self.assertEqual(flattened[-1], self.curve.end)
        self.assertGreater(len(flattened), 4)


class EntityTests(unittest.TestCase):
    def test_open_path_nodes_and_move_node(self):
        first = LineSpan(Vec2(0, 0), Vec2(10, 0))
        second = LineSpan(Vec2(10, 0), Vec2(20, 5))
        path = PathEntity("layer", (first, second), node_ids=("n0", "n1", "n2"))
        self.assertEqual([node.point for node in path.nodes()], [Vec2(0, 0), Vec2(10, 0), Vec2(20, 5)])
        moved = path.with_node_moved("n1", Vec2(12, 3))
        self.assertEqual(moved.spans[0].end, Vec2(12, 3))
        self.assertEqual(moved.spans[1].start, Vec2(12, 3))
        self.assertEqual(path.spans[0].end, Vec2(10, 0))

    def test_closed_path_area_reverse_and_split(self):
        path = PathEntity.from_points(
            "layer", (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)), closed=True
        )
        self.assertAlmostEqual(path.signed_area(), 100)
        self.assertAlmostEqual(path.reversed().signed_area(), -100)
        split = path.with_span_replaced(path.spans[0].id, path.spans[0].split(0.5))
        self.assertEqual(len(split.spans), 5)
        self.assertEqual(len(split.node_ids), 5)
        self.assertAlmostEqual(split.signed_area(), 100)

    def test_path_invariants_reject_gap_and_wrong_nodes(self):
        with self.assertRaises(InvariantError):
            PathEntity(
                "layer",
                (LineSpan(Vec2(0, 0), Vec2(1, 0)), LineSpan(Vec2(2, 0), Vec2(3, 0))),
            )
        with self.assertRaises(InvariantError):
            PathEntity("layer", (LineSpan(Vec2(0, 0), Vec2(1, 0)),), node_ids=("one",))

    def test_circle_nodes_transform_flatten(self):
        circle = CircleEntity("layer", Vec2(2, 3), 5, id="circle")
        self.assertAlmostEqual(circle.area(), 25 * math.pi)
        self.assertEqual(circle.bounds().min_x, -3)
        moved = circle.with_node_moved(circle.center_node_id, Vec2(10, 10))
        self.assertEqual(moved.center, Vec2(10, 10))
        resized = circle.with_node_moved(circle.radius_node_id, Vec2(12, 3))
        self.assertAlmostEqual(resized.radius, 10)
        scaled = circle.transformed(Affine2D.scaling(2))
        self.assertAlmostEqual(scaled.radius, 10)
        self.assertTrue(circle.flatten(0.1)[0].almost_equals(circle.flatten(0.1)[-1]))

    def test_ellipse_exact_bounds(self):
        ellipse = EllipseEntity("layer", Vec2(0, 0), 10, 5, rotation=math.pi / 2)
        self.assertAlmostEqual(ellipse.bounds().width, 10, places=8)
        self.assertAlmostEqual(ellipse.bounds().height, 20, places=8)
        with self.assertRaises(GeometryError):
            ellipse.transformed(Affine2D.scaling(2, 3))


if __name__ == "__main__":
    unittest.main()
