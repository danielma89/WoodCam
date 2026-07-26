import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from woodcam_editor.domain import (
    InMemoryCommandHistory,
    ReplaceTextOutlinesCommand,
    Vec2,
    VectorDocument,
)
from woodcam_editor.presentation.compat import QtWidgets
from woodcam_editor.presentation.text_vector import (
    TextVectorOptions,
    create_text_outlines,
    text_options_from_group,
)


class TextVectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_text_is_closed_portable_paths_grouped_at_requested_height(self):
        document = VectorDocument.create_default()
        result = create_text_outlines(
            TextVectorOptions("O\nA", family="Sans Serif", height_mm=25.0),
            layer_id=document.active_layer_id,
        )

        paths = result.entities[:-1]
        group = result.entities[-1]
        self.assertGreaterEqual(len(paths), 3)  # O outer/inner plus A outline
        self.assertTrue(all(path.closed for path in paths))
        self.assertEqual(group.child_ids, tuple(path.id for path in paths))
        self.assertAlmostEqual(result.bounds_height_mm, 25.0)

        history = InMemoryCommandHistory(document)
        from woodcam_editor.domain import AddEntitiesCommand

        history.execute(AddEntitiesCommand(result.entities))
        self.assertIn(result.group_id, document.entities_by_id)
        history.undo()
        self.assertFalse(document.entities_by_id)

    def test_blank_text_is_rejected(self):
        document = VectorDocument.create_default()
        with self.assertRaisesRegex(ValueError, "Digite algum texto"):
            create_text_outlines(
                TextVectorOptions("   "), layer_id=document.active_layer_id
            )

    def test_edit_replaces_outlines_but_keeps_group_and_origin_for_undo(self):
        document = VectorDocument.create_default()
        original = create_text_outlines(
            TextVectorOptions("O", height_mm=20.0),
            layer_id=document.active_layer_id,
            origin=Vec2(120.0, 80.0),
        )
        history = InMemoryCommandHistory(document)
        from woodcam_editor.domain import AddEntitiesCommand

        history.execute(AddEntitiesCommand(original.entities))
        original_group = document.get_entity(original.group_id)
        original_bounds = None
        for child_id in original_group.child_ids:
            current = document.get_entity(child_id).bounds()
            original_bounds = current if original_bounds is None else original_bounds.union(current)

        edited_options = TextVectorOptions("NOVA", family="Sans Serif", height_mm=35.0)
        replacement = create_text_outlines(
            edited_options,
            layer_id=document.active_layer_id,
            origin=Vec2(original_bounds.min_x, original_bounds.min_y),
            group_id=original.group_id,
        )
        history.execute(
            ReplaceTextOutlinesCommand(
                original.group_id, original_group.child_ids, replacement.entities
            )
        )
        updated_group = document.get_entity(original.group_id)
        self.assertEqual(updated_group.id, original.group_id)
        self.assertEqual(text_options_from_group(updated_group).text, "NOVA")
        self.assertFalse(set(original_group.child_ids).intersection(updated_group.child_ids))
        updated_bounds = None
        for child_id in updated_group.child_ids:
            current = document.get_entity(child_id).bounds()
            updated_bounds = current if updated_bounds is None else updated_bounds.union(current)
        self.assertAlmostEqual(updated_bounds.min_x, original_bounds.min_x)
        self.assertAlmostEqual(updated_bounds.min_y, original_bounds.min_y)

        history.undo()
        restored_group = document.get_entity(original.group_id)
        self.assertEqual(restored_group.child_ids, original_group.child_ids)
        self.assertEqual(text_options_from_group(restored_group).text, "O")


if __name__ == "__main__":
    unittest.main()
