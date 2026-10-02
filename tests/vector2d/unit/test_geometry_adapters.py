from dataclasses import dataclass, field
import unittest

from woodcam_editor.adapters.panelnest import document_to_panel_parts
from woodcam_editor.adapters.woodcam_geometry import (
    GeometryAdapterError,
    document_to_woodcam_geometry,
)


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class LineSpan:
    start: Point
    end: Point

    def flatten(self, deflection=0.01):
        return (self.start, self.end)


@dataclass(frozen=True)
class PathEntity:
    id: str
    layer_id: str
    spans: tuple
    closed: bool
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CircleEntity:
    id: str
    layer_id: str
    center: Point
    radius: float


@dataclass(frozen=True)
class GroupEntity:
    id: str
    layer_id: str
    child_ids: tuple
    type: str = "group"


@dataclass(frozen=True)
class Layer:
    id: str
    visible: bool = True
    purpose: str = "design"


@dataclass(frozen=True)
class Piece:
    id: str
    outer_path_id: str
    inner_path_ids: tuple
    name: str = "Lateral"
    quantity: int = 2
    material: str = "MDF"
    thickness: float = 15.0
    placement: object = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Translation:
    dx: float
    dy: float

    def apply_to_point(self, point):
        return Point(point.x + self.dx, point.y + self.dy)


@dataclass
class Document:
    entities_by_id: dict
    layers_by_id: dict
    pieces_by_id: dict = field(default_factory=dict)
    document_uuid: str = "document-1"
    revision: int = 3


def rectangle(identifier="outer", layer_id="design"):
    points = (Point(0, 0), Point(100, 0), Point(100, 50), Point(0, 50))
    spans = tuple(
        LineSpan(points[index], points[(index + 1) % len(points)])
        for index in range(len(points))
    )
    return PathEntity(identifier, layer_id, spans, True)


def regular_polygon(identifier, center, radius, sides=16, layer_id="design"):
    import math

    points = tuple(
        Point(
            center.x + radius * math.cos(2.0 * math.pi * index / sides),
            center.y + radius * math.sin(2.0 * math.pi * index / sides),
        )
        for index in range(sides)
    )
    spans = tuple(
        LineSpan(points[index], points[(index + 1) % len(points)])
        for index in range(len(points))
    )
    return PathEntity(identifier, layer_id, spans, True)


class GeometryAdapterTests(unittest.TestCase):
    def test_whole_document_ignores_open_piece_marking(self):
        outer = rectangle()
        marking = PathEntity(
            "marking",
            "design",
            (LineSpan(Point(10, 20), Point(30, 20)),),
            False,
            {"woodcam_role": "piece_marking"},
        )
        document = Document(
            {outer.id: outer, marking.id: marking},
            {"design": Layer("design")},
        )

        geometry = document_to_woodcam_geometry(document)

        self.assertEqual(len(geometry["contours"]), 1)
        with self.assertRaises(GeometryAdapterError):
            document_to_woodcam_geometry(
                document,
                entity_ids=(marking.id,),
            )

    def test_selected_imported_polyline_circle_is_a_drill_hole(self):
        imported_hole = regular_polygon(
            "selected-hole-polyline",
            Point(20, 25),
            5,
        )
        document = Document(
            {imported_hole.id: imported_hole},
            {"design": Layer("design")},
        )

        geometry = document_to_woodcam_geometry(
            document,
            entity_ids=(imported_hole.id,),
        )

        self.assertEqual(len(geometry["holes"]), 1)
        self.assertEqual(geometry["contours"], [])
        self.assertAlmostEqual(geometry["holes"][0]["diameter_mm"], 10.0, places=3)

    def test_piece_outer_circle_is_never_promoted_to_drill_hole(self):
        dogbone_like_outer = regular_polygon(
            "dogbone-like-piece-outer",
            Point(20, 25),
            1.5875,
        )
        piece = Piece(
            "round-external-feature",
            dogbone_like_outer.id,
            (),
        )
        document = Document(
            {dogbone_like_outer.id: dogbone_like_outer},
            {"design": Layer("design")},
            {piece.id: piece},
        )

        geometry = document_to_woodcam_geometry(
            document,
            entity_ids=(dogbone_like_outer.id,),
        )

        self.assertEqual(geometry["holes"], [])
        self.assertEqual(len(geometry["contours"]), 1)

    def test_remnant_cut_is_reserved_for_the_open_centerline_bridge(self):
        remnant = PathEntity(
            "remnant",
            "design",
            (LineSpan(Point(0, 40), Point(100, 40)),),
            False,
            {"woodcam_role": "remnant_cut"},
        )
        document = Document(
            {remnant.id: remnant},
            {"design": Layer("design")},
        )

        self.assertEqual(
            document_to_woodcam_geometry(document),
            {"contours": [], "holes": []},
        )

    def test_whole_document_ignores_group_relationship_and_uses_leaf_geometry(self):
        outer = rectangle()
        group = GroupEntity("group-board", "design", (outer.id,))
        document = Document(
            {outer.id: outer, group.id: group},
            {"design": Layer("design")},
        )

        geometry = document_to_woodcam_geometry(document)

        self.assertEqual(len(geometry["contours"]), 1)
        self.assertEqual(geometry["contours"][0][0], (0.0, 0.0))

    def test_pocket_region_is_sent_only_when_explicitly_selected(self):
        outer = rectangle()
        pocket = PathEntity(
            "pocket",
            "design",
            (
                LineSpan(Point(0, 10), Point(40, 10)),
                LineSpan(Point(40, 10), Point(40, 30)),
                LineSpan(Point(40, 30), Point(0, 30)),
                LineSpan(Point(0, 30), Point(0, 10)),
            ),
            True,
            {
                "import_role": "pocket_region",
                "pocket_depth_mm": 5.0,
            },
        )
        document = Document(
            {outer.id: outer, pocket.id: pocket},
            {"design": Layer("design")},
        )

        complete = document_to_woodcam_geometry(document)
        selected = document_to_woodcam_geometry(
            document,
            entity_ids=(pocket.id,),
        )

        self.assertEqual(len(complete["contours"]), 1)
        self.assertEqual(complete["contours"][0][0], (0.0, 0.0))
        self.assertEqual(len(selected["contours"]), 1)
        self.assertEqual(selected["contours"][0][0], (0.0, 10.0))

    def test_cam_contract_preserves_path_and_exact_circle_hole(self):
        outer = rectangle()
        hole = CircleEntity("hole", "design", Point(20, 25), 5)
        document = Document(
            {outer.id: outer, hole.id: hole},
            {"design": Layer("design")},
        )

        geometry = document_to_woodcam_geometry(document, deflection=0.1)

        self.assertEqual(
            geometry["contours"],
            [[(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)]],
        )
        self.assertEqual(len(geometry["holes"]), 1)
        self.assertEqual(geometry["holes"][0]["x"], 20.0)
        self.assertEqual(geometry["holes"][0]["y"], 25.0)
        self.assertEqual(geometry["holes"][0]["diameter_mm"], 10.0)
        self.assertGreaterEqual(len(geometry["holes"][0]["points"]), 12)

    def test_selected_circle_can_be_preserved_as_pocket_contour(self):
        circle = CircleEntity("pocket-circle", "design", Point(20, 25), 5)
        document = Document(
            {circle.id: circle},
            {"design": Layer("design")},
        )

        geometry = document_to_woodcam_geometry(
            document,
            entity_ids=(circle.id,),
            deflection=0.1,
            classify_small_circles_as_holes=False,
        )

        self.assertEqual(geometry["holes"], [])
        self.assertEqual(len(geometry["contours"]), 1)
        self.assertGreaterEqual(len(geometry["contours"][0]), 12)

    def test_open_design_path_blocks_cam_in_strict_mode(self):
        path = PathEntity(
            "open",
            "design",
            (LineSpan(Point(0, 0), Point(10, 0)),),
            False,
        )
        document = Document({path.id: path}, {"design": Layer("design")})

        with self.assertRaisesRegex(GeometryAdapterError, "está aberto"):
            document_to_woodcam_geometry(document)

    def test_hidden_and_reference_layers_are_not_sent_to_cam(self):
        visible = rectangle("visible", "design")
        hidden = rectangle("hidden", "hidden")
        reference = rectangle("reference", "reference")
        document = Document(
            {item.id: item for item in (visible, hidden, reference)},
            {
                "design": Layer("design"),
                "hidden": Layer("hidden", visible=False),
                "reference": Layer("reference", purpose="reference"),
            },
        )

        geometry = document_to_woodcam_geometry(document)

        self.assertEqual(len(geometry["contours"]), 1)

    def test_panelnest_piece_keeps_hole_attached_and_never_promotes_it_to_piece(self):
        outer = rectangle()
        hole = CircleEntity("hole", "design", Point(20, 25), 5)
        piece = Piece("piece-1", outer.id, (hole.id,))
        document = Document(
            {outer.id: outer, hole.id: hole},
            {"design": Layer("design")},
            {piece.id: piece},
        )

        parts = document_to_panel_parts(document, deflection=0.1)

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].id, "piece-1")
        self.assertEqual(parts[0].quantity, 2)
        self.assertEqual(len(parts[0].circular_holes), 1)
        self.assertEqual(parts[0].inner_profile_loops, ())

    def test_panelnest_recovers_small_round_hole_imported_as_polyline(self):
        """A tessellated imported circle must not disappear in PanelNest."""
        outer = rectangle()
        imported_hole = regular_polygon("hole-polyline", Point(20, 25), 5)
        piece = Piece("piece-1", outer.id, (imported_hole.id,))
        document = Document(
            {outer.id: outer, imported_hole.id: imported_hole},
            {"design": Layer("design")},
            {piece.id: piece},
        )

        parts = document_to_panel_parts(document, deflection=0.1)

        self.assertEqual(len(parts), 1)
        self.assertEqual(len(parts[0].circular_holes), 1)
        self.assertAlmostEqual(parts[0].circular_holes[0]["x"], 20.0, places=6)
        self.assertAlmostEqual(parts[0].circular_holes[0]["y"], 25.0, places=6)
        self.assertAlmostEqual(parts[0].circular_holes[0]["diameter_mm"], 10.0, places=3)
        self.assertEqual(parts[0].inner_profile_loops, ())

    def test_piece_placement_is_applied_to_outer_and_hole_as_one_unit(self):
        outer = rectangle()
        hole = CircleEntity("hole", "design", Point(20, 25), 5)
        piece = Piece(
            "piece-1",
            outer.id,
            (hole.id,),
            placement=Translation(1000, 2000),
        )
        document = Document(
            {outer.id: outer, hole.id: hole},
            {"design": Layer("design")},
            {piece.id: piece},
        )

        geometry = document_to_woodcam_geometry(document, piece_ids=[piece.id])

        self.assertEqual(geometry["contours"][0][0], (1000.0, 2000.0))
        self.assertEqual(geometry["holes"][0]["x"], 1020.0)
        self.assertEqual(geometry["holes"][0]["y"], 2025.0)


if __name__ == "__main__":
    unittest.main()
