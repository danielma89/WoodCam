import unittest
from unittest import mock

import woodcam_editor.application.piece_organizer as piece_organizer
from woodcam_editor.application.piece_organizer import (
    classify_document_pieces,
    organize_pieces,
)


class _Path:
    def __init__(self, entity_id, points, closed=True, metadata=None):
        self.id = entity_id
        self.points = points
        self.closed = closed
        self.metadata = dict(metadata or {})


class _Document:
    def __init__(self, entities):
        self.entities_by_id = {entity.id: entity for entity in entities}


class PieceOrganizerTest(unittest.TestCase):
    def test_holes_remain_attached_to_their_piece(self):
        outer = _Path("outer", [(0, 0), (100, 0), (100, 50), (0, 50)])
        hole_a = _Path("hole-a", [(10, 10), (20, 10), (20, 20), (10, 20)])
        hole_b = _Path("hole-b", [(70, 10), (80, 10), (80, 20), (70, 20)])
        result = classify_document_pieces(_Document([outer, hole_a, hole_b]))
        self.assertEqual(len(result.pieces), 1)
        self.assertEqual(set(result.pieces[0].inner_ids), {"hole-a", "hole-b"})
        organized = organize_pieces(result.pieces, (0, 0, 500, 500), spacing=5)
        self.assertEqual(len(organized.placements), 1)
        self.assertEqual(
            set(organized.placements[0].entity_ids),
            {"outer", "hole-a", "hole-b"},
        )

    def test_open_vector_does_not_become_a_piece(self):
        open_path = _Path("open", [(0, 0), (10, 0)], closed=False)
        result = classify_document_pieces(_Document([open_path]))
        self.assertEqual(result.open_entity_ids, ["open"])
        self.assertEqual(result.pieces, [])

    def test_remnant_cut_is_not_a_piece_or_an_open_vector_blocker(self):
        remnant = _Path(
            "remnant",
            [(0, 100), (200, 100)],
            closed=False,
            metadata={"woodcam_role": "remnant_cut"},
        )

        result = classify_document_pieces(_Document([remnant]))

        self.assertEqual(result.open_entity_ids, [])
        self.assertEqual(result.pieces, [])

    def test_open_markings_inside_one_piece_are_owned_and_do_not_block_nesting(self):
        outer = _Path("outer", [(0, 0), (200, 0), (200, 50), (0, 50)])
        marks = (
            _Path("mark-a", [(50, 15), (50, 35)], closed=False),
            _Path("mark-b", [(100, 15), (100, 35)], closed=False),
            _Path("mark-c", [(150, 15), (150, 35)], closed=False),
        )
        outside = _Path("outside", [(250, 10), (250, 30)], closed=False)

        result = classify_document_pieces(_Document((outer, *marks, outside)))

        self.assertEqual(len(result.pieces), 1)
        self.assertEqual(
            set(result.pieces[0].marking_ids),
            {"mark-a", "mark-b", "mark-c"},
        )
        self.assertEqual(
            set(result.marking_entity_ids),
            {"mark-a", "mark-b", "mark-c"},
        )
        self.assertEqual(result.open_entity_ids, ["outside"])
        organized = organize_pieces(result.pieces, (0, 0, 500, 500), spacing=5)
        self.assertEqual(
            set(organized.placements[0].entity_ids),
            {"outer", "mark-a", "mark-b", "mark-c"},
        )

    def test_open_path_inside_a_cutout_is_not_claimed_as_piece_marking(self):
        outer = _Path("outer", [(0, 0), (100, 0), (100, 100), (0, 100)])
        cutout = _Path("cutout", [(30, 30), (70, 30), (70, 70), (30, 70)])
        open_path = _Path("open-in-cutout", [(40, 50), (60, 50)], closed=False)

        result = classify_document_pieces(_Document((outer, cutout, open_path)))

        self.assertEqual(result.pieces[0].marking_ids, ())
        self.assertEqual(result.marking_entity_ids, [])
        self.assertEqual(result.open_entity_ids, ["open-in-cutout"])

    def test_open_path_crossing_a_concave_boundary_remains_ambiguous(self):
        outer = _Path(
            "concave",
            [(0, 0), (100, 0), (100, 30), (30, 30), (30, 100), (0, 100)],
        )
        crossing = _Path("crossing", [(15, 80), (80, 15)], closed=False)

        result = classify_document_pieces(_Document((outer, crossing)))

        self.assertEqual(result.pieces[0].marking_ids, ())
        self.assertEqual(result.open_entity_ids, ["crossing"])

    def test_open_path_on_overlapping_pieces_is_not_attached_arbitrarily(self):
        first = _Path("first", [(0, 0), (100, 0), (100, 50), (0, 50)])
        second = _Path("second", [(50, 0), (150, 0), (150, 50), (50, 50)])
        ambiguous = _Path("ambiguous", [(60, 25), (90, 25)], closed=False)

        result = classify_document_pieces(_Document((first, second, ambiguous)))

        self.assertEqual(len(result.pieces), 2)
        self.assertTrue(all(not piece.marking_ids for piece in result.pieces))
        self.assertEqual(result.open_entity_ids, ["ambiguous"])

    def test_reports_piece_that_does_not_fit(self):
        outer = _Path("large", [(0, 0), (300, 0), (300, 200), (0, 200)])
        classified = classify_document_pieces(_Document([outer]))
        result = organize_pieces(classified.pieces, (0, 0, 100, 100), spacing=2)
        self.assertEqual(result.placements, [])
        self.assertEqual(result.unplaced_piece_ids, [classified.pieces[0].piece_id])

    def test_nested_contours_from_different_imports_are_independent_pieces(self):
        outer = _Path(
            "outer", [(0, 0), (100, 0), (100, 100), (0, 100)],
            metadata={"import_batch_id": "first"},
        )
        nested = _Path(
            "nested", [(20, 20), (40, 20), (40, 40), (20, 40)],
            metadata={"import_batch_id": "second"},
        )

        result = classify_document_pieces(_Document([outer, nested]))

        self.assertEqual(len(result.pieces), 2)
        self.assertTrue(all(not piece.inner_ids for piece in result.pieces))

    def test_panelnest_instances_do_not_scan_every_other_panel(self):
        entities = []
        for index in range(120):
            metadata = {
                "import_batch_id": "cabinet",
                "panelnest_instance_id": "panel-%03d" % index,
            }
            x_value = float(index * 200)
            entities.extend(
                (
                    _Path(
                        "outer-%03d" % index,
                        [(x_value, 0), (x_value + 100, 0), (x_value + 100, 50), (x_value, 50)],
                        metadata=metadata,
                    ),
                    _Path(
                        "hole-%03d" % index,
                        [(x_value + 30, 10), (x_value + 70, 10), (x_value + 70, 40), (x_value + 30, 40)],
                        metadata=metadata,
                    ),
                )
            )

        original = piece_organizer._bounds_contains
        with mock.patch.object(
            piece_organizer,
            "_bounds_contains",
            wraps=original,
        ) as contains:
            result = classify_document_pieces(_Document(entities))

        self.assertEqual(len(result.pieces), 120)
        self.assertTrue(all(len(piece.inner_ids) == 1 for piece in result.pieces))
        # Two loops per physical instance: candidate filtering must stay
        # proportional to the instance, not 240 × 240 comparisons.
        self.assertLess(contains.call_count, 1000)

    def test_overflow_continues_on_side_by_side_sheets(self):
        paths = [
            _Path(
                "piece-%d" % index,
                [(0, 0), (80, 0), (80, 80), (0, 80)],
            )
            for index in range(3)
        ]
        classified = classify_document_pieces(_Document(paths))

        result = organize_pieces(classified.pieces, (0, 0, 100, 100), spacing=2)

        self.assertEqual(len(result.placements), 3)
        self.assertEqual(result.unplaced_piece_ids, [])
        self.assertEqual(len(result.sheet_bounds), 3)
        self.assertEqual({item.sheet_index for item in result.placements}, {0, 1, 2})
        self.assertLess(result.sheet_bounds[0][2], result.sheet_bounds[1][0])

    def test_prearranged_blocks_are_preserved_when_overflow_needs_same_sheets(self):
        """Do not turn two deliberate three-part blocks into a 5+1 layout."""

        paths = []
        for index, x_value in enumerate((0, 22, 44, 100, 122, 144)):
            paths.append(
                _Path(
                    "piece-%d" % index,
                    [
                        (x_value, 0),
                        (x_value + 20, 0),
                        (x_value + 20, 80),
                        (x_value, 80),
                    ],
                )
            )
        paths.append(
            _Path(
                "piece-0-hole",
                [(5, 10), (15, 10), (15, 20), (5, 20)],
            )
        )
        classified = classify_document_pieces(_Document(paths))
        rotations = {
            piece.piece_id: (0.0,)
            for piece in classified.pieces
        }

        result = organize_pieces(
            classified.pieces,
            (0, 0, 120, 100),
            spacing=2,
            rotations=rotations,
            search_mode="fast",
        )

        self.assertEqual(result.strategy, "arranjo atual preservado")
        self.assertEqual(len(result.sheet_bounds), 2)
        by_sheet = {
            sheet_index: sorted(
                placement.piece_id
                for placement in result.placements
                if placement.sheet_index == sheet_index
            )
            for sheet_index in range(2)
        }
        self.assertEqual(
            [len(by_sheet[0]), len(by_sheet[1])],
            [3, 3],
        )
        first_placement = next(
            placement
            for placement in result.placements
            if placement.piece_id.endswith("piece-0")
        )
        self.assertIn("piece-0-hole", first_placement.entity_ids)
        source_by_id = {piece.piece_id: piece for piece in classified.pieces}
        for sheet_index in range(2):
            placements = sorted(
                (
                    placement
                    for placement in result.placements
                    if placement.sheet_index == sheet_index
                ),
                key=lambda placement: placement.placed_bounds[0],
            )
            source_gaps = [
                source_by_id[right.piece_id].bounds[0]
                - source_by_id[left.piece_id].bounds[0]
                for left, right in zip(placements, placements[1:])
            ]
            placed_gaps = [
                right.placed_bounds[0] - left.placed_bounds[0]
                for left, right in zip(placements, placements[1:])
            ]
            self.assertEqual(source_gaps, placed_gaps)

    def test_valid_manual_layout_wins_when_nesting_would_waste_a_sheet(self):
        paths = [
            _Path("piece-a", [(0, 0), (20, 0), (20, 80), (0, 80)]),
            _Path("piece-b", [(22, 0), (42, 0), (42, 80), (22, 80)]),
        ]
        classified = classify_document_pieces(_Document(paths))
        pieces = classified.pieces

        def one_piece_page(piece):
            return piece_organizer.OrganizationResult(
                placements=[
                    piece_organizer.PiecePlacement(
                        piece_id=piece.piece_id,
                        instance=1,
                        dx=2.0 - piece.bounds[0],
                        dy=2.0 - piece.bounds[1],
                        rotation_degrees=0.0,
                        placed_bounds=(2.0, 2.0, 22.0, 82.0),
                        entity_ids=(piece.outer_id,),
                    )
                ],
                strategy="nesting incompleto",
                evaluated_layouts=1,
            )

        with mock.patch.object(
            piece_organizer,
            "_organize_single_sheet",
            side_effect=[
                one_piece_page(pieces[0]),
                one_piece_page(pieces[1]),
            ],
        ):
            result = organize_pieces(
                pieces,
                (0, 0, 100, 100),
                spacing=2,
                rotations={piece.piece_id: (0.0,) for piece in pieces},
            )

        self.assertEqual(result.strategy, "arranjo atual preservado")
        self.assertEqual(len(result.sheet_bounds), 1)
        self.assertEqual(len(result.placements), 2)

    def test_multi_sheet_search_does_not_strand_large_parts_after_small_parts(self):
        specifications = (
            ("large-a", 0, 60, 100),
            ("large-b", 70, 60, 100),
            ("small-0", 140, 40, 50),
            ("small-1", 190, 40, 50),
            ("small-2", 240, 40, 50),
            ("small-3", 290, 40, 50),
        )
        paths = [
            _Path(
                name,
                [(x_value, 0), (x_value + width, 0),
                 (x_value + width, height), (x_value, height)],
            )
            for name, x_value, width, height in specifications
        ]
        pieces = classify_document_pieces(_Document(paths)).pieces

        result = organize_pieces(
            pieces,
            (0, 0, 100, 100),
            spacing=0,
            rotations={piece.piece_id: (0.0,) for piece in pieces},
            search_mode="fast",
        )

        self.assertEqual(len(result.placements), 6)
        self.assertEqual(len(result.sheet_bounds), 2)
        for sheet_index in range(2):
            sheet_piece_ids = {
                placement.piece_id
                for placement in result.placements
                if placement.sheet_index == sheet_index
            }
            self.assertEqual(len(sheet_piece_ids), 3)
            self.assertEqual(
                sum("large" in piece_id for piece_id in sheet_piece_ids),
                1,
            )

    def test_table_lr4_free_rotation_compares_orthogonal_two_sheet_layout(self):
        """Regression for the 1834 x 2745 mm LR4 table SVG."""

        dimensions = (
            [(100, 2665)] * 7
            + [(100, 2094)] * 10
            + [(100, 2413)] * 2
            + [(52.7, 2413), (81.35, 2413)]
            + [(100, 645)] * 2
            + [(81.35, 645), (52.7, 645)]
            + [(100, 46.5)] * 2
            + [(25, 25)]
        )
        paths = []
        source_x = 0.0
        for index, (width, height) in enumerate(dimensions):
            paths.append(
                _Path(
                    "lr4-part-%02d" % index,
                    [
                        (source_x, 0),
                        (source_x + width, 0),
                        (source_x + width, height),
                        (source_x, height),
                    ],
                )
            )
            source_x += width + 20.0
        pieces = classify_document_pieces(_Document(paths)).pieces
        free_rotations = {
            piece.piece_id: tuple(range(0, 360, 15))
            for piece in pieces
        }

        # Isolate the automatic nesting decision from the separate candidate
        # that preserves deliberate source-space blocks.
        with mock.patch.object(
            piece_organizer,
            "_preserve_existing_layout_candidate",
            return_value=None,
        ):
            result = organize_pieces(
                pieces,
                (0, 0, 1834, 2745),
                spacing=3.175,
                rotations=free_rotations,
                search_mode="fast",
                search_budget=(4, 0, 1, 1),
            )

        self.assertEqual(len(result.placements), 28)
        self.assertEqual(len(result.sheet_bounds), 2)
        self.assertTrue(result.strategy.startswith("ângulos ortogonais"))
        self.assertTrue(
            all(
                abs(float(placement.rotation_degrees) % 90.0) <= 1.0e-7
                for placement in result.placements
            )
        )

    def test_overlapping_source_layout_still_uses_regular_nesting(self):
        paths = [
            _Path(
                "piece-%d" % index,
                [(0, 0), (20, 0), (20, 80), (0, 80)],
            )
            for index in range(3)
        ]
        classified = classify_document_pieces(_Document(paths))

        result = organize_pieces(
            classified.pieces,
            (0, 0, 120, 100),
            spacing=2,
            rotations={piece.piece_id: (0.0,) for piece in classified.pieces},
            search_mode="fast",
        )

        self.assertNotEqual(result.strategy, "arranjo atual preservado")
        self.assertEqual(len(result.placements), 3)
        self.assertEqual(len(result.sheet_bounds), 1)


if __name__ == "__main__":
    unittest.main()
