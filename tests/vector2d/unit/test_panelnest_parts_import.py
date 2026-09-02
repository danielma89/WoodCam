import unittest

from woodcam_editor.application.piece_organizer import classify_document_pieces
from woodcam_editor.domain import (
    ArcSpan,
    CircleEntity,
    PathEntity,
    Vec2,
    VectorDocument,
)
from woodcam_editor.importers.panelnest_parts import import_panelnest_parts


class _Part:
    def __init__(self):
        self.part_id = "PN-001"
        self.label = "Lateral"
        self.length_mm = 100.0
        self.width_mm = 50.0
        self.thickness_mm = 15.0
        self.quantity = 2
        self.profile_points = [(0, 0), (100, 0), (100, 50), (0, 50)]
        self.inner_profile_loops = [[(40, 15), (60, 15), (60, 35), (40, 35)]]
        self.holes = [{"x_mm": 20, "y_mm": 25, "diameter_mm": 5}]


class PanelNestPartsImportTests(unittest.TestCase):
    def test_transposed_hole_is_reconciled_inside_panel_profile(self):
        part = _Part()
        part.quantity = 1
        # Payload from an older equal-axis convention: (20, 80) is outside a
        # 100 x 50 panel, while its unambiguous transpose (80, 20) is valid.
        part.holes = [{"x_mm": 20, "y_mm": 80, "diameter_mm": 5}]

        result = import_panelnest_parts((part,), layer_id="layer")

        circles = [
            entity
            for entity in result.entities
            if isinstance(entity, CircleEntity)
        ]
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].center, Vec2(80, 20))

    def test_local_hole_coordinates_follow_nonzero_profile_and_detached_are_dropped(self):
        part = _Part()
        part.quantity = 1
        part.profile_points = [
            (100, 200), (200, 200), (200, 250), (100, 250),
        ]
        part.holes = [
            {"x_mm": 20, "y_mm": 25, "diameter_mm": 5},
            {"x_mm": 300, "y_mm": 300, "diameter_mm": 5},
        ]

        result = import_panelnest_parts((part,), layer_id="layer")

        circles = [
            entity
            for entity in result.entities
            if isinstance(entity, CircleEntity)
        ]
        self.assertEqual(len(circles), 1)
        # Staging normalizes the nonzero source profile back to the origin.
        self.assertEqual(circles[0].center, Vec2(20, 25))
        self.assertTrue(
            any("fora do perfil" in issue.message for issue in result.issues)
        )

    def test_hole_inside_existing_internal_cutout_is_not_imported_as_loose_ring(self):
        part = _Part()
        part.quantity = 1
        part.holes = [
            {"x_mm": 20, "y_mm": 25, "diameter_mm": 5},
            {"x_mm": 50, "y_mm": 25, "diameter_mm": 5},
        ]

        result = import_panelnest_parts((part,), layer_id="layer")

        circles = [
            entity
            for entity in result.entities
            if isinstance(entity, CircleEntity)
        ]
        self.assertEqual([entity.center for entity in circles], [Vec2(20, 25)])
        self.assertTrue(
            any("fora do perfil" in issue.message for issue in result.issues)
        )

    def test_exact_source_keeps_matching_piece_and_discards_detached_component(self):
        part = _Part()
        part.object_name = "SourcePanel"
        part.quantity = 1
        exact_outer = PathEntity.from_points(
            "layer",
            (Vec2(0, 0), Vec2(100, 0), Vec2(100, 50), Vec2(0, 50)),
            closed=True,
        )
        exact_hole = CircleEntity("layer", Vec2(20, 25), 2.5)
        detached = CircleEntity("layer", Vec2(160, 25), 5.0)

        result = import_panelnest_parts(
            (part,),
            layer_id="layer",
            exact_entities_by_source={
                "SourcePanel": (exact_outer, exact_hole, detached),
            },
        )

        leaves = [
            entity
            for entity in result.entities
            if type(entity).__name__ != "GroupEntity"
        ]
        self.assertEqual(len(leaves), 2)
        self.assertEqual(
            [entity.center for entity in leaves if isinstance(entity, CircleEntity)],
            [Vec2(20, 25)],
        )
        self.assertTrue(
            any("vetor(es) solto(s)" in issue.message for issue in result.issues)
        )

    def test_stages_panelnest_parts_without_calling_layout_or_nesting(self):
        result = import_panelnest_parts((_Part(),), layer_id="layer", staging_gap=10)

        paths = [item for item in result.entities if type(item).__name__ == "PathEntity"]
        circles = [item for item in result.entities if type(item).__name__ == "CircleEntity"]
        self.assertEqual(len(paths), 4)
        self.assertEqual(len(circles), 2)
        self.assertTrue(all(path.closed for path in paths))
        self.assertEqual(paths[0].bounds().width, 100.0)
        self.assertEqual(paths[1].bounds().width, 20.0)
        # The staging area is a compact shelf/grid, not one very long line and
        # not a PanelNest layout calculation.
        self.assertEqual(paths[2].bounds().min_x, 0.0)
        self.assertEqual(paths[2].bounds().min_y, 60.0)
        self.assertTrue(result.source_metadata["nesting_executed"] is False)

    def test_keeps_inner_recut_with_its_own_panel_instance(self):
        document = VectorDocument.create_default()
        result = import_panelnest_parts((_Part(),), layer_id=document.active_layer_id)
        document.add_entities(result.entities)

        classification = classify_document_pieces(document)

        self.assertEqual(len(classification.pieces), 2)
        self.assertTrue(all(len(piece.inner_ids) == 2 for piece in classification.pieces))

    def test_source_profile_can_replace_transposed_panelnest_holes(self):
        part = _Part()
        part.object_name = "Dogbone"
        part.quantity = 1
        part.profile_points = []
        part.holes = [
            {"x_mm": 4.0, "y_mm": 4.0, "diameter_mm": 6.0},
            {"x_mm": 16.0, "y_mm": 4.0, "diameter_mm": 6.0},
        ]
        dogbone = [
            (7.0, 2.0), (5.0, 1.0), (3.0, 2.0), (2.0, 5.0),
            (3.0, 7.0), (7.0, 8.0), (7.0, 12.0), (3.0, 13.0),
            (2.0, 15.0), (3.0, 18.0), (7.0, 19.0), (13.0, 19.0),
            (17.0, 18.0), (18.0, 15.0), (17.0, 13.0), (13.0, 12.0),
            (13.0, 8.0), (17.0, 7.0), (18.0, 5.0), (17.0, 2.0),
            (13.0, 1.0),
        ]

        result = import_panelnest_parts(
            (part,),
            layer_id="layer",
            source_profiles_by_source={
                "Dogbone": {"inner_loops": (dogbone,), "holes": ()},
            },
        )

        self.assertEqual(
            [type(item).__name__ for item in result.entities],
            ["PathEntity", "PathEntity"],
        )

    def test_exact_source_wires_override_even_an_explicit_panelnest_profile(self):
        part = _Part()
        part.object_name = "SourcePanel"
        part.quantity = 2
        # Deliberately rectangular public payload.  The source has a curved
        # recut, which must remain an ArcSpan for both occurrences.
        exact_outer = PathEntity.from_points(
            "layer",
            (Vec2(0, 0), Vec2(40, 0), Vec2(40, 20), Vec2(0, 20)),
            closed=True,
        )
        exact_inner = PathEntity(
            "layer",
            (
                ArcSpan(Vec2(16, 10), Vec2(24, 10), Vec2(20, 10), clockwise=False),
                ArcSpan(Vec2(24, 10), Vec2(16, 10), Vec2(20, 10), clockwise=False),
            ),
            closed=True,
        )

        result = import_panelnest_parts(
            (part,),
            layer_id="layer",
            exact_entities_by_source={"SourcePanel": (exact_outer, exact_inner)},
        )

        self.assertEqual(len(result.entities), 6)
        compounds = [
            entity for entity in result.entities if type(entity).__name__ == "GroupEntity"
        ]
        self.assertEqual(len(compounds), 2)
        self.assertTrue(all(len(entity.child_ids) == 2 for entity in compounds))
        self.assertEqual(
            sum(
                isinstance(span, ArcSpan)
                for entity in result.entities
                for span in getattr(entity, "spans", ())
            ),
            4,
        )
