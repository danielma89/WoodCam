import unittest

from woodcam_editor.domain.entities import CircleEntity, PathEntity
from woodcam_editor.importers.sketch import import_sketch


class Point3:
    def __init__(self, x, y, z=0.0):
        self.x = x
        self.y = y
        self.z = z


class LineSegment:
    TypeId = "Part::GeomLineSegment"

    def __init__(self, start, end):
        self.StartPoint = start
        self.EndPoint = end


class Circle:
    TypeId = "Part::GeomCircle"

    def __init__(self, center, radius):
        self.Center = center
        self.Radius = radius


class UnsupportedSpline:
    TypeId = "Part::GeomBSplineCurve"


class FakeSketch:
    TypeId = "Sketcher::SketchObject"
    Name = "Sketch001"
    Label = "Porta"
    Document = None

    def __init__(self, geometry, construction=()):
        self.Geometry = list(geometry)
        self._construction = set(construction)

    def getConstruction(self, index):
        return index in self._construction

    def getGlobalPlacement(self):
        return None


class SketchImporterTests(unittest.TestCase):
    def test_imports_closed_line_chain_and_exact_circle_without_mutating_source(self):
        points = [Point3(0, 0), Point3(100, 0), Point3(100, 50), Point3(0, 50)]
        geometry = [
            LineSegment(points[index], points[(index + 1) % len(points)])
            for index in range(len(points))
        ]
        geometry.append(Circle(Point3(20, 20), 5))
        sketch = FakeSketch(geometry)
        before = list(sketch.Geometry)

        result = import_sketch(sketch, layer_id="layer-design")

        self.assertEqual(sketch.Geometry, before)
        paths = [entity for entity in result.entities if isinstance(entity, PathEntity)]
        circles = [entity for entity in result.entities if isinstance(entity, CircleEntity)]
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].closed)
        self.assertEqual(len(paths[0].spans), 4)
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].radius, 5.0)
        self.assertFalse(result.issues)

    def test_skips_construction_and_reports_unsupported_without_silent_conversion(self):
        sketch = FakeSketch(
            [
                LineSegment(Point3(0, 0), Point3(10, 0)),
                LineSegment(Point3(0, 2), Point3(10, 2)),
                UnsupportedSpline(),
            ],
            construction={1},
        )

        result = import_sketch(sketch, layer_id="layer-design")

        self.assertEqual(len(result.entities), 1)
        self.assertIsInstance(result.entities[0], PathEntity)
        messages = "\n".join(issue.message for issue in result.issues)
        self.assertIn("construção ignorada", messages)
        self.assertIn("B-spline", messages)


if __name__ == "__main__":
    unittest.main()

