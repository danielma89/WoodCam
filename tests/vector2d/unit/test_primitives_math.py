import math
import unittest

from woodcam_editor.domain import Affine2D, BBox2D, GeometryError, Vec2
from woodcam_editor.geometry.math2d import (
    PointLocation,
    closest_point_on_segment,
    point_in_polygon,
    polygon_centroid,
    segment_intersections,
    signed_area,
)


class Vec2Tests(unittest.TestCase):
    def test_vector_arithmetic_and_products(self):
        first = Vec2(3, 4)
        second = Vec2(-2, 5)
        self.assertEqual(first + second, Vec2(1, 9))
        self.assertEqual(first - second, Vec2(5, -1))
        self.assertEqual(first * 2, Vec2(6, 8))
        self.assertEqual(2 * first, Vec2(6, 8))
        self.assertAlmostEqual(first.dot(second), 14)
        self.assertAlmostEqual(first.cross(second), 23)
        self.assertAlmostEqual(first.length(), 5)

    def test_normalize_and_lerp(self):
        self.assertTrue(Vec2(3, 4).normalized().almost_equals(Vec2(0.6, 0.8)))
        self.assertEqual(Vec2(0, 0).lerp(Vec2(10, 20), 0.25), Vec2(2.5, 5))
        with self.assertRaises(GeometryError):
            Vec2(0, 0).normalized()

    def test_finite_coordinates_are_required(self):
        with self.assertRaises(GeometryError):
            Vec2(float("nan"), 0)
        with self.assertRaises(GeometryError):
            Vec2(0, float("inf"))


class BoundsAndTransformTests(unittest.TestCase):
    def test_bounds_union_contains_and_intersects(self):
        first = BBox2D.from_points((Vec2(-1, 2), Vec2(3, 7)))
        second = BBox2D(2, 6, 5, 9)
        self.assertEqual(first.width, 4)
        self.assertEqual(first.height, 5)
        self.assertEqual(first.union(second), BBox2D(-1, 2, 5, 9))
        self.assertTrue(first.contains_point(Vec2(0, 4)))
        self.assertTrue(first.intersects(second))
        self.assertFalse(first.contains_bbox(second))

    def test_affine_composition_inverse_and_rotation_origin(self):
        point = Vec2(2, 1)
        transform = Affine2D.translation(Vec2(5, -3)) @ Affine2D.rotation(math.pi / 2)
        moved = transform.apply_to_point(point)
        self.assertTrue(moved.almost_equals(Vec2(4, -1), 1e-12))
        self.assertTrue(transform.inverse().apply_to_point(moved).almost_equals(point, 1e-12))
        around = Affine2D.rotation(math.pi, origin=Vec2(1, 1))
        self.assertTrue(around.apply_to_point(Vec2(2, 1)).almost_equals(Vec2(0, 1), 1e-12))

    def test_similarity_detection_and_singular_inverse(self):
        self.assertTrue(Affine2D.rotation(0.3).is_similarity())
        self.assertTrue(Affine2D.scaling(-2).is_similarity())
        self.assertFalse(Affine2D.scaling(2, 3).is_similarity())
        with self.assertRaises(GeometryError):
            Affine2D.scaling(0).inverse()


class Math2DTests(unittest.TestCase):
    def test_closest_point_on_segment(self):
        point, parameter = closest_point_on_segment(Vec2(4, 3), Vec2(0, 0), Vec2(10, 0))
        self.assertEqual(point, Vec2(4, 0))
        self.assertAlmostEqual(parameter, 0.4)

    def test_crossing_and_overlap_intersections(self):
        crossing = segment_intersections(Vec2(0, 0), Vec2(10, 10), Vec2(0, 10), Vec2(10, 0))
        self.assertEqual(len(crossing), 1)
        self.assertTrue(crossing[0].point.almost_equals(Vec2(5, 5)))
        overlap = segment_intersections(Vec2(0, 0), Vec2(10, 0), Vec2(4, 0), Vec2(12, 0))
        self.assertEqual([item.point for item in overlap], [Vec2(4, 0), Vec2(10, 0)])
        self.assertTrue(all(item.kind == "overlap" for item in overlap))

    def test_polygon_area_centroid_and_point_location(self):
        square = (Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10))
        self.assertAlmostEqual(signed_area(square), 100)
        self.assertEqual(polygon_centroid(square), Vec2(5, 5))
        self.assertEqual(point_in_polygon(Vec2(5, 5), square), PointLocation.INSIDE)
        self.assertEqual(point_in_polygon(Vec2(0, 5), square), PointLocation.BOUNDARY)
        self.assertEqual(point_in_polygon(Vec2(20, 5), square), PointLocation.OUTSIDE)


if __name__ == "__main__":
    unittest.main()

