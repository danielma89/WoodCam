import unittest
from woodcam_editor.domain import DeleteEntitiesCommand, VectorDocument, WorkArea, PathEntity, Vec2
from woodcam_editor.domain.sheets import DeleteEmptySheetCommand
from woodcam_editor.application.sheet_management import resize_work_area_command

class SheetCommandsTests(unittest.TestCase):
    def document(self):
        doc = VectorDocument.create_default()
        doc.work_area = WorkArea(0, 0, 200, 200)
        doc.metadata['organization_sheet_bounds'] = [[0, 0, 200, 200], [250, 0, 450, 200], [500, 0, 700, 200]]
        return doc

    def test_delete_empty_sheet_preserves_vectors_positions_and_undo(self):
        doc = self.document()
        part = PathEntity.from_points(doc.active_layer_id, (Vec2(510,10), Vec2(550,10), Vec2(550,50), Vec2(510,50)), closed=True)
        doc.entities_by_id[part.id] = part
        command = DeleteEmptySheetCommand(1)
        command.apply(doc)
        self.assertEqual(doc.metadata['organization_sheet_bounds'], [[0,0,200,200], [500,0,700,200]])
        self.assertEqual(doc.get_entity(part.id), part)
        command.revert(doc)
        self.assertEqual(len(doc.metadata['organization_sheet_bounds']), 3)

    def test_occupied_sheet_is_not_deleted_even_if_geometry_crosses_border(self):
        doc = self.document()
        part = PathEntity.from_points(doc.active_layer_id, (Vec2(190,10), Vec2(260,10)))
        doc.entities_by_id[part.id] = part
        for index in (0, 1):
            with self.assertRaises(ValueError):
                DeleteEmptySheetCommand(index).apply(doc)
        self.assertEqual(len(doc.metadata['organization_sheet_bounds']), 3)

    def test_last_sheet_is_preserved(self):
        doc = self.document()
        doc.metadata['organization_sheet_bounds'] = [[0,0,200,200]]
        with self.assertRaises(ValueError):
            DeleteEmptySheetCommand(0).apply(doc)

    def test_resize_only_active_sheet_shifts_neighbour_rigidly_and_undoes(self):
        doc = self.document()
        part = PathEntity.from_points(doc.active_layer_id, (Vec2(510,10), Vec2(550,10), Vec2(550,50), Vec2(510,50)), closed=True)
        doc.entities_by_id[part.id] = part
        command = resize_work_area_command(doc, WorkArea(0,0,400,300), 1)
        command.apply(doc)
        self.assertEqual(doc.metadata['organization_sheet_bounds'], [[0,0,200,200], [250,0,650,300], [700,0,900,200]])
        self.assertEqual(doc.get_entity(part.id).bounds().width, 40)
        self.assertEqual(doc.get_entity(part.id).bounds().min_x, 710)
        command.revert(doc)
        self.assertEqual(doc.get_entity(part.id), part)
        self.assertEqual(doc.metadata['organization_sheet_bounds'][2], [500,0,700,200])
        command.apply(doc)
        self.assertEqual(doc.get_entity(part.id).bounds().min_x, 710)

    def test_shrink_does_not_move_or_scale_any_piece(self):
        doc = self.document()
        part = PathEntity.from_points(doc.active_layer_id, (Vec2(260,10), Vec2(440,10)))
        doc.entities_by_id[part.id] = part
        resize_work_area_command(doc, WorkArea(0,0,100,100), 1).apply(doc)
        self.assertEqual(doc.get_entity(part.id), part)
        self.assertEqual(doc.metadata['organization_sheet_bounds'][2], [500,0,700,200])

    def test_remnants_follow_shift_and_delete_renumbers_without_moving(self):
        doc = self.document()
        cut = dict(sheet_index=2, start=[500,150], end=[700,150],
                   remnant_bounds=[500,150,700,200], remnant_id='sheet-2-remnant-1')
        remnant = PathEntity.from_points(doc.active_layer_id, (Vec2(500,150),Vec2(700,150)),
            metadata=dict(woodcam_role='remnant_cut', sheet_index=2, cut_start=cut['start'],
                          cut_end=cut['end'], remnant_bounds=cut['remnant_bounds'], remnant_id=cut['remnant_id']))
        doc.entities_by_id[remnant.id] = remnant
        doc.metadata['organization_remnant_cuts'] = [cut]
        resize_work_area_command(doc, WorkArea(0,0,400,300), 1).apply(doc)
        shifted = doc.get_entity(remnant.id)
        self.assertEqual(shifted.metadata['cut_start'], [700,150])
        self.assertEqual(doc.metadata['organization_remnant_cuts'][0]['start'], [700,150])
        command = DeleteEmptySheetCommand(1)
        command.apply(doc)
        remaining = doc.get_entity(remnant.id)
        self.assertEqual(remaining.spans, shifted.spans)
        self.assertEqual(remaining.metadata['sheet_index'], 1)
        self.assertEqual(doc.metadata['organization_remnant_cuts'][0]['sheet_index'], 1)
        command.revert(doc)
        self.assertEqual(doc.get_entity(remnant.id), shifted)

    def test_delete_remnant_cleans_its_metadata_and_undo_restores_both(self):
        doc = self.document()
        remnant = PathEntity.from_points(
            doc.active_layer_id, (Vec2(0, 150), Vec2(200, 150)),
            metadata={"woodcam_role": "remnant_cut"},
        )
        doc.add_entities((remnant,))
        cut = {"entity_id": remnant.id, "start": [0, 150], "end": [200, 150]}
        doc.metadata["organization_remnant_cuts"] = [cut]
        command = DeleteEntitiesCommand((remnant.id,))
        change = command.apply(doc)
        self.assertTrue(change.work_area_changed)
        self.assertNotIn(remnant.id, doc.entities_by_id)
        self.assertEqual(doc.metadata["organization_remnant_cuts"], [])
        command.revert(doc)
        self.assertIn(remnant.id, doc.entities_by_id)
        self.assertEqual(doc.metadata["organization_remnant_cuts"], [cut])

if __name__ == '__main__':
    unittest.main()
