import unittest

from woodcam_editor.application.piece_organizer import (
    classify_document_pieces, organize_pieces, organization_with_stationary_pieces,
    validate_organization_result_geometry,
)
from woodcam_editor.domain import VectorDocument, PathEntity, Vec2


class ActiveSheetOrganizationTest(unittest.TestCase):
    def test_overflow_reuses_empty_sheet_before_allocating_another(self):
        from woodcam_editor.application.piece_organizer import organize_pieces_in_sheet_pool
        result = organize_pieces_in_sheet_pool(
            self.pieces(), ((500, 0, 600, 100), (900, 0, 1000, 100)),
            new_sheet_bounds=(1100, 0, 1200, 100), spacing=4,
        )
        self.assertEqual(len(result.placements), 2)
        self.assertFalse(result.unplaced_piece_ids)
        self.assertEqual(result.sheet_bounds, ((500., 0., 600., 100.), (900., 0., 1000., 100.)))
        self.assertEqual({p.sheet_index for p in result.placements}, {0, 1})

    def test_overflow_allocates_sheet_beyond_existing_occupied_sheets(self):
        from woodcam_editor.application.piece_organizer import organize_pieces_in_sheet_pool
        result = organize_pieces_in_sheet_pool(
            self.pieces(), ((500, 0, 600, 100),),
            new_sheet_bounds=(1100, 0, 1200, 100), spacing=4,
        )
        self.assertEqual(len(result.placements), 2)
        self.assertEqual(result.sheet_bounds[-1], (1100., 0., 1200., 100.))

    def test_crossing_piece_belongs_to_largest_overlap_not_full_containment(self):
        from woodcam_editor.application.piece_organizer import organization_sheet_index
        sheets = ((0, 0, 200, 200), (250, 0, 450, 200))
        self.assertEqual(organization_sheet_index((140, 5, 270, 135), sheets), 0)
        self.assertEqual(organization_sheet_index((5, 140, 135, 270), sheets), 0)
        self.assertEqual(organization_sheet_index((350, 50, 390, 90), sheets), 1)

    def test_smaller_empty_sheet_is_skipped_without_losing_overflow(self):
        from woodcam_editor.application.piece_organizer import organize_pieces_in_sheet_pool
        result = organize_pieces_in_sheet_pool(
            self.pieces(), ((500, 0, 600, 100), (900, 0, 950, 50)),
            new_sheet_bounds=(1100, 0, 1200, 100), spacing=4,
        )
        self.assertEqual(len(result.placements), 2)
        self.assertEqual(result.sheet_bounds[-1], (1100., 0., 1200., 100.))
        self.assertNotIn((900., 0., 950., 50.), result.sheet_bounds)

    def test_oversized_piece_does_not_allocate_endless_empty_sheets(self):
        from woodcam_editor.application.piece_organizer import organize_pieces_in_sheet_pool
        result = organize_pieces_in_sheet_pool(
            self.pieces(), ((500, 0, 550, 50),),
            new_sheet_bounds=(1100, 0, 1150, 50), spacing=4,
        )
        self.assertFalse(result.placements)
        self.assertEqual(len(result.unplaced_piece_ids), 2)
        self.assertEqual(len(result.sheet_bounds), 1)

    def pieces(self):
        document = VectorDocument.create_default()
        for index, x in enumerate((540, 630)):
            entity = PathEntity.from_points(
                document.active_layer_id,
                tuple(Vec2(x + dx, y) for dx, y in ((0, 20), (80, 20), (80, 100), (0, 100))),
                id="part-%d" % index, closed=True,
            )
            document.entities_by_id[entity.id] = entity
        return classify_document_pieces(document).pieces

    def test_active_sheet_does_not_allocate_overflow_sheets(self):
        pieces = self.pieces()
        result = organize_pieces(pieces, (500, 0, 600, 100), spacing=4, single_sheet=True)
        self.assertEqual(result.sheet_bounds, ((500., 0., 600., 100.),))
        self.assertEqual(len(result.placements), 1)
        self.assertEqual(len(result.unplaced_piece_ids), 1)
        self.assertGreaterEqual(result.placements[0].placed_bounds[0], 500)

    def test_stationary_piece_participates_in_collision_validation(self):
        pieces = self.pieces()
        result = organize_pieces(pieces[1:], (540, 20, 740, 220), single_sheet=True)
        combined = organization_with_stationary_pieces(pieces, result)
        self.assertEqual(len(combined.placements), 2)
        self.assertEqual(len(result.placements), 1)
        self.assertTrue(validate_organization_result_geometry(pieces, combined))
